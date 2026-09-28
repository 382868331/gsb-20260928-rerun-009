from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image

from .models import Origin, Project, Rect, Revision, RollbackRequest

DATA_ROOT = Path(os.environ.get("MAP_EDITOR_DATA", Path.cwd() / "data"))


def ensure_storage() -> Path:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return DATA_ROOT


def _project_path(project_id: str) -> Path:
    return ensure_storage() / project_id / "project.json"


def list_projects() -> list[Project]:
    root = ensure_storage()
    projects = []
    for path in root.glob("*/project.json"):
        try:
            projects.append(get_project(path.parent.name))
        except Exception:
            continue
    return sorted(projects, key=lambda item: item.id, reverse=True)


def save_project(project: Project) -> None:
    path = _project_path(project.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(project.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def get_project(project_id: str) -> Project:
    return Project.model_validate_json(_project_path(project_id).read_text(encoding="utf-8"))


def create_project(filename: str, content: bytes, name: str | None, resolution: float) -> Project:
    project_id = uuid.uuid4().hex[:12]
    directory = ensure_storage() / project_id
    directory.mkdir(parents=True)
    suffix = Path(filename).suffix.lower() or ".bin"
    source_relative = f"source{suffix}"
    source_path = directory / source_relative
    source_path.write_bytes(content)
    normalized_path = directory / "original.png"
    with Image.open(source_path) as image:
        width, height = image.size
        normalized = image.convert("RGBA")
        normalized.save(normalized_path)
    safe_name = name or Path(filename).stem
    project = Project(
        id=project_id,
        name=safe_name,
        original_filename=filename,
        image_path=f"/api/projects/{project_id}/image",
        normalized_image_path=source_relative,
        width=width,
        height=height,
        resolution=resolution,
        origin=Origin(x=0, y=0, yaw=0),
    )
    save_project(project)
    return project


def open_project_image(project: Project) -> Image.Image:
    path = ensure_storage() / project.id / project.normalized_image_path
    with Image.open(path) as image:
        return image.convert("RGBA")


def make_revision(project: Project, label: str | None = None) -> Revision:
    next_id = (project.revisions[-1].id + 1) if project.revisions else 1
    revision = Revision(
        id=next_id,
        label=label or f"版本 {next_id}",
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        origin=project.origin.model_copy(deep=True),
        forbidden_zones=[zone.model_copy(deep=True) for zone in project.forbidden_zones],
    )
    project.revisions.append(revision)
    return revision


def rollback(project: Project, request: RollbackRequest) -> Revision:
    source = next((item for item in project.revisions if item.id == request.revision_id), None)
    if source is None:
        raise KeyError(f"revision {request.revision_id} not found")
    project.origin = source.origin.model_copy(deep=True)
    project.forbidden_zones = [zone.model_copy(deep=True) for zone in source.forbidden_zones]
    return make_revision(project, request.label)


def delete_project(project_id: str) -> None:
    shutil.rmtree(ensure_storage() / project_id, ignore_errors=True)
