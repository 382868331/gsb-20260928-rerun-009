from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .geometry import validate_passage
from .maps import image_to_grid, make_sample_map
from .models import PassageRequestModel, ProjectUpdateModel, VersionCreateModel
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
        "closure_zones": [],
        "waiting_zones": [],
        "checkpoints": [],
        "start": {"x": resolution, "y": resolution},
        "end": {"x": resolution, "y": map_height * resolution - resolution},
        "robot_width": 0.5,
        "robot_length": 0.8,
        "start_orientation": 0.0,
        "end_orientation": None,
        "linear_speed": 0.5,
        "angular_speed": 0.5,
        "start_time": 0.0,
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
    for checkpoint in state["checkpoints"]:
        if checkpoint["window_end"] < checkpoint["window_start"]:
            raise HTTPException(status_code=400, detail="checkpoint window end precedes start")
    for zone in state["closure_zones"]:
        if zone["close_end"] <= zone["close_start"]:
            raise HTTPException(status_code=400, detail="closure window end must be after its start")
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
            start_orientation=state["start_orientation"],
            end_orientation=state["end_orientation"],
            checkpoints=state["checkpoints"],
            waiting_zones=state["waiting_zones"],
            closure_zones=state["closure_zones"],
            linear_speed=state["linear_speed"],
            angular_speed=state["angular_speed"],
            start_time=state["start_time"],
            keep_geometry=payload.keep_geometry,
            geometric_path=payload.geometric_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["map_version"] = row["updated_at"]
    result["state_snapshot"] = state
    store.save_plan(
        project_id,
        {
            "status": result.get("status"),
            "schedule": result.get("schedule", []),
            "segments": result.get("segments", []),
            "plans": result.get("plans", []),
            "algorithm": result.get("algorithm"),
            "map_updated_at": row["updated_at"],
        },
        state,
    )
    return result


@app.get("/api/projects/{project_id}/plans")
async def list_plans(project_id: int) -> list[dict[str, Any]]:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return [dict(row) for row in store.list_plans(project_id)]
