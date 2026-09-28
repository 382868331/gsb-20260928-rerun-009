from __future__ import annotations

import io

import numpy as np
from PIL import Image

from app import storage
from app.main import get_project, project_detail, update_project, verify_project
from app.models import Origin, ProjectUpdate, Rect, VerificationRequest


def demo_bytes():
    pixels = np.full((121, 101), 255, dtype=np.uint8)
    wall_x = 80
    pixels[:45, wall_x] = 0
    pixels[65:, wall_x] = 0
    pixels[:, 0] = 0
    pixels[:, -1] = 0
    pixels[0, :] = 0
    pixels[-1, :] = 0
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return buffer.getvalue()


def test_project_routes_persist_verification_and_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_ROOT", tmp_path)
    project = storage.create_project("demo.png", demo_bytes(), "演示地图", 0.05)

    request = VerificationRequest.model_validate({
        "robot": {"width": 0.5, "length": 1.0},
        "start": {"x": 1.3, "y": 3.0},
        "goal": {"x": 4.3, "y": 3.0},
        "orientations": 24,
        "save": True,
    })
    response = verify_project(project.id, request)
    assert response.result.feasible is True

    reloaded = project_detail(project.id)
    assert reloaded.last_verification is not None
    assert reloaded.last_verification.feasible is True

    updated = update_project(
        project.id,
        ProjectUpdate(
            origin=Origin(x=-0.2, y=-0.1, yaw=0),
            forbidden_zones=[Rect(id="z1", x=0.4, y=0.3, width=0.5, height=0.4)],
            revision_label="原点与禁行区修订",
        ),
    )
    assert updated.revisions[0].label == "原点与禁行区修订"
    assert get_project(project.id).forbidden_zones[0].id == "z1"
    raw = demo_bytes()
    assert (tmp_path / project.id / "source.png").read_bytes() == raw
    assert np.asarray(Image.open(tmp_path / project.id / "original.png"))[..., 0].mean() > 100
