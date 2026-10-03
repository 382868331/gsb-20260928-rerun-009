from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any

import numpy as np


ALGORITHM_VERSION = "rectangle-sweep-timed-zones-v4"

# 10 deg per configuration layer. Each layer conservatively covers the whole
# angular interval around its center, so changing between adjacent layers can
# never skip an intermediate heading.
ORIENTATION_STEPS = 36
ANGLE_SUBDIVISIONS = 5
DEFAULT_SEARCH_BUDGET = 1_500_000


@dataclass(frozen=True)
class Point:
    x: float
    y: float


def world_to_grid(point: Point, resolution: float, height_cells: int, origin: Point) -> tuple[int, int]:
    col = int(round((point.x - origin.x) / resolution))
    row = height_cells - 1 - int(round((point.y - origin.y) / resolution))
    return row, col


def grid_to_world(row: int, col: int, resolution: float, height_cells: int, origin: Point) -> Point:
    return Point(origin.x + col * resolution, origin.y + (height_cells - 1 - row) * resolution)


def _footprint_mask(
    theta_start: float,
    theta_end: float,
    robot_width: float,
    robot_length: float,
    resolution: float,
) -> np.ndarray:
    """Conservative raster footprint for every heading in an angle interval.

    Cell centers within half a cell of the exact swept rectangle union are
    included, so this is the mask for a single pose / in-place rotation.
    Translation sweeps are handled separately by directional dilation.
    """

    corner_radius = 0.5 * math.hypot(robot_width, robot_length)
    patch_radius = int(math.ceil(corner_radius / resolution)) + 1
    offsets = np.arange(-patch_radius, patch_radius + 1)
    local_x, local_y = np.meshgrid(offsets * resolution, offsets * resolution)
    hit = np.zeros_like(local_x, dtype=bool)

    # Inflation between sampled headings makes the angular sweep conservative
    # instead of trusting a finite set of displayed poses.
    interval = abs(theta_end - theta_start)
    # Slack covers rotation between the sampled headings. A single-heading mask
    # must stay exactly the rectangle size; otherwise straight door passages
    # are rejected by phantom angular inflation.
    sample_step = interval / max(1, ANGLE_SUBDIVISIONS - 1)
    angular_slack = (
        corner_radius * math.sin(max(0.0, interval - sample_step) / 2.0)
        if interval > 0.0
        else 0.0
    )
    half_length = robot_length / 2.0 + angular_slack
    half_width = robot_width / 2.0 + angular_slack

    for sample in range(ANGLE_SUBDIVISIONS):
        theta = theta_start + sample_step * sample
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        along = local_x * sin_theta + local_y * cos_theta
        across = local_x * cos_theta - local_y * sin_theta
        # A cell whose center is exactly half a cell beyond the rectangle edge
        # only touches it on a zero-area boundary; exclude that grazing case so
        # an exactly-sized robot can use an exactly-sized aligned opening.
        cell_slack = resolution * 0.5 - 1e-9
        hit |= (np.abs(along) <= half_length + cell_slack) & (
            np.abs(across) <= half_width + cell_slack
        )

    return hit


def _free_centers_for_mask(occupancy: np.ndarray, mask: np.ndarray) -> np.ndarray:
    rows, cols = occupancy.shape
    blocked = np.zeros_like(occupancy, dtype=np.int32)
    center = mask.shape[0] // 2
    for mask_row, mask_col in np.argwhere(mask):
        delta_row = mask_row - center
        delta_col = mask_col - center
        source_row_min = max(0, delta_row)
        source_row_max = min(rows, rows + delta_row)
        source_col_min = max(0, delta_col)
        source_col_max = min(cols, cols + delta_col)
        if source_row_min >= source_row_max or source_col_min >= source_col_max:
            continue
        blocked[
            source_row_min - delta_row:source_row_max - delta_row,
            source_col_min - delta_col:source_col_max - delta_col,
        ] += occupancy[
            source_row_min:source_row_max,
            source_col_min:source_col_max,
        ].astype(np.int32)
    return blocked == 0


def build_orientation_layers(
    occupancy: np.ndarray,
    robot_width: float,
    robot_length: float,
    resolution: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build per-pose free arrays plus an in-place rotation sweep array.

    ``free[layer]`` marks centers where the rectangle at the layer heading
    alone is collision free. ``sweep[layer]`` additionally covers the whole
    angular interval between adjacent layers, which is exactly what an
    in-place 10 deg turn sweeps through.
    """
    angle_step = 2.0 * math.pi / ORIENTATION_STEPS
    free = np.zeros((ORIENTATION_STEPS, *occupancy.shape), dtype=bool)
    sweep = np.zeros((ORIENTATION_STEPS, *occupancy.shape), dtype=bool)
    orientations = np.arange(ORIENTATION_STEPS, dtype=float) * angle_step
    for layer, center_angle in enumerate(orientations):
        pose_mask = _footprint_mask(
            float(center_angle),
            float(center_angle),
            robot_width,
            robot_length,
            resolution,
        )
        sweep_mask = _footprint_mask(
            float(center_angle - angle_step / 2.0),
            float(center_angle + angle_step / 2.0),
            robot_width,
            robot_length,
            resolution,
        )
        free[layer] = _free_centers_for_mask(occupancy, pose_mask)
        sweep[layer] = _free_centers_for_mask(occupancy, sweep_mask)
    return free, sweep, orientations


def _move_free(free: np.ndarray, direction: tuple[int, int]) -> np.ndarray:
    """Centers from which a one-cell translation in ``direction`` is safe.

    A translation is the sweep of the pose over both endpoint cells, so the
    source center must also be valid when shifted by the movement vector.
    """
    dr, dc = direction
    # Source ranges that keep the destination inside the grid.
    src_r_min = max(0, -dr)
    src_r_max = min(free.shape[1], free.shape[1] - dr)
    src_c_min = max(0, -dc)
    src_c_max = min(free.shape[2], free.shape[2] - dc)
    dst_r_min, dst_r_max = src_r_min + dr, src_r_max + dr
    dst_c_min, dst_c_max = src_c_min + dc, src_c_max + dc
    shifted = np.zeros_like(free)
    shifted[:, dst_r_min:dst_r_max, dst_c_min:dst_c_max] = free[
        :, src_r_min:src_r_max, src_c_min:src_c_max
    ]
    return free & shifted


def normalize_orientation(orientation: float) -> float:
    wrapped = float(orientation) % (2.0 * math.pi)
    if wrapped < 0:
        wrapped += 2.0 * math.pi
    return wrapped


def nearest_orientation_layer(orientation: float) -> int:
    layer = round(normalize_orientation(orientation) / (2.0 * math.pi) * ORIENTATION_STEPS)
    return int(layer) % ORIENTATION_STEPS


def build_occupancy(
    base_grid: list[list[bool]],
    forbidden_zones: list[dict[str, float]],
    resolution: float,
    origin: Point,
) -> np.ndarray:
    occupancy = np.array(base_grid, dtype=bool)
    rows, cols = occupancy.shape
    for zone in forbidden_zones:
        zx = float(zone["x"]) - origin.x
        zy = float(zone["y"]) - origin.y
        x1 = min(zx, zx + float(zone["width"]))
        x2 = max(zx, zx + float(zone["width"]))
        y1 = min(zy, zy + float(zone["height"]))
        y2 = max(zy, zy + float(zone["height"]))
        x1 = max(0.0, x1)
        x2 = min(cols * resolution, x2)
        y1 = max(0.0, y1)
        y2 = min(rows * resolution, y2)
        min_col = int(np.floor(x1 / resolution))
        max_col = int(np.ceil(x2 / resolution))
        min_row = int(np.floor((rows * resolution - y2) / resolution))
        max_row = int(np.ceil((rows * resolution - y1) / resolution))
        if max_row > min_row and max_col > min_col:
            occupancy[min_row:max_row, min_col:max_col] = True
    return occupancy


EPS = 1e-9


def default_position_tolerance(resolution: float) -> float:
    """保存与核对共用的缺省位置容差含义。"""

    return max(resolution, 1.5 * resolution)


def validate_passage(
    base_grid: list[list[bool]],
    forbidden_zones: list[dict[str, float]],
    resolution: float,
    robot_width: float,
    robot_length: float,
    origin: dict[str, float],
    start: dict[str, float],
    end: dict[str, float],
    start_orientation: float = 0.0,
    end_orientation: float | None = None,
    checkpoints: list[dict[str, Any]] | None = None,
    waiting_zones: list[dict[str, float]] | None = None,
    timed_zones: list[dict[str, Any]] | None = None,
    linear_speed: float = 0.5,
    angular_speed: float = 0.5,
    start_time: float = 0.0,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
    keep_geometry: bool = False,
    geometric_path: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if robot_width <= 0 or robot_length <= 0:
        raise ValueError("robot dimensions must be positive")
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    if linear_speed <= 0 or angular_speed <= 0:
        raise ValueError("robot speeds must be positive")

    map_origin = Point(origin["x"], origin["y"])
    occupancy = build_occupancy(base_grid, forbidden_zones, resolution, map_origin)
    rows, cols = occupancy.shape
    start_row, start_col = world_to_grid(
        Point(start["x"], start["y"]), resolution, rows, map_origin
    )
    end_row, end_col = world_to_grid(
        Point(end["x"], end["y"]), resolution, rows, map_origin
    )
    if not (0 <= start_row < rows and 0 <= start_col < cols):
        raise ValueError("start is outside the map")
    if not (0 <= end_row < rows and 0 <= end_col < cols):
        raise ValueError("end is outside the map")

    free, sweep, orientations = build_orientation_layers(
        occupancy, robot_width, robot_length, resolution
    )
    angular_resolution = 2.0 * math.pi / ORIENTATION_STEPS
    start_layer = nearest_orientation_layer(start_orientation)
    end_layer = nearest_orientation_layer(
        start_orientation if end_orientation is None else end_orientation
    )

    blocked_result = {
        "passable": False,
        "algorithm": ALGORITHM_VERSION,
        "path": [],
        "segments": [],
        "angular_resolution": angular_resolution,
        "search_completed": True,
    }
    if not free[start_layer, start_row, start_col]:
        return {
            **blocked_result,
            "status": "blocked",
            "reason": "start pose collides with an obstacle for the complete rectangle footprint",
        }
    if not free[end_layer, end_row, end_col]:
        return {
            **blocked_result,
            "status": "blocked",
            "reason": "end pose collides with an obstacle for the complete rectangle footprint",
        }

    can_up = _move_free(free, (-1, 0))
    can_down = _move_free(free, (1, 0))
    can_left = _move_free(free, (0, -1))
    can_right = _move_free(free, (0, 1))
    can_translate = (can_up, can_down, can_left, can_right)

    geometric = _geometric_bfs(
        free,
        sweep,
        can_translate,
        start_layer,
        start_row,
        start_col,
        end_layer,
        end_row,
        end_col,
        orientations,
        resolution,
        rows,
        map_origin,
    )
    if geometric is None:
        return {
            **blocked_result,
            "status": "blocked",
            "reason": "no collision-free trajectory exists for the complete rectangle footprint, including the in-place turning sweep",
        }

    ordered = sorted(checkpoints or [], key=lambda item: int(item["order"]))
    checkpoint_specs = _build_checkpoint_specs(
        ordered, resolution, rows, cols, map_origin, angular_resolution
    )
    wait_cells = _zone_cells(waiting_zones or [], resolution, rows, cols, map_origin)
    timed = _build_timed_zones(
        timed_zones or [], occupancy, robot_width, robot_length, resolution, map_origin
    )

    common = {
        "algorithm": ALGORITHM_VERSION,
        "path": geometric["path"],
        "angular_resolution": angular_resolution,
    }
    if not checkpoint_specs and not timed:
        segments = _segments_for_path(
            geometric["path"],
            start_time,
            resolution / linear_speed,
            angular_resolution / angular_speed,
            checkpoint_specs,
            timed,
        )
        return {
            **common,
            "passable": True,
            "status": "feasible",
            "reason": geometric["reason"],
            "schedule": [],
            "segments": segments,
            "search_completed": True,
        }

    search_args = dict(
        free=free,
        sweep=sweep,
        can_translate=can_translate,
        specs=checkpoint_specs,
        wait_cells=wait_cells,
        timed=timed,
        start_layer=start_layer,
        start_row=start_row,
        start_col=start_col,
        end_layer=end_layer,
        end_row=end_row,
        end_col=end_col,
        orientations=orientations,
        resolution=resolution,
        rows=rows,
        map_origin=map_origin,
        linear_speed=linear_speed,
        angular_speed=angular_speed,
        start_time=start_time,
        search_budget=search_budget,
        geometric_path=geometric["path"],
    )

    retained: dict[str, Any] | None = None
    if keep_geometry:
        frozen_nodes = _freeze_path(
            geometric_path or geometric["path"], resolution, rows, map_origin
        )
        retained = _timed_search(**search_args, frozen_nodes=frozen_nodes)
        retained_result = _package_plan(
            retained,
            common,
            checkpoint_specs,
            timed,
            resolution,
            linear_speed,
            angular_speed,
            stage="retain_geometry",
        )
        if retained_result["status"] == "feasible":
            retained_result["retain_basis"] = _retain_basis(timed, checkpoint_specs)
            return retained_result

    result = _timed_search(**search_args, frozen_nodes=None)
    packaged = _package_plan(
        result,
        common,
        checkpoint_specs,
        timed,
        resolution,
        linear_speed,
        angular_speed,
        stage="reroute",
    )
    if retained is not None:
        packaged["retain_geometry"] = _summarize_stage(retained, timed, checkpoint_specs)
    return packaged


def _retain_basis(timed, specs) -> dict[str, Any]:
    return {
        "timed_zones": [
            {
                "id": zone["id"],
                "close_start": zone["close_start"],
                "close_end": zone["close_end"],
            }
            for zone in timed
        ],
        "checkpoint_windows": [
            {
                "order": spec["order"],
                "window_start": spec["window_start"],
                "window_end": spec["window_end"],
            }
            for spec in specs
        ],
    }


def _build_checkpoint_specs(
    ordered,
    resolution,
    rows,
    cols,
    map_origin,
    angular_resolution,
):
    specs = []
    for item in ordered:
        point = Point(float(item["x"]), float(item["y"]))
        cp_row, cp_col = world_to_grid(point, resolution, rows, map_origin)
        if not (0 <= cp_row < rows and 0 <= cp_col < cols):
            raise ValueError("checkpoint is outside the map")
        raw_tolerance = item.get("tolerance")
        tolerance = (
            float(raw_tolerance)
            if raw_tolerance is not None
            else default_position_tolerance(resolution)
        )
        if tolerance < 0:
            raise ValueError("checkpoint tolerance must be non-negative")
        tolerance_cells = max(0, int(round(tolerance / resolution)))
        wanted_layer = nearest_orientation_layer(float(item.get("orientation", 0.0)))
        raw_orient = item.get("orientation_tolerance")
        orient_tolerance = (
            float(raw_orient)
            if raw_orient is not None
            else angular_resolution / 2.0
        )
        orient_steps = max(0, int(math.floor(orient_tolerance / angular_resolution + EPS)))
        layers = {
            (wanted_layer + delta) % ORIENTATION_STEPS
            for delta in range(-orient_steps, orient_steps + 1)
        }
        window_start = float(item.get("window_start", 0.0))
        window_end = float(item.get("window_end", window_start))
        if window_end < window_start:
            raise ValueError("checkpoint window end precedes start")
        specs.append(
            {
                "order": int(item["order"]),
                "row": cp_row,
                "col": cp_col,
                "tolerance_cells": tolerance_cells,
                "tolerance": tolerance,
                "layers": layers,
                "window_start": window_start,
                "window_end": window_end,
            }
        )
    return specs


def _freeze_path(path, resolution, rows, map_origin):
    nodes = set()
    for node in path:
        row, col = world_to_grid(
            Point(float(node["x"]), float(node["y"])), resolution, rows, map_origin
        )
        nodes.add((nearest_orientation_layer(float(node["orientation"])), row, col))
    return nodes


def _build_timed_zones(
    zones, occupancy, robot_width, robot_length, resolution, map_origin
):
    built = []
    rows, cols = occupancy.shape
    angle_step = 2.0 * math.pi / ORIENTATION_STEPS
    for zone in zones:
        close_start = float(zone["close_start"])
        close_end = float(zone["close_end"])
        if close_end < close_start:
            raise ValueError("timed zone close end precedes start")
        zone_mask = np.zeros_like(occupancy)
        cells = _zone_cells([zone], resolution, rows, cols, map_origin)
        for row, col in cells:
            zone_mask[row, col] = True
        pose_blocked = np.zeros((ORIENTATION_STEPS, rows, cols), dtype=bool)
        sweep_blocked = np.zeros((ORIENTATION_STEPS, rows, cols), dtype=bool)
        for layer in range(ORIENTATION_STEPS):
            center_angle = layer * angle_step
            pose_mask = _footprint_mask(
                center_angle, center_angle, robot_width, robot_length, resolution
            )
            sweep_mask = _footprint_mask(
                center_angle - angle_step / 2.0,
                center_angle + angle_step / 2.0,
                robot_width,
                robot_length,
                resolution,
            )
            pose_blocked[layer] = ~_free_centers_for_mask(zone_mask, pose_mask)
            sweep_blocked[layer] = ~_free_centers_for_mask(zone_mask, sweep_mask)
        built.append(
            {
                "id": str(zone.get("id", f"timed-{len(built)}")),
                "close_start": close_start,
                "close_end": close_end,
                "pose_blocked": pose_blocked,
                "sweep_blocked": sweep_blocked,
                "cells": cells,
            }
        )
    return built


def _zone_cells(zones, resolution, rows, cols, origin):
    cells = set()
    for zone in zones:
        zx = float(zone["x"]) - origin.x
        zy = float(zone["y"]) - origin.y
        x1 = max(0.0, min(zx, zx + float(zone["width"])))
        x2 = min(cols * resolution, max(zx, zx + float(zone["width"])))
        y1 = max(0.0, min(zy, zy + float(zone["height"])))
        y2 = min(rows * resolution, max(zy, zy + float(zone["height"])))
        if x2 <= x1 or y2 <= y1:
            continue
        min_col = int(math.floor(x1 / resolution))
        max_col = int(math.ceil(x2 / resolution))
        min_row = int(math.floor((rows * resolution - y2) / resolution))
        max_row = int(math.ceil((rows * resolution - y1) / resolution))
        for row in range(max(0, min_row), min(rows, max_row)):
            for col in range(max(0, min_col), min(cols, max_col)):
                cells.add((row, col))
    return cells


def _geometric_bfs(
    free,
    sweep,
    can_translate,
    start_layer,
    start_row,
    start_col,
    end_layer,
    end_row,
    end_col,
    orientations,
    resolution,
    rows,
    map_origin,
):
    dist = np.full((ORIENTATION_STEPS, rows, free.shape[2]), -1, dtype=np.int32)
    previous = {}
    queue = []
    dist[start_layer, start_row, start_col] = 0
    previous[(start_layer, start_row, start_col)] = None
    queue.append((start_layer, start_row, start_col))

    moves = ((-1, 0), (1, 0), (0, -1), (0, 1))
    head = 0
    target = None
    while head < len(queue):
        layer, row, col = queue[head]
        head += 1
        if layer == end_layer and row == end_row and col == end_col:
            target = (layer, row, col)
            break
        for move_index, (dr, dc) in enumerate(moves):
            next_row, next_col = row + dr, col + dc
            if not (0 <= next_row < rows and 0 <= next_col < free.shape[2]):
                continue
            if not can_translate[move_index][layer, row, col]:
                continue
            key = (layer, next_row, next_col)
            if dist[key] == -1:
                dist[key] = dist[layer, row, col] + 1
                previous[key] = (layer, row, col)
                queue.append(key)
        for next_layer in ((layer - 1) % ORIENTATION_STEPS, (layer + 1) % ORIENTATION_STEPS):
            if not sweep[next_layer, row, col]:
                continue
            key = (next_layer, row, col)
            if dist[key] == -1:
                dist[key] = dist[layer, row, col] + 1
                previous[key] = (layer, row, col)
                queue.append(key)

    if target is None:
        return None

    nodes = []
    current = target
    while current is not None:
        layer, row, col = current
        point = grid_to_world(row, col, resolution, rows, map_origin)
        nodes.append({"x": point.x, "y": point.y, "orientation": float(orientations[layer])})
        current = previous[current]
    nodes.reverse()
    return {
        "passable": True,
        "reason": "collision-free passage and required end orientation achieved with the complete rectangle footprint",
        "algorithm": ALGORITHM_VERSION,
        "path": nodes,
        "angular_resolution": 2.0 * math.pi / ORIENTATION_STEPS,
    }


def _intervals_overlap(start, end, close_start, close_end):
    """闭区间动作与半开封路 [close_start, close_end) 的占用重叠判定。

    端点含义：close_start 时刻必须已完全撤离，因此恰在 close_start 结束的
    撤离动作允许；close_end 时刻才开放，恰在 close_end 开始的进入动作允许。
    """

    return start < close_end - EPS and end > close_start + EPS


def _pose_clear(layer, row, col, timed, start, end):
    for zone in timed:
        if _intervals_overlap(start, end, zone["close_start"], zone["close_end"]):
            if zone["pose_blocked"][layer, row, col]:
                return False
    return True


def _move_clear(layer, row, col, next_row, next_col, timed, start, end):
    for zone in timed:
        source_in = zone["pose_blocked"][layer, row, col]
        target_in = zone["pose_blocked"][layer, next_row, next_col]
        if not source_in and not target_in:
            continue
        opening = zone["close_end"]
        closing = zone["close_start"]
        # 撤离：源在区内、目标在区外。恰在封路开始时刻出发允许（该时刻必须
        # 已经在撤离），但动作持续期间仍与封闭区间重叠则不行。
        if source_in and not target_in:
            # start < closing：撤离必须在封路开始前完成（end<=closing）。
            if start < closing - EPS and end > closing + EPS:
                return False
            # start == closing：边界时刻正在撤离允许；start > closing 则已晚。
            if start > closing + EPS and start < opening - EPS:
                return False
            continue
        # 进入：恰在开放时刻 close_end 出发允许，早一点都不行。
        if target_in and not source_in:
            if start < opening - EPS:
                return False
            continue
        # 源和目标都在区内：封闭期间任何一段都不允许。
        if _intervals_overlap(start, end, closing, opening):
            return False
    return True


def _turn_clear(layer, next_layer, row, col, timed, start, end):
    for zone in timed:
        if not _intervals_overlap(start, end, zone["close_start"], zone["close_end"]):
            continue
        # build_orientation_layers 中 sweep[layer] 覆盖该层与相邻层之间的整段
        # 角扫掠；转向目标层的 sweep 掩码正是本次转向扫过的范围。
        if zone["sweep_blocked"][next_layer % ORIENTATION_STEPS, row, col]:
            return False
    return True


def _checkpoint_progress(progress, layer, row, col, specs, time=0.0):
    while progress < len(specs):
        spec = specs[progress]
        if abs(row - spec["row"]) > spec["tolerance_cells"] or abs(
            col - spec["col"]
        ) > spec["tolerance_cells"]:
            return progress
        if layer not in spec["layers"]:
            return progress
        if not (spec["window_start"] - EPS <= time <= spec["window_end"] + EPS):
            return progress
        progress += 1
    return progress


def _inside_tolerance(row, col, spec):
    return (
        abs(row - spec["row"]) <= spec["tolerance_cells"]
        and abs(col - spec["col"]) <= spec["tolerance_cells"]
    )


def _next_resume_time(time, row, col, progress, specs, timed):
    """等待位上可重新出发的最早时刻。

    需要同时满足：下一个检查点窗口已开（若人在其容差区内）以及覆盖该等待
    位的所有封闭区间均已开放。禁止停留的通道不会出现在 wait_cells 中。
    """

    resume = time
    if progress < len(specs):
        spec = specs[progress]
        if _inside_tolerance(row, col, spec) and time < spec["window_start"]:
            resume = max(resume, spec["window_start"])
    layers_blocking = set()
    for zone in timed:
        blocking_layers = {
            int(layer)
            for layer in np.nonzero(zone["pose_blocked"][:, row, col])[0]
        }
        layers_blocking |= blocking_layers
        if blocking_layers and zone["close_start"] - EPS <= time < zone["close_end"]:
            resume = max(resume, zone["close_end"])
    return resume


def _timed_search(
    *,
    free,
    sweep,
    can_translate,
    specs,
    wait_cells,
    timed,
    start_layer,
    start_row,
    start_col,
    end_layer,
    end_row,
    end_col,
    orientations,
    resolution,
    rows,
    map_origin,
    linear_speed,
    angular_speed,
    start_time,
    search_budget,
    geometric_path,
    frozen_nodes=None,
):
    cols = free.shape[2]
    angle_step = 2.0 * math.pi / ORIENTATION_STEPS
    move_cost = resolution / linear_speed
    turn_cost = angle_step / angular_speed
    moves = ((-1, 0), (1, 0), (0, -1), (0, 1))
    completed = True

    def in_frozen(layer, row, col):
        return frozen_nodes is None or (layer, row, col) in frozen_nodes

    initial_progress = _checkpoint_progress(
        0, start_layer, start_row, start_col, specs, float(start_time)
    )

    # 状态 (进度, 姿态层, 行, 列, gate)：gate=1 表示已在合法等待位停住、
    # 等待窗口/封路边界后重新出发；普通边只能产生 gate=0，避免迟到标签
    # 被最早到达标签支配而丢失等待方案。
    start_key = (initial_progress, start_layer, start_row, start_col, 0)
    times = {start_key: float(start_time)}
    parents = {start_key: (None, "start", float(start_time), None)}
    heap = [(float(start_time), start_key)]
    goal_key = None
    expansions = 0

    def relax(candidate, arrival, parent, action, wait_resume=None, wait_start=None):
        best = times.get(candidate)
        if best is None or arrival < best - EPS:
            times[candidate] = arrival
            parents[candidate] = (parent, action, arrival, (wait_start, wait_resume))
            heapq.heappush(heap, (arrival, candidate))

    while heap:
        time, key = heapq.heappop(heap)
        if times[key] < time - EPS:
            continue
        progress_key = key
        progress, layer, row, col, gate = key
        if progress == len(specs) and (layer, row, col) == (
            end_layer,
            end_row,
            end_col,
        ):
            goal_key = key
            break
        expansions += 1
        if expansions > search_budget:
            completed = False
            break

        if gate == 1:
            resume_progress = _checkpoint_progress(
                progress, layer, row, col, specs, time
            )
            if resume_progress != progress:
                # 窗口恰在等待结束时刻开启并通过检查点：提升进度后继续展开。
                progress_key = (resume_progress, layer, row, col, 1)
                progress = resume_progress
            # 等待后的重新出发：只允许离开等待位，且出发时刻本身合法。
            for move_index, (dr, dc) in enumerate(moves):
                next_row, next_col = row + dr, col + dc
                if not (0 <= next_row < rows and 0 <= next_col < cols):
                    continue
                if not can_translate[move_index][layer, row, col]:
                    continue
                if not in_frozen(layer, next_row, next_col):
                    continue
                arrival = time + move_cost
                if not _move_clear(
                    layer, row, col, next_row, next_col, timed, time, arrival
                ):
                    continue
                new_progress = _checkpoint_progress(
                    progress, layer, next_row, next_col, specs, arrival
                )
                if (
                    next_spec is not None
                    and new_progress == progress
                    and not _inside_tolerance(next_row, next_col, next_spec)
                    and _inside_tolerance(row, col, next_spec)
                ):
                    continue
                relax(
                    (new_progress, layer, next_row, next_col, 0),
                    arrival,
                    progress_key,
                    "wait_move",
                )
            for next_layer in (
                (layer - 1) % ORIENTATION_STEPS,
                (layer + 1) % ORIENTATION_STEPS,
            ):
                if not sweep[next_layer, row, col]:
                    continue
                if not in_frozen(next_layer, row, col):
                    continue
                arrival = time + turn_cost
                if not _turn_clear(layer, next_layer, row, col, timed, time, arrival):
                    continue
                new_progress = _checkpoint_progress(
                    progress, next_layer, row, col, specs, arrival
                )
                relax(
                    (new_progress, next_layer, row, col, 0),
                    arrival,
                    progress_key,
                    "wait_turn",
                )
            continue

        next_spec = specs[progress] if progress < len(specs) else None

        # 普通状态：检查点窗口已关且尚未通过，则此路不通。
        if next_spec is not None and time > next_spec["window_end"] + EPS:
            continue
        # 未到窗口且身处检查点容差区：只能在合法等待位原地等候。
        inside_early = (
            next_spec is not None
            and time + EPS < next_spec["window_start"]
            and _inside_tolerance(row, col, next_spec)
        )
        if inside_early and (row, col) not in wait_cells:
            continue

        # 在合法等待位生成“停住并等到最早可行动时刻”的 gate=1 标签。
        can_arm = False
        resume = time
        if (row, col) in wait_cells:
            resume = _next_resume_time(time, row, col, progress, specs, timed)
            if resume > time + EPS and _pose_clear(
                layer, row, col, timed, time, resume
            ):
                can_arm = True
        if inside_early and (row, col) in wait_cells:
            can_arm = True
            resume = max(resume, next_spec["window_start"])
        if can_arm:
            resume_progress = _checkpoint_progress(
                progress, layer, row, col, specs, resume
            )
            relax(
                (resume_progress, layer, row, col, 1),
                resume,
                key,
                "wait",
                wait_resume=resume,
                wait_start=time,
            )
            # 若恰好在窗口开启瞬间通过检查点，gate=1 状态会在弹出时以 resume
            # 时刻继续展开；等待本身不再产生普通移动。
            continue

        for move_index, (dr, dc) in enumerate(moves):
            next_row, next_col = row + dr, col + dc
            if not (0 <= next_row < rows and 0 <= next_col < cols):
                continue
            if not can_translate[move_index][layer, row, col]:
                continue
            if not in_frozen(layer, next_row, next_col):
                continue
            arrival = time + move_cost
            if not _move_clear(
                layer, row, col, next_row, next_col, timed, time, arrival
            ):
                continue
            new_progress = _checkpoint_progress(
                progress, layer, next_row, next_col, specs, arrival
            )
            # 不得在窗口关闭/未开期间穿过容差区而不通过检查点。
            if next_spec is not None and new_progress == progress:
                leaving = _inside_tolerance(row, col, next_spec) and not _inside_tolerance(
                    next_row, next_col, next_spec
                )
                if leaving:
                    continue
                if (
                    _inside_tolerance(next_row, next_col, next_spec)
                    and arrival + EPS < next_spec["window_start"]
                    and (next_row, next_col) not in wait_cells
                ):
                    continue
                if arrival > next_spec["window_end"] + EPS:
                    continue
            relax(
                (new_progress, layer, next_row, next_col, 0),
                arrival,
                key,
                "move",
            )

        for next_layer in (
            (layer - 1) % ORIENTATION_STEPS,
            (layer + 1) % ORIENTATION_STEPS,
        ):
            if not sweep[next_layer, row, col]:
                continue
            if not in_frozen(next_layer, row, col):
                continue
            arrival = time + turn_cost
            if not _turn_clear(layer, next_layer, row, col, timed, time, arrival):
                continue
            new_progress = _checkpoint_progress(
                progress, next_layer, row, col, specs, arrival
            )
            if (
                next_spec is not None
                and new_progress == progress
                and arrival > next_spec["window_end"] + EPS
            ):
                continue
            relax(
                (new_progress, next_layer, row, col, 0),
                arrival,
                key,
                "turn",
            )

    if not completed:
        return {"status": "search_incomplete", "goal_key": None, "parents": {}}
    if goal_key is None:
        diagnosis = _diagnose_timed_failure(
            sweep=sweep,
            can_translate=can_translate,
            specs=specs,
            timed=timed,
            start_layer=start_layer,
            start_row=start_row,
            start_col=start_col,
        )
        return {"status": diagnosis["status"], "reason": diagnosis["reason"], "goal_key": None, "parents": {}}

    schedule = _reconstruct_schedule(
        goal_key, parents, orientations, resolution, rows, map_origin
    )
    return {
        "status": "feasible",
        "schedule": schedule,
        "goal_key": goal_key,
        "parents": parents,
    }


def _diagnose_timed_failure(
    *,
    sweep,
    can_translate,
    specs,
    timed,
    start_layer,
    start_row,
    start_col,
):
    rows, cols = sweep.shape[1], sweep.shape[2]
    moves = ((-1, 0), (1, 0), (0, -1), (0, 1))
    seen = set()
    start = (0, start_layer, start_row, start_col)
    seen.add(start)
    queue = [start]
    head = 0
    while head < len(queue):
        progress, layer, row, col = queue[head]
        head += 1
        candidates = []
        for move_index, (dr, dc) in enumerate(moves):
            next_row, next_col = row + dr, col + dc
            if (
                0 <= next_row < rows
                and 0 <= next_col < cols
                and can_translate[move_index][layer, row, col]
            ):
                candidates.append((layer, next_row, next_col))
        for next_layer in (
            (layer - 1) % ORIENTATION_STEPS,
            (layer + 1) % ORIENTATION_STEPS,
        ):
            if sweep[next_layer, row, col]:
                candidates.append((next_layer, row, col))
        for cand_layer, cand_row, cand_col in candidates:
            new_progress = _checkpoint_progress(
                progress, cand_layer, cand_row, cand_col, specs, 0.0
            )
            candidate = (new_progress, cand_layer, cand_row, cand_col)
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)

    for index, spec in enumerate(specs):
        row_lo = max(0, spec["row"] - spec["tolerance_cells"])
        row_hi = min(rows, spec["row"] + spec["tolerance_cells"] + 1)
        col_lo = max(0, spec["col"] - spec["tolerance_cells"])
        col_hi = min(cols, spec["col"] + spec["tolerance_cells"] + 1)

        def reachable_in(layers):
            for layer in layers:
                for row in range(row_lo, row_hi):
                    for col in range(col_lo, col_hi):
                        if (index, layer, row, col) in seen:
                            return True
            return False

        if not reachable_in(range(ORIENTATION_STEPS)):
            return {
                "status": "road_blocked",
                "reason": (
                    f"checkpoint {spec['order']} is inside a passage the complete "
                    "rectangle footprint cannot reach at any orientation"
                ),
            }
        if not reachable_in(spec["layers"]):
            return {
                "status": "orientation_mismatch",
                "reason": (
                    f"checkpoint {spec['order']} cannot be reached with the required "
                    "orientation for the complete rectangle footprint"
                ),
            }

    if timed:
        return {
            "status": "timed_blocked",
            "reason": (
                "geometry and checkpoint poses exist, but no schedule can clear every "
                "temporary closure: waiting spots are closed, evacuation finishes too "
                "late, or the corridor cannot be used as a waiting area"
            ),
        }
    return {
        "status": "window_missed",
        "reason": (
            "the checkpoint pose is geometrically reachable, but no allowed waiting "
            "area lets the robot pass inside every ordered time window"
        ),
    }


def _reconstruct_schedule(
    goal_key,
    parents,
    orientations,
    resolution,
    rows,
    map_origin,
):
    events = []
    key = goal_key
    while key is not None:
        parent, action, time, wait_info = parents[key]
        progress, layer, row, col, gate = key
        point = grid_to_world(row, col, resolution, rows, map_origin)
        event = {
            "event": action,
            "time": time,
            "gate": gate,
            "x": point.x,
            "y": point.y,
            "orientation": float(orientations[layer]),
            "row": row,
            "col": col,
            "checkpoint": progress,
        }
        if wait_info is not None:
            wait_start, wait_until = wait_info
            event["wait_start"] = wait_start
            event["wait_until"] = wait_until
        events.append(event)
        key = parent
    events.reverse()
    return events


def _segments_from_schedule(
    schedule,
    specs,
    timed,
    resolution,
    move_cost,
    turn_cost,
):
    """把搜索状态序列压成可对外逐段重放的到达/转向/等待/通过记录。"""

    segments = []
    previous = None
    for event in schedule:
        action = event["event"]
        if action == "start":
            previous = event
            segments.append(
                {
                    "type": "start",
                    "time": event["time"],
                    "x": event["x"],
                    "y": event["y"],
                    "orientation": event["orientation"],
                    "row": event["row"],
                    "col": event["col"],
                }
            )
            continue
        if action == "wait":
            segments.append(
                {
                    "type": "wait",
                    "start": event["wait_start"],
                    "end": event["wait_until"],
                    "x": event["x"],
                    "y": event["y"],
                    "orientation": event["orientation"],
                    "row": event["row"],
                    "col": event["col"],
                }
            )
            previous = event
            continue
        if action in ("move", "wait_move"):
            duration = move_cost
            kind = "arrive" if action == "move" else "wait_depart_arrive"
            seg = {
                "type": kind,
                "start": event["time"] - duration,
                "end": event["time"],
                "from_x": previous["x"],
                "from_y": previous["y"],
                "x": event["x"],
                "y": event["y"],
                "orientation": event["orientation"],
                "from_row": previous["row"],
                "from_col": previous["col"],
                "row": event["row"],
                "col": event["col"],
            }
        elif action in ("turn", "wait_turn"):
            # 相邻姿态层只差一个角层；整圈旋转时方向角会从 2π 跳回 0，
            # 不能用显示角度差推算耗时。
            duration = turn_cost
            before_orientation = (
                previous["orientation"] if previous is not None else event["orientation"]
            )
            seg = {
                "type": "turn",
                "start": event["time"] - duration,
                "end": event["time"],
                "x": event["x"],
                "y": event["y"],
                "from_orientation": before_orientation,
                "orientation": event["orientation"],
                "angular_steps": 1,
                "row": event["row"],
                "col": event["col"],
            }
        else:
            continue
        segments.append(seg)
        previous = event

    # 兜底：用检查点进度变化补“通过检查点”事件。
    return _annotate_checkpoint_events(segments, schedule, specs)


def _segments_for_path(path, start_time, move_cost, turn_cost, specs, timed):
    segments = [
        {
            "type": "start",
            "time": start_time,
            "x": path[0]["x"],
            "y": path[0]["y"],
            "orientation": path[0]["orientation"],
        }
    ]
    time = start_time
    previous = path[0]
    for node in path[1:]:
        if abs(node["orientation"] - previous["orientation"]) > EPS:
            segments.append(
                {
                    "type": "turn",
                    "start": time,
                    "end": time + turn_cost,
                    "x": node["x"],
                    "y": node["y"],
                    "from_orientation": previous["orientation"],
                    "orientation": node["orientation"],
                }
            )
            time += turn_cost
        if abs(node["x"] - previous["x"]) > EPS or abs(node["y"] - previous["y"]) > EPS:
            segments.append(
                {
                    "type": "arrive",
                    "start": time,
                    "end": time + move_cost,
                    "from_x": previous["x"],
                    "from_y": previous["y"],
                    "x": node["x"],
                    "y": node["y"],
                    "orientation": node["orientation"],
                }
            )
            time += move_cost
        previous = node
    return segments


def _annotate_checkpoint_events(segments, schedule, specs):
    events = []
    last_progress = 0
    for event in schedule:
        cp = event["checkpoint"]
        if cp > last_progress:
            for passed in range(last_progress, cp):
                spec = specs[passed]
                events.append(
                    {
                        "sort_time": event["time"],
                        "segment": {
                            "type": "checkpoint",
                            "order": spec["order"],
                            "time": event["time"],
                            "x": event["x"],
                            "y": event["y"],
                            "orientation": event["orientation"],
                            "row": event["row"],
                            "col": event["col"],
                            "window_start": spec["window_start"],
                            "window_end": spec["window_end"],
                            "tolerance": spec["tolerance"],
                        },
                    }
                )
            last_progress = cp
    for index, seg in enumerate(segments):
        events.append({"sort_time": seg.get("end", seg.get("time", 0.0)), "segment": seg})
    events.sort(key=lambda item: item["sort_time"])
    return [item["segment"] for item in events]




def _package_plan(
    result,
    common,
    specs,
    timed,
    resolution,
    linear_speed,
    angular_speed,
    stage,
):
    move_cost = resolution / linear_speed
    turn_cost = (2.0 * math.pi / ORIENTATION_STEPS) / angular_speed
    if result["status"] == "search_incomplete":
        return {
            **common,
            "passable": False,
            "status": "search_incomplete",
            "reason": "search budget exhausted before the timed plan was proved feasible or infeasible",
            "schedule": [],
            "segments": [],
            "plans": [],
            "search_completed": False,
            "stage": stage,
        }
    if result["status"] != "feasible":
        return {
            **common,
            "passable": False,
            "status": result["status"],
            "reason": result["reason"],
            "schedule": [],
            "segments": [],
            "plans": [],
            "search_completed": True,
            "stage": stage,
        }
    schedule = result["schedule"]
    segments = _segments_from_schedule(
        schedule, specs, timed, resolution, move_cost, turn_cost
    )
    replay = replay_segments(
        segments=segments,
        specs=specs,
        timed=timed,
        move_cost=move_cost,
        turn_cost=turn_cost,
    )
    plan = {
        "id": 0,
        "label": "保留原几何路线重排时间" if stage == "retain_geometry" else "重新选路方案",
        "feasible": True,
        "stage": stage,
        "arrival_time": schedule[-1]["time"],
        "schedule": schedule,
        "segments": segments,
        "replay": replay,
        "waiting_cells": sorted(
            {(item["row"], item["col"]) for item in schedule if item["event"] == "wait"}
        ),
    }
    return {
        **common,
        "passable": True,
        "status": "feasible",
        "reason": "逐段重放确认：到达、转向、等待与检查点通过在各封路时段内均合法",
        "schedule": schedule,
        "segments": segments,
        "replay": replay,
        "plans": [plan],
        "plan": plan,
        "search_completed": True,
        "stage": stage,
    }


def _summarize_stage(result, timed, specs):
    reasons = {
        "search_incomplete": "原几何路线下搜索预算耗尽，可行性尚未证明",
        "feasible": "原几何路线可仅靠重排时间通过",
        "timed_blocked": (
            "原几何路线无法在封路时段内撤离或找到合法等待位，需要重新选路"
        ),
        "window_missed": "原几何路线缺少合法等待位，赶不上检查点窗口",
        "road_blocked": "原几何路线存在不可达姿态",
        "orientation_mismatch": "原几何路线无法满足检查点姿态",
        "blocked": "原几何路线与静态障碍冲突",
    }
    summary = {"status": result["status"], "reason": reasons.get(result["status"], result.get("reason", ""))}
    if result["status"] == "feasible":
        summary["arrival_time"] = result["schedule"][-1]["time"]
    return summary


def replay_segments(*, segments, specs, timed, move_cost, turn_cost):
    """独立重放每一段：时间连续、转向扫掠、等待占位与检查点窗口都重新核验。"""

    checks = []
    checkpoint_hits = {}
    failures = []

    def record(index, kind, ok, **detail):
        item = {"index": index, "type": kind, "ok": bool(ok)}
        item.update(detail)
        checks.append(item)
        if not ok:
            failures.append(item)

    cursor = None
    for index, seg in enumerate(segments):
        kind = seg["type"]
        if kind == "start":
            cursor = {"time": seg["time"], "x": seg["x"], "y": seg["y"], "orientation": seg["orientation"]}
            record(index, kind, True)
            continue
        if kind == "wait":
            ok = seg["end"] >= seg["start"] - EPS
            violations = []
            for zone in timed:
                if _intervals_overlap(seg["start"], seg["end"], zone["close_start"], zone["close_end"]):
                    layer = nearest_orientation_layer(seg["orientation"])
                    if zone["pose_blocked"][layer, seg["row"], seg["col"]]:
                        violations.append(zone["id"])
            ok = ok and not violations
            record(index, kind, ok, closed_zones=violations)
            cursor = {"time": seg["end"], "x": seg["x"], "y": seg["y"], "orientation": seg["orientation"]}
            continue
        if kind == "turn":
            violations = []
            expected_cost = turn_cost * float(seg.get("angular_steps", 1))
            timing_ok = abs((seg["end"] - seg["start"]) - expected_cost) <= 1e-6
            continuity_ok = cursor is not None and abs(seg["start"] - cursor["time"]) <= 1e-6
            layer_to = nearest_orientation_layer(seg["orientation"])
            for zone in timed:
                if _intervals_overlap(seg["start"], seg["end"], zone["close_start"], zone["close_end"]):
                    if zone["sweep_blocked"][layer_to, seg["row"], seg["col"]]:
                        violations.append(zone["id"])
            record(
                index,
                kind,
                not violations and timing_ok and continuity_ok,
                closed_zones=violations,
                timing_ok=timing_ok,
                continuity_ok=continuity_ok,
            )
            cursor = {"time": seg["end"], "x": seg["x"], "y": seg["y"], "orientation": seg["orientation"]}
            continue
        if kind in ("arrive", "wait_depart_arrive"):
            violations = []
            timing_ok = abs((seg["end"] - seg["start"]) - move_cost) <= 1e-6
            continuity_ok = cursor is not None and abs(seg["start"] - cursor["time"]) <= 1e-6
            adjacency_ok = (
                abs(abs(seg["row"] - seg["from_row"]) + abs(seg["col"] - seg["from_col"]) - 1)
                <= EPS
            )
            layer = nearest_orientation_layer(seg["orientation"])
            for zone in timed:
                if _intervals_overlap(seg["start"], seg["end"], zone["close_start"], zone["close_end"]):
                    if zone["pose_blocked"][layer, seg["row"], seg["col"]] or zone[
                        "pose_blocked"
                    ][layer, seg["from_row"], seg["from_col"]]:
                        violations.append(zone["id"])
            record(
                index,
                kind,
                not violations and timing_ok and continuity_ok and adjacency_ok,
                closed_zones=violations,
                timing_ok=timing_ok,
                continuity_ok=continuity_ok,
                adjacency_ok=adjacency_ok,
            )
            cursor = {"time": seg["end"], "x": seg["x"], "y": seg["y"], "orientation": seg["orientation"]}
            continue
        if kind == "checkpoint":
            spec = next((item for item in specs if item["order"] == seg["order"]), None)
            window_ok = (
                spec is not None
                and spec["window_start"] - EPS <= seg["time"] <= spec["window_end"] + EPS
            )
            position_ok = (
                spec is not None
                and abs(seg["row"] - spec["row"]) <= spec["tolerance_cells"]
                and abs(seg["col"] - spec["col"]) <= spec["tolerance_cells"]
            )
            orientation_ok = spec is not None and nearest_orientation_layer(
                seg["orientation"]
            ) in spec["layers"]
            ok = window_ok and position_ok and orientation_ok
            if spec is not None:
                checkpoint_hits[spec["order"]] = seg["time"]
            record(
                index,
                kind,
                ok,
                window_ok=window_ok,
                position_ok=position_ok,
                orientation_ok=orientation_ok,
            )
            continue
        record(index, kind, False, reason="unknown segment")

    missing = [spec["order"] for spec in specs if spec["order"] not in checkpoint_hits]
    all_ok = not failures and not missing
    return {
        "ok": all_ok,
        "checks": checks,
        "failures": failures,
        "missing_checkpoints": missing,
        "checkpoint_times": checkpoint_hits,
        "segment_count": len(segments),
    }


