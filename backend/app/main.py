from __future__ import annotations

import json
import math
import os
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .geometry import validate_passage
from .maps import image_to_grid, make_sample_map
from .models import (
    PassageRequestModel,
    ProjectUpdateModel,
    RejudgeRequestModel,
    TimedPlanRequestModel,
    VersionCreateModel,
)
from .planner import map_revision, plan_timed, retime_path
from .storage import Store


DB_PATH = os.environ.get("MAP_EDITOR_DB", "data/map_editor.db")
app = FastAPI(title="Robot Map Editor", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
store = Store(DB_PATH)


def default_state(map_width: int, map_height: int, resolution: float) -> dict[str, Any]:
    return {
        "origin": {"x": 0.0, "y": 0.0},
        "forbidden_zones": [],
        "no_wait_zones": [],
        "start": {"x": resolution, "y": resolution},
        "end": {"x": resolution, "y": map_height * resolution - resolution},
        "start_orientation": 0.0,
        "end_orientation": 0.0,
        "robot_width": 0.5,
        "robot_length": 0.8,
        "linear_speed": 0.5,
        "angular_speed": 90.0,
        "checkpoints": [],
    }


def serialize_project(row) -> dict[str, Any]:
    map_data = json.loads(row["map_data"])
    return {
        "id": row["id"],
        "name": row["name"],
        "resolution": row["resolution"],
        "threshold": row["threshold"],
        "width": row["width"],
        "height": row["height"],
        "state": json.loads(row["state"]),
        "grid": map_data["grid"],
        "original_url": f"/api/projects/{row['id']}/original",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/projects")
async def projects() -> list[dict[str, Any]]:
    return [dict(row) for row in store.list_projects()]


@app.post("/api/projects")
async def create_project(
    name: str = Form(...),
    resolution: float = Form(0.1),
    threshold: int = Form(128),
    file: UploadFile | None = File(None),
) -> dict[str, Any]:
    if resolution <= 0:
        raise HTTPException(status_code=400, detail="resolution must be positive")
    if file is not None:
        data = await file.read()
        grid, normalized = image_to_grid(data, threshold)
        image_data = normalized["data"]
        filename = normalized["filename"]
        content_type = normalized["content_type"]
        width = normalized["width"]
        height = normalized["height"]
    else:
        image_data, grid = make_sample_map()
        filename = "sample-doors.png"
        content_type = "image/png"
        height = len(grid)
        width = len(grid[0])
    state = default_state(width, height, resolution)
    project_id = store.create_project(
        name,
        str(filename),
        str(content_type),
        resolution,
        threshold,
        image_data,
        width,
        height,
        grid,
        state,
    )
    return serialize_project(store.get_project(project_id))


@app.get("/api/projects/{project_id}")
async def get_project(project_id: int) -> dict[str, Any]:
    row = store.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return serialize_project(row)


@app.get("/api/projects/{project_id}/original")
async def get_original(project_id: int):
    row = store.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return Response(
        content=row["original_image"],
        media_type=row["content_type"],
        headers={"Cache-Control": "no-store"},
    )


@app.put("/api/projects/{project_id}")
async def update_project(project_id: int, payload: ProjectUpdateModel) -> dict[str, Any]:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    state = payload.state.model_dump()
    store.update_state(project_id, state)
    return serialize_project(store.get_project(project_id))


@app.get("/api/projects/{project_id}/versions")
async def versions(project_id: int) -> list[dict[str, Any]]:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return [dict(row) for row in store.list_versions(project_id)]


@app.post("/api/projects/{project_id}/versions")
async def create_version(project_id: int, payload: VersionCreateModel) -> dict[str, Any]:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    version_id = store.create_version(project_id, payload.name, payload.state.model_dump())
    row = store.get_version(version_id)
    return dict(row)


@app.post("/api/versions/{version_id}/restore")
async def restore_version(version_id: int) -> dict[str, Any]:
    state = store.restore_version(version_id)
    if state is None:
        raise HTTPException(status_code=404, detail="version not found")
    return {"state": state}


@app.post("/api/projects/{project_id}/validate")
async def validate_project(project_id: int, payload: PassageRequestModel) -> dict[str, Any]:
    row = store.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    grid = json.loads(row["map_data"])["grid"]
    state = payload.state.model_dump()
    if state["start"] is None or state["end"] is None:
        raise HTTPException(status_code=400, detail="start and end are required")
    try:
        result = validate_passage(
            grid,
            state["forbidden_zones"],
            row["resolution"],
            state["robot_width"],
            state["robot_length"],
            state["origin"],
            state["start"],
            state["end"],
            math.radians(state.get("start_orientation", 0.0)),
            math.radians(state.get("end_orientation", 0.0)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


def _run_timed_plan(row, state: dict[str, Any]) -> dict[str, Any]:
    grid = json.loads(row["map_data"])["grid"]
    if state["start"] is None or state["end"] is None:
        raise HTTPException(status_code=400, detail="start and end are required")
    try:
        return plan_timed(
            grid,
            state["forbidden_zones"],
            state.get("no_wait_zones", []),
            row["resolution"],
            state["robot_width"],
            state["robot_length"],
            state["origin"],
            state["start"],
            math.radians(state.get("start_orientation", 0.0)),
            [
                {**cp, "orientation": math.radians(cp["orientation"])}
                for cp in state.get("checkpoints", [])
            ],
            state["end"],
            math.radians(state.get("end_orientation", 0.0)),
            state.get("linear_speed", 0.5),
            math.radians(state.get("angular_speed", 90.0)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/timed-plan")
async def timed_plan(project_id: int, payload: TimedPlanRequestModel) -> dict[str, Any]:
    row = store.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    state = payload.state.model_dump()
    result = _run_timed_plan(row, state)
    record = {
        "state": state,
        "result": result,
        "map_revision": map_revision(json.loads(row["map_data"])["grid"], state),
        "map_updated_at": row["updated_at"],
    }
    plan_id = store.create_plan(
        project_id,
        payload.name or f"方案 {len(payload.state.checkpoints)} 检查点",
        "search",
        result["status"],
        record,
    )
    return {"plan_id": plan_id, **result}


@app.get("/api/projects/{project_id}/plans")
async def list_plans(project_id: int) -> list[dict[str, Any]]:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return [dict(row) for row in store.list_plans(project_id)]


@app.get("/api/plans/{plan_id}")
async def get_plan(plan_id: int) -> dict[str, Any]:
    row = store.get_plan(plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="plan not found")
    payload = json.loads(row["payload"])
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "name": row["name"],
        "kind": row["kind"],
        "status": row["status"],
        "created_at": row["created_at"],
        **payload,
    }


@app.post("/api/plans/{plan_id}/rejudge")
async def rejudge_plan(plan_id: int, payload: RejudgeRequestModel) -> dict[str, Any]:
    row = store.get_plan(plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="plan not found")
    project = store.get_project(row["project_id"])
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    record = json.loads(row["payload"])
    result = record["result"]
    if result.get("status") != "ok" or not result.get("path"):
        raise HTTPException(status_code=400, detail="只有成功的方案可以按原路线重判")
    state = record["state"]
    checkpoints = [cp.model_dump() for cp in payload.checkpoints]
    checkpoint_entries = [
        {"name": entry["name"], "path_index": entry["path_index"]}
        for entry in result.get("schedule", [])
        if entry.get("kind") == "checkpoint"
    ]
    if len(checkpoint_entries) != len(checkpoints):
        raise HTTPException(status_code=400, detail="检查点数量与原方案不一致")
    common = {
        "path": result["path"],
        "checkpoint_entries": checkpoint_entries,
        "checkpoints": [
            {**cp, "orientation": math.radians(cp["orientation"])} for cp in checkpoints
        ],
        "no_wait_zones": state.get("no_wait_zones", []),
        "map_rows": project["height"],
        "map_cols": project["width"],
        "resolution": project["resolution"],
        "origin": state["origin"],
        "linear_speed": state.get("linear_speed", 0.5),
        "angular_speed": math.radians(state.get("angular_speed", 90.0)),
    }
    kept = retime_path(**common, relocate_waits=False)
    adjusted = retime_path(**common, relocate_waits=True)
    new_state = {**state, "checkpoints": checkpoints}
    revision = map_revision(json.loads(project["map_data"])["grid"], new_state)
    kept_id = store.create_plan(
        row["project_id"],
        f"{row['name']} · 保留几何重判",
        "rejudge-kept",
        kept["status"],
        {
            "state": new_state,
            "result": {**kept, "path": result["path"]},
            "map_revision": revision,
            "map_updated_at": project["updated_at"],
        },
    )
    adjusted_id = store.create_plan(
        row["project_id"],
        f"{row['name']} · 调整等待位置",
        "rejudge-adjusted",
        adjusted["status"],
        {
            "state": new_state,
            "result": {**adjusted, "path": result["path"]},
            "map_revision": revision,
            "map_updated_at": project["updated_at"],
        },
    )
    return {
        "kept": {"plan_id": kept_id, **kept},
        "adjusted": {"plan_id": adjusted_id, **adjusted},
    }
