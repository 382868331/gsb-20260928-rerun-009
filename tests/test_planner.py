from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from app.models import Point, Rect, Robot
from app.planner import build_occupancy, verify_passage
from app import storage


def make_map(gap_bottom: float, gap_top: float, scale: int = 20) -> Image.Image:
    width = 101
    height = 121
    pixels = np.full((height, width), 255, dtype=np.uint8)
    gate_px_bottom = int(round(gap_bottom * scale))
    gate_px_top = int(round(gap_top * scale))
    wall_x = int(4 * scale)
    pixels[: height - gate_px_top, wall_x] = 0
    pixels[height - gate_px_bottom:, wall_x] = 0
    pixels[:, 0] = 0
    pixels[:, -1] = 0
    pixels[0, :] = 0
    pixels[-1, :] = 0
    return Image.fromarray(pixels, mode="L")


def verify_gap(bottom: float, top: float):
    image = make_map(bottom, top)
    origin = (0.0, 0.0, 0.0)
    occupancy = build_occupancy(image, [], origin, 0.05)
    return verify_passage(
        occupancy,
        0.05,
        Robot(width=0.5, length=1.0),
        Point(x=1.3, y=(bottom + top) / 2),
        Point(x=4.3, y=(bottom + top) / 2),
        origin,
        121,
        orientations=24,
    )


def test_wide_gate_allows_full_robot():
    result = verify_gap(2.5, 3.5)
    assert result.feasible is True
    assert len(result.path) >= 2


def test_narrow_gate_rejects_full_robot_even_if_center_is_free():
    result = verify_gap(2.8, 3.2)
    assert result.feasible is False
    assert "足迹" in result.reason or "通道" in result.reason


def test_edge_obstacle_collision_uses_robot_footprint_not_only_center():
    image = make_map(2.5, 3.5)
    occupancy = build_occupancy(image, [], (0, 0, 0), 0.05)
    result = verify_passage(
        occupancy,
        0.05,
        Robot(width=0.5, length=1.0),
        Point(x=0.28, y=0.25),
        Point(x=4.3, y=0.25),
        (0, 0, 0),
        121,
    )
    assert bool(occupancy[120, 5]) is True
    assert bool(occupancy[120, 6]) is True
    assert result.feasible is False
    assert "起点" in result.reason


def test_verification_and_revisions_remain_stable_after_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_ROOT", tmp_path)
    image = make_map(2.5, 3.5)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    project = storage.create_project("map.png", buffer.getvalue(), "测试地图", 0.05)
    project.forbidden_zones = [Rect(id="zone-1", x=0.5, y=0.2, width=0.6, height=0.3)]
    storage.make_revision(project, "添加贴边禁行区")
    storage.save_project(project)
    assert (tmp_path / project.id / "original.png").exists()

    image = storage.open_project_image(project)
    first = verify_passage(
        build_occupancy(image, project.forbidden_zones, project.origin, project.resolution),
        project.resolution,
        Robot(width=0.5, length=1.0),
        Point(x=1.2, y=3.0),
        Point(x=4.3, y=3.0),
        (0, 0, 0),
        project.height,
        24,
    )
    reloaded = storage.get_project(project.id)
    image_reloaded = storage.open_project_image(reloaded)
    second = verify_passage(
        build_occupancy(image_reloaded, reloaded.forbidden_zones, reloaded.origin, reloaded.resolution),
        reloaded.resolution,
        Robot(width=0.5, length=1.0),
        Point(x=1.2, y=3.0),
        Point(x=4.3, y=3.0),
        (0, 0, 0),
        reloaded.height,
        24,
    )
    assert first.feasible == second.feasible
    assert [[pose.x, pose.y, pose.yaw] for pose in first.path] == [[pose.x, pose.y, pose.yaw] for pose in second.path]
    assert reloaded.revisions[0].forbidden_zones[0].id == "zone-1"
