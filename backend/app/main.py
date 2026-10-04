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
from .storage import Store, map_fingerprint


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
        "timed_zones": [],
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


def normalize_state(state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """补齐旧工程缺少的新增字段，返回 (完整状态, 实际补过的字段名)。

    旧版本工程没有检查点/限时封路等字段；这里只补显式默认值，
    不虚构检查点，也不把补齐后的状态标记为已验证。
    """

    defaults: dict[str, Any] = {
        "origin": {"x": 0.0, "y": 0.0},
        "forbidden_zones": [],
        "timed_zones": [],
        "waiting_zones": [],
        "checkpoints": [],
        "start": None,
        "end": None,
        "robot_width": 0.5,
        "robot_length": 0.8,
        "start_orientation": 0.0,
        "end_orientation": None,
        "linear_speed": 0.5,
        "angular_speed": 0.5,
        "start_time": 0.0,
    }
    applied = [key for key in defaults if key not in state]
    merged = {key: state.get(key, value) for key, value in defaults.items()}
    return merged, applied


def serialize_project(row) -> dict[str, Any]:
    map_data = json.loads(row["map_data"])
    state, applied = normalize_state(json.loads(row["state"]))
    return {
        "id": row["id"],
        "name": row["name"],
        "resolution": row["resolution"],
        "threshold": row["threshold"],
        "width": row["width"],
        "height": row["height"],
        "state": state,
        "state_defaults": applied,
        "grid": map_data["grid"],
        "original_url": f"/api/projects/{row['id']}/original",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# 结论必须逐字段完整保存；读取时也不允许把缺失字段补成新的通过/失败。
RESULT_REQUIRED_FIELDS = (
    "status",
    "passable",
    "reason",
    "path",
    "schedule",
    "segments",
    "search_completed",
    "algorithm",
)


def load_plan_record(row, current_row=None) -> dict[str, Any]:
    """读取保存方案：保留全部结论字段，并标注地图是否已变化。

    - result_complete=False 表示旧报告缺少判断字段，缺失字段保持缺失，
      不会被解释成新的通过或失败；
    - map_changed=True 仅在能取得保存时地图快照且与当前地图不同时给出，
      旧快照缺失时用 map_snapshot_missing 显式表达不确定性。
    """

    saved_result = json.loads(row["result"])
    state = json.loads(row["state"])
    missing_fields = [
        key for key in RESULT_REQUIRED_FIELDS if key not in saved_result
    ]
    record = {
        "id": row["id"],
        "created_at": row["created_at"],
        "result": saved_result,
        "state": state,
        "result_complete": not missing_fields,
        "missing_fields": missing_fields,
        "map_version": saved_result.get("map_version")
        or (row["map_version"] if "map_version" in row.keys() else None),
        "map_snapshot_missing": row["map_grid"] is None
        if "map_grid" in row.keys()
        else True,
    }
    saved_grid = (
        json.loads(row["map_grid"])
        if "map_grid" in row.keys() and row["map_grid"] is not None
        else None
    )
    saved_resolution = (
        row["map_resolution"] if "map_resolution" in row.keys() else None
    )
    current_fingerprint = None
    if current_row is not None:
        current_grid = json.loads(current_row["map_data"])["grid"]
        current_fingerprint = map_fingerprint(
            current_grid, current_row["resolution"], state
        )
        record["current_map_version"] = current_row["updated_at"]
    saved_fingerprint = (
        row["map_fingerprint"] if "map_fingerprint" in row.keys() else None
    ) or saved_result.get("map_fingerprint")
    record["map_fingerprint"] = saved_fingerprint
    record["current_map_fingerprint"] = current_fingerprint
    if saved_grid is None:
        record["map_changed"] = False
    elif current_row is None:
        record["map_changed"] = False
    else:
        current_grid = json.loads(current_row["map_data"])["grid"]
        record["map_changed"] = (
            saved_grid != current_grid
            or float(saved_resolution) != float(current_row["resolution"])
            or (
                saved_fingerprint is not None
                and current_fingerprint is not None
                and saved_fingerprint != current_fingerprint
            )
        )
    return record


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
    merged, applied = normalize_state(state)
    return {"state": merged, "state_defaults": applied}


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
            timed_zones=state.get("timed_zones", []),
            linear_speed=state["linear_speed"],
            angular_speed=state["angular_speed"],
            start_time=state["start_time"],
            keep_geometry=payload.keep_geometry,
            geometric_path=payload.geometric_path,
            search_budget=payload.search_budget,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    fingerprint = map_fingerprint(grid, row["resolution"], state)
    result["map_version"] = row["updated_at"]
    result["map_fingerprint"] = fingerprint
    result["robot_params"] = {
        "robot_width": state["robot_width"],
        "robot_length": state["robot_length"],
        "linear_speed": state["linear_speed"],
        "angular_speed": state["angular_speed"],
        "start_orientation": state["start_orientation"],
        "end_orientation": state["end_orientation"],
    }
    result["state_snapshot"] = state
    result["search_budget"] = payload.search_budget
    store.save_plan(
        project_id,
        result,
        state,
        map_grid=grid,
        map_resolution=row["resolution"],
        map_version=row["updated_at"],
        map_checksum=fingerprint,
    )
    return result


@app.post("/api/plans/{plan_id}/replay")
async def replay_plan(plan_id: int) -> dict[str, Any]:
    """重开保存方案后，用保存时的地图快照/状态逐段重放，结论必须一致。

    地图后来变化时只明确标识旧报告，绝不把缺字段或新结果冒充原结论。
    """

    record = store.get_plan(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail="plan not found")
    saved_result = json.loads(record["result"])
    state = json.loads(record["state"])
    project_id = record["project_id"]
    row = store.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")

    keys = record.keys()
    saved_grid = json.loads(record["map_grid"]) if "map_grid" in keys and record["map_grid"] else None
    saved_resolution = record["map_resolution"] if "map_resolution" in keys else None
    current_grid = json.loads(row["map_data"])["grid"]
    grid = saved_grid if saved_grid is not None else current_grid
    resolution = float(saved_resolution) if saved_resolution is not None else row["resolution"]
    map_snapshot_missing = saved_grid is None
    current_fingerprint = map_fingerprint(current_grid, row["resolution"], state)
    saved_fingerprint = (
        record["map_fingerprint"] if "map_fingerprint" in keys else None
    ) or saved_result.get("map_fingerprint")
    map_changed = (
        not map_snapshot_missing
        and (
            saved_grid != current_grid
            or resolution != float(row["resolution"])
            or (
                saved_fingerprint is not None
                and saved_fingerprint != current_fingerprint
            )
        )
    )
    missing_fields = [
        key
        for key in (
            "status",
            "passable",
            "reason",
            "path",
            "schedule",
            "segments",
            "search_completed",
            "algorithm",
        )
        if key not in saved_result
    ]
    try:
        fresh = validate_passage(
            grid,
            state["forbidden_zones"],
            resolution,
            state["robot_width"],
            state["robot_length"],
            state["origin"],
            state["start"],
            state["end"],
            start_orientation=state["start_orientation"],
            end_orientation=state["end_orientation"],
            checkpoints=state["checkpoints"],
            waiting_zones=state["waiting_zones"],
            timed_zones=state.get("timed_zones", []),
            linear_speed=state["linear_speed"],
            angular_speed=state["angular_speed"],
            start_time=state["start_time"],
            keep_geometry=bool(saved_result.get("stage") == "retain_geometry"),
            geometric_path=saved_result.get("path"),
            search_budget=int(saved_result.get("search_budget", 1_500_000)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    replay_ok = bool(fresh.get("replay", {}).get("ok", True))
    consistent = (
        not missing_fields
        and fresh.get("status") == saved_result.get("status")
        and fresh.get("passable") == saved_result.get("passable")
        and fresh.get("search_completed") == saved_result.get("search_completed")
        and replay_ok
    )
    return {
        "consistent": consistent,
        "saved_status": saved_result.get("status"),
        "fresh_status": fresh.get("status"),
        "saved_passable": saved_result.get("passable"),
        "fresh_passable": fresh.get("passable"),
        "saved_reason": saved_result.get("reason"),
        "fresh_reason": fresh.get("reason"),
        "saved_search_completed": saved_result.get("search_completed"),
        "fresh_search_completed": fresh.get("search_completed"),
        "saved_segments": saved_result.get("segments", []),
        "fresh_segments": fresh.get("segments", []),
        "saved_path": saved_result.get("path"),
        "fresh_path": fresh.get("path"),
        "saved_schedule": saved_result.get("schedule", []),
        "fresh_schedule": fresh.get("schedule", []),
        "replay": fresh.get("replay", {}),
        "algorithm": fresh.get("algorithm"),
        "saved_algorithm": saved_result.get("algorithm"),
        "result_complete": not missing_fields,
        "missing_fields": missing_fields,
        "map_changed": map_changed,
        "map_snapshot_missing": map_snapshot_missing,
        "saved_map_version": saved_result.get("map_version")
        or (record["map_version"] if "map_version" in record.keys() else None),
        "current_map_version": row["updated_at"],
        "saved_map_fingerprint": saved_fingerprint,
        "current_map_fingerprint": current_fingerprint,
        "robot_params": {
            "robot_width": state["robot_width"],
            "robot_length": state["robot_length"],
            "linear_speed": state["linear_speed"],
            "angular_speed": state["angular_speed"],
        },
    }


@app.get("/api/projects/{project_id}/plans")
async def list_plans(project_id: int) -> list[dict[str, Any]]:
    row = store.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return [load_plan_record(plan_row, row) for plan_row in store.list_plans(project_id)]


@app.get("/api/plans/{plan_id}")
async def get_plan(plan_id: int) -> dict[str, Any]:
    record = store.get_plan(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return load_plan_record(record, store.get_project(record["project_id"]))
