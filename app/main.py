from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .models import Project, ProjectUpdate, RollbackRequest, VerificationRequest, VerifyResponse
from .planner import build_occupancy, verify_passage
from .storage import (
    create_project,
    delete_project,
    get_project,
    list_projects,
    make_revision,
    open_project_image,
    rollback,
    save_project,
)

app = FastAPI(title="机器人地图编辑平台", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/projects", response_model=list[Project])
def projects():
    return list_projects()


@app.post("/api/projects", response_model=Project)
async def upload_project(
    map_file: UploadFile = File(...),
    name: str | None = Form(None),
    resolution: float = Form(0.05),
):
    if resolution <= 0:
        raise HTTPException(status_code=400, detail="分辨率必须大于 0")
    content = await map_file.read()
    if not content:
        raise HTTPException(status_code=400, detail="地图文件为空")
    return create_project(map_file.filename or "map.png", content, name, resolution)


@app.get("/api/projects/{project_id}", response_model=Project)
def project_detail(project_id: str):
    try:
        return get_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="工程不存在") from exc


@app.get("/api/projects/{project_id}/image")
def project_image(project_id: str):
    project = get_project(project_id)
    return FileResponse(f"data/{project.id}/original.png", media_type="image/png")


@app.get("/api/projects/{project_id}/source")
def project_source(project_id: str):
    project = get_project(project_id)
    return FileResponse(f"data/{project.id}/{project.normalized_image_path}", filename=project.original_filename)


@app.patch("/api/projects/{project_id}", response_model=Project)
def update_project(project_id: str, update: ProjectUpdate):
    project = get_project(project_id)
    if update.revision_label is not None:
        make_revision(project, update.revision_label)
    if update.origin is not None:
        project.origin = update.origin
    if update.forbidden_zones is not None:
        project.forbidden_zones = update.forbidden_zones
    save_project(project)
    return project


@app.post("/api/projects/{project_id}/revisions", response_model=Project)
def snapshot_project(project_id: str, update: ProjectUpdate):
    return update_project(project_id, update)


@app.post("/api/projects/{project_id}/rollback", response_model=Project)
def rollback_project(project_id: str, request: RollbackRequest):
    project = get_project(project_id)
    try:
        rollback(project, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="版本不存在") from exc
    save_project(project)
    return project


@app.delete("/api/projects/{project_id}")
def remove_project(project_id: str):
    delete_project(project_id)
    return {"ok": True}


@app.post("/api/projects/{project_id}/verify", response_model=VerifyResponse)
def verify_project(project_id: str, request: VerificationRequest):
    project = get_project(project_id)
    image = open_project_image(project)
    occupancy = build_occupancy(image, project.forbidden_zones, project.origin, project.resolution)
    result = verify_passage(
        occupancy,
        project.resolution,
        request.robot,
        request.start,
        request.goal,
        (project.origin.x, project.origin.y, project.origin.yaw),
        project.height,
        request.orientations,
    )
    if request.save:
        project.last_verification = result
        save_project(project)
    return VerifyResponse(project=project, result=result)
