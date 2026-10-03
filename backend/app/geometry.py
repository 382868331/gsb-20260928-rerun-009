from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any

import numpy as np


ALGORITHM_VERSION = "rectangle-sweep-timed-checkpoints-v3"

# 10 deg per configuration layer. Each layer conservatively covers the whole
# angular interval around its center, so changing between adjacent layers can
# never skip an intermediate heading.
ORIENTATION_STEPS = 36
ANGLE_SUBDIVISIONS = 5
DEFAULT_SEARCH_BUDGET = 1_500_000
EPS = 1e-9
MOVES = ((-1, 0), (1, 0), (0, -1), (0, 1))


def intervals_overlap(t0: float, t1: float, close_start: float, close_end: float) -> bool:
    """Positive-length overlap of [t0, t1] with [close_start, close_end).

    Boundary rule: finishing exactly at ``close_start`` counts as evacuated in
    time, and starting exactly at ``close_end`` counts as allowed re-entry.
    """
    return t0 < close_end - EPS and t1 > close_start + EPS


def closed_at(close_start: float, close_end: float, time: float) -> bool:
    """Whether a pose held at ``time`` violates the closure.

    The closure is active on [close_start, close_end): at ``close_start`` the
    robot must already be outside, at ``close_end`` it may be inside again.
    """
    return close_start - EPS <= time < close_end - EPS


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
        pose_mask, sweep_mask = _layer_masks(
            float(center_angle), robot_width, robot_length, resolution
        )
        free[layer] = _free_centers_for_mask(occupancy, pose_mask)
        sweep[layer] = _free_centers_for_mask(occupancy, sweep_mask)
    return free, sweep, orientations


def _layer_masks(
    center_angle: float,
    robot_width: float,
    robot_length: float,
    resolution: float,
) -> tuple[np.ndarray, np.ndarray]:
    angle_step = 2.0 * math.pi / ORIENTATION_STEPS
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
    return pose_mask, sweep_mask


def build_closure_layers(
    closure_zones: list[dict[str, Any]],
    rows: int,
    cols: int,
    resolution: float,
    origin: Point,
    robot_width: float,
    robot_length: float,
) -> list[dict[str, Any]]:
    """Per-closure time-dependent clearance arrays.

    For each closure zone the same rectangle masks used for static obstacles
    produce ``free``/``sweep``/``moves`` arrays: while the closure is active
    the robot body (pose, turning sweep or translation sweep) must stay
    completely outside the zone.
    """
    closures: list[dict[str, Any]] = []
    angle_step = 2.0 * math.pi / ORIENTATION_STEPS
    orientations = np.arange(ORIENTATION_STEPS, dtype=float) * angle_step
    for zone in closure_zones:
        occupancy = np.zeros((rows, cols), dtype=bool)
        for cell in _zone_cells([zone], resolution, rows, cols, origin):
            occupancy[cell] = True
        free = np.zeros((ORIENTATION_STEPS, rows, cols), dtype=bool)
        sweep = np.zeros((ORIENTATION_STEPS, rows, cols), dtype=bool)
        for layer, center_angle in enumerate(orientations):
            pose_mask, sweep_mask = _layer_masks(
                float(center_angle), robot_width, robot_length, resolution
            )
            free[layer] = _free_centers_for_mask(occupancy, pose_mask)
            sweep[layer] = _free_centers_for_mask(occupancy, sweep_mask)
        moves = tuple(_move_free(free, direction) for direction in MOVES)
        closures.append(
            {
                "id": zone.get("id", ""),
                "close_start": float(zone["close_start"]),
                "close_end": float(zone["close_end"]),
                "free": free,
                "sweep": sweep,
                "moves": moves,
            }
        )
    return closures


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
    shifted = np.zeros_like(free)
    # shifted[x] = free[x + direction], so the conjunction below marks a
    # source cell safe only when the destination pose is collision free too.
    shifted[:, src_r_min:src_r_max, src_c_min:src_c_max] = free[
        :, src_r_min + dr:src_r_max + dr, src_c_min + dc:src_c_max + dc
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
    closure_zones: list[dict[str, Any]] | None = None,
    linear_speed: float = 0.5,
    angular_speed: float = 0.5,
    start_time: float = 0.0,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
    forbidden_wait_cells: list[tuple[int, int]] | None = None,
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
    start_row, start_col = world_to_grid(Point(start["x"], start["y"]), resolution, rows, map_origin)
    end_row, end_col = world_to_grid(Point(end["x"], end["y"]), resolution, rows, map_origin)
    if not (0 <= start_row < rows and 0 <= start_col < cols):
        raise ValueError("start is outside the map")
    if not (0 <= end_row < rows and 0 <= end_col < cols):
        raise ValueError("end is outside the map")

    free, sweep, orientations = build_orientation_layers(
        occupancy, robot_width, robot_length, resolution
    )
    closures = build_closure_layers(
        closure_zones or [],
        rows,
        cols,
        resolution,
        map_origin,
        robot_width,
        robot_length,
    )
    for zone in closures:
        if zone["close_end"] <= zone["close_start"]:
            raise ValueError("closure window end must be after its start")
    angular_resolution = 2.0 * math.pi / ORIENTATION_STEPS
    start_layer = nearest_orientation_layer(start_orientation)
    end_layer = nearest_orientation_layer(
        start_orientation if end_orientation is None else end_orientation
    )

    if not free[start_layer, start_row, start_col]:
        return {
            "passable": False,
            "status": "blocked",
            "reason": "start pose collides with an obstacle for the complete rectangle footprint",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
            "angular_resolution": angular_resolution,
        }
    if not free[end_layer, end_row, end_col]:
        return {
            "passable": False,
            "status": "blocked",
            "reason": "end pose collides with an obstacle for the complete rectangle footprint",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
            "angular_resolution": angular_resolution,
        }

    # Translation edge masks. A movement is safe only when the swept cells of
    # both endpoints are obstacle free; this uses the actual rectangle instead
    # of the in-place angular sweep mask.
    can_up = _move_free(free, (-1, 0))
    can_down = _move_free(free, (1, 0))
    can_left = _move_free(free, (0, -1))
    can_right = _move_free(free, (0, 1))
    can_translate = (can_up, can_down, can_left, can_right)

    # Start pose must not sit inside an active closure: the robot cannot
    # already be somewhere it is forbidden to be at start_time.
    for zone in closures:
        if closed_at(zone["close_start"], zone["close_end"], float(start_time)) and not zone[
            "free"
        ][start_layer, start_row, start_col]:
            return {
                "passable": False,
                "status": "blocked",
                "reason": (
                    "start pose is inside a temporarily closed region while that "
                    "region is closed at the departure time"
                ),
                "algorithm": ALGORITHM_VERSION,
                "path": [],
                "angular_resolution": angular_resolution,
                "search_completed": True,
            }

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
            "passable": False,
            "status": "blocked",
            "reason": "no collision-free trajectory exists for the complete rectangle footprint, including the in-place turning sweep",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
            "angular_resolution": angular_resolution,
            "search_completed": True,
        }

    if not checkpoints and not closures and not keep_geometry:
        geometric["status"] = "feasible"
        geometric["search_completed"] = True
        return geometric

    ordered = sorted(checkpoints or [], key=lambda item: int(item["order"]))
    checkpoint_specs = []
    for item in ordered:
        point = Point(float(item["x"]), float(item["y"]))
        cp_row, cp_col = world_to_grid(point, resolution, rows, map_origin)
        if not (0 <= cp_row < rows and 0 <= cp_col < cols):
            raise ValueError("checkpoint is outside the map")
        # Missing and explicit-null tolerances share one default so a saved
        # checkpoint without the optional fields validates identically.
        raw_tolerance = item.get("tolerance")
        if raw_tolerance is None:
            raw_tolerance = max(resolution, 1.5 * resolution)
        tolerance = float(raw_tolerance)
        tolerance_cells = max(1, int(round(tolerance / resolution)))
        wanted_layer = nearest_orientation_layer(float(item["orientation"]))
        raw_orient = item.get("orientation_tolerance")
        if raw_orient is None:
            raw_orient = angular_resolution / 2.0
        orient_tolerance = float(raw_orient)
        orient_steps = max(0, int(math.floor(orient_tolerance / angular_resolution)))
        layers = {
            (wanted_layer + delta) % ORIENTATION_STEPS
            for delta in range(-orient_steps, orient_steps + 1)
        }
        checkpoint_specs.append(
            {
                "order": int(item["order"]),
                "row": cp_row,
                "col": cp_col,
                "tolerance_cells": tolerance_cells,
                "layers": layers,
                "window_start": float(item["window_start"]),
                "window_end": float(item["window_end"]),
            }
        )

    wait_cells = _zone_cells(waiting_zones or [], resolution, rows, cols, map_origin)
    blocked_cells = set(forbidden_wait_cells or [])
    if keep_geometry:
        # Reschedule the previously returned geometry under the new windows
        # and closures without letting the search pick a different route.
        if not geometric_path:
            raise ValueError("keep_geometry requires a geometric_path to reschedule")
        return _schedule_fixed_path(
            path=geometric_path,
            specs=checkpoint_specs,
            closures=closures,
            free=free,
            sweep=sweep,
            can_translate=can_translate,
            wait_cells=wait_cells - blocked_cells,
            start_layer=start_layer,
            end_layer=end_layer,
            orientations=orientations,
            resolution=resolution,
            rows=rows,
            map_origin=map_origin,
            linear_speed=linear_speed,
            angular_speed=angular_speed,
            start_time=start_time,
        )
    plans: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    # Each rerun forbids wait cells used by the previous plan, producing
    # alternative waiting-position schedules while keeping the same geometry.
    for plan_index in range(3):
        result = _timed_search(
            free=free,
            sweep=sweep,
            can_translate=can_translate,
            specs=checkpoint_specs,
            closures=closures,
            wait_cells=wait_cells - blocked_cells,
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
        if result["status"] == "search_incomplete":
            result["plans"] = plans
            return result
        if result["status"] != "feasible":
            break
        plan = result["plans"][0]
        used = {tuple(cell) for cell in plan["waiting_cells"]}
        if not used or used <= blocked_cells:
            if not plans:
                plans.append(plan)
            break
        plan["id"] = plan_index
        plan["label"] = ("最早到达方案", "备选等待方案一", "备选等待方案二")[plan_index]
        plans.append(plan)
        blocked_cells |= used

    assert result is not None
    if plans:
        # A later run may be infeasible merely because an alternative wait cell
        # was forbidden; the already found schedules remain valid.
        result["plans"] = plans
        result["schedule"] = plans[0]["schedule"]
        result["segments"] = plans[0]["segments"]
        result["status"] = "feasible"
        result["passable"] = True
        result["reason"] = (
            f"found {len(plans)} feasible timed schedule(s) with different waiting positions"
            if len(plans) > 1
            else "all ordered checkpoints are reached inside their time windows with the required orientation"
        )
    return result


def _geometric_bfs(
    free: np.ndarray,
    sweep: np.ndarray,
    can_translate: tuple[np.ndarray, ...],
    start_layer: int,
    start_row: int,
    start_col: int,
    end_layer: int,
    end_row: int,
    end_col: int,
    orientations: np.ndarray,
    resolution: float,
    rows: int,
    map_origin: Point,
) -> dict[str, Any] | None:
    """BFS in (heading layer, cell) space.

    Reaching the end cell is not enough: the exact end layer is required,
    otherwise arriving at the right place with the wrong heading would be
    reported as success.
    """

    dist = np.full((ORIENTATION_STEPS, rows, free.shape[2]), -1, dtype=np.int32)
    previous: dict[tuple[int, int, int], tuple[int, int, int] | None] = {}
    queue: list[tuple[int, int, int]] = []
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
            # In-place rotation is allowed only when the whole swept angular
            # interval fits, not merely the two endpoint headings.
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
    current: tuple[int, int, int] | None = target
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


def _zone_cells(
    zones: list[dict[str, float]],
    resolution: float,
    rows: int,
    cols: int,
    origin: Point,
) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
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
        for cell in range(max(0, min_row), min(rows, max_row)):
            for col_index in range(max(0, min_col), min(cols, max_col)):
                cells.add((cell, col_index))
    return cells


def _checkpoint_progress(
    progress: int,
    layer: int,
    row: int,
    col: int,
    specs: list[dict[str, Any]],
    time: float = 0.0,
) -> int:
    while progress < len(specs):
        spec = specs[progress]
        if abs(row - spec["row"]) > spec["tolerance_cells"] or abs(
            col - spec["col"]
        ) > spec["tolerance_cells"]:
            return progress
        if layer not in spec["layers"]:
            return progress
        # Crossing the pose outside its window is not a valid pass: arriving
        # too early does not count and too late is a missed window.
        if not (spec["window_start"] - 1e-9 <= time <= spec["window_end"] + 1e-9):
            return progress
        progress += 1
    return progress


def _timed_search(
    *,
    free: np.ndarray,
    sweep: np.ndarray,
    can_translate: tuple[np.ndarray, ...],
    specs: list[dict[str, Any]],
    closures: list[dict[str, Any]],
    wait_cells: set[tuple[int, int]],
    start_layer: int,
    start_row: int,
    start_col: int,
    end_layer: int,
    end_row: int,
    end_col: int,
    orientations: np.ndarray,
    resolution: float,
    rows: int,
    map_origin: Point,
    linear_speed: float,
    angular_speed: float,
    start_time: float,
    search_budget: int,
    geometric_path: list[dict[str, Any]],
) -> dict[str, Any]:
    cols = free.shape[2]
    angle_step = 2.0 * math.pi / ORIENTATION_STEPS
    move_cost = resolution / linear_speed
    turn_cost = angle_step / angular_speed
    moves = ((-1, 0), (1, 0), (0, -1), (0, 1))
    completed = True

    def pose_clear_time(layer, row, col, t0, t1):
        # A pose held over [t0, t1] must keep the whole rectangle outside
        # every closure that is active during that interval.
        for zone in closures:
            if intervals_overlap(t0, t1, zone["close_start"], zone["close_end"]) and not zone[
                "free"
            ][layer, row, col]:
                return False
        return True

    def move_clear_time(move_index, layer, row, col, t0, t1):
        for zone in closures:
            if intervals_overlap(t0, t1, zone["close_start"], zone["close_end"]) and not zone[
                "moves"
            ][move_index][layer, row, col]:
                return False
        return True

    def turn_clear_time(next_layer, row, col, t0, t1):
        for zone in closures:
            if intervals_overlap(t0, t1, zone["close_start"], zone["close_end"]) and not zone[
                "sweep"
            ][next_layer, row, col]:
                return False
        return True

    def pose_clear_instant(layer, row, col, time):
        # Boundary rule: at close_start the body must already be fully
        # outside, so a pose held at that exact instant is checked too.
        for zone in closures:
            if closed_at(zone["close_start"], zone["close_end"], time) and not zone[
                "free"
            ][layer, row, col]:
                return False
        return True

    initial_progress = _checkpoint_progress(
        0, start_layer, start_row, start_col, specs, float(start_time)
    )

    # State is (checkpoint progress, heading layer, row, col). Earliest arrival
    # always dominates: leaving later cannot help unless the robot waits, which
    # is handled by an explicit wait self-edge inside allowed waiting areas.
    start_key = (initial_progress, start_layer, start_row, start_col)
    times: dict[tuple[int, int, int, int], float] = {start_key: float(start_time)}
    parents: dict[tuple[int, int, int, int], tuple[Any, str, float, float | None]] = {
        start_key: (None, "start", float(start_time), None)
    }
    heap: list[tuple[float, tuple[int, int, int, int]]] = [
        (float(start_time), start_key)
    ]
    goal_key: tuple[int, int, int, int] | None = None
    expansions = 0

    def relax(candidate, arrival, parent, action, wait_resume=None):
        best = times.get(candidate)
        if best is None or arrival < best - 1e-9:
            times[candidate] = arrival
            parents[candidate] = (parent, action, arrival, wait_resume)
            heapq.heappush(heap, (arrival, candidate))

    def may_enter(progress, row, col, arrival):
        # Before a checkpoint window opens the robot must not enter its
        # tolerance region, unless it stops in a legal waiting cell there. This
        # prevents slipping past a checkpoint early and counting from the edge.
        if progress >= len(specs):
            return True
        spec = specs[progress]
        inside = (
            abs(row - spec["row"]) <= spec["tolerance_cells"]
            and abs(col - spec["col"]) <= spec["tolerance_cells"]
        )
        if not inside or arrival >= spec["window_start"] - 1e-9:
            return True
        return (row, col) in wait_cells

    while heap:
        time, key = heapq.heappop(heap)
        if times[key] < time - 1e-9:
            continue
        progress, layer, row, col = key
        if progress == len(specs) and (layer, row, col) == (
            end_layer, end_row, end_col
        ):
            # The robot stops here forever, so the end pose must also survive
            # every closure that has not fully passed yet.
            if all(
                zone["close_end"] <= time + EPS or zone["free"][layer, row, col]
                for zone in closures
            ):
                goal_key = key
                break
            continue
        expansions += 1
        if expansions > search_budget:
            completed = False
            break

        next_spec = specs[progress] if progress < len(specs) else None

        # Entering the next checkpoint's tolerance region is allowed early only
        # when this exact cell is a legal waiting position; otherwise the robot
        # would overshoot the checkpoint before its window opens.
        if (
            next_spec is not None
            and time < next_spec["window_start"] - 1e-9
            and abs(row - next_spec["row"]) <= next_spec["tolerance_cells"]
            and abs(col - next_spec["col"]) <= next_spec["tolerance_cells"]
            and (row, col) not in wait_cells
        ):
            continue

        # Once the next checkpoint window has closed this branch is dead.
        if progress < len(specs) and time > specs[progress]["window_end"] + 1e-9:
            continue

        # In a legal waiting area the robot may hold this pose until the next
        # window opens or a blocking closure ends, and then depart. Generate
        # the delayed departures explicitly: a plain self-edge would be
        # dominated by the earlier arrival label. Waiting in a corridor is
        # impossible because only declared waiting cells receive these edges,
        # and a waiting spot inside an active closure is no shelter either.
        if (row, col) in wait_cells:
            resume_candidates: list[float] = []
            if (
                progress < len(specs)
                and time < specs[progress]["window_start"] - EPS
            ):
                resume_candidates.append(specs[progress]["window_start"])
            for zone in closures:
                # Any closure that has not fully ended may force a stop here:
                # reaching its edge later without a waiting cell is a dead
                # end, so the safe stop must be offered upstream already.
                if zone["close_end"] > time + EPS:
                    resume_candidates.append(zone["close_end"])
            for resume in resume_candidates:
                # The whole stop must keep the body outside every active
                # closure for its full duration.
                if not pose_clear_time(layer, row, col, time, resume):
                    continue
                if not pose_clear_instant(layer, row, col, resume):
                    continue
                for move_index, (dr, dc) in enumerate(moves):
                    next_row, next_col = row + dr, col + dc
                    if not (0 <= next_row < rows and 0 <= next_col < cols):
                        continue
                    if not can_translate[move_index][layer, row, col]:
                        continue
                    arrival = resume + move_cost
                    if not move_clear_time(move_index, layer, row, col, resume, arrival):
                        continue
                    if not pose_clear_instant(layer, next_row, next_col, arrival):
                        continue
                    next_progress = _checkpoint_progress(
                        progress, layer, next_row, next_col, specs, arrival
                    )
                    if not may_enter(progress, next_row, next_col, arrival):
                        continue
                    relax(
                        (next_progress, layer, next_row, next_col),
                        arrival,
                        key,
                        "wait_move",
                        resume,
                    )
                for next_layer in (
                    (layer - 1) % ORIENTATION_STEPS,
                    (layer + 1) % ORIENTATION_STEPS,
                ):
                    if not sweep[next_layer, row, col]:
                        continue
                    arrival = resume + turn_cost
                    if not turn_clear_time(next_layer, row, col, resume, arrival):
                        continue
                    if not pose_clear_instant(next_layer, row, col, arrival):
                        continue
                    next_progress = _checkpoint_progress(
                        progress, next_layer, row, col, specs, arrival
                    )
                    if not may_enter(progress, row, col, arrival):
                        continue
                    relax(
                        (next_progress, next_layer, row, col),
                        arrival,
                        key,
                        "wait_turn",
                        resume,
                    )
                # Record the delayed label at the same pose regardless of
                # whether the checkpoint is crossed on resume: the robot may
                # hold any legal waiting cell, and reconstruction keeps it.
                resume_progress = _checkpoint_progress(
                    progress, layer, row, col, specs, resume
                )
                relax(
                    (resume_progress, layer, row, col), resume, key, "wait_here", resume
                )

        for move_index, (dr, dc) in enumerate(moves):
            next_row, next_col = row + dr, col + dc
            if not (0 <= next_row < rows and 0 <= next_col < cols):
                continue
            if not can_translate[move_index][layer, row, col]:
                continue
            arrival = time + move_cost
            if not move_clear_time(move_index, layer, row, col, time, arrival):
                continue
            if not pose_clear_instant(layer, next_row, next_col, arrival):
                continue
            next_progress = _checkpoint_progress(
                progress, layer, next_row, next_col, specs, arrival
            )
            if not may_enter(progress, next_row, next_col, arrival):
                continue
            if (
                next_progress == progress
                and progress < len(specs)
                and arrival > specs[progress]["window_end"] + 1e-9
            ):
                continue
            relax((next_progress, layer, next_row, next_col), arrival, key, "move")

        for next_layer in (
            (layer - 1) % ORIENTATION_STEPS,
            (layer + 1) % ORIENTATION_STEPS,
        ):
            # An in-place turn is legal only when the full angular sweep of the
            # rectangle is collision free.
            if not sweep[next_layer, row, col]:
                continue
            arrival = time + turn_cost
            if not turn_clear_time(next_layer, row, col, time, arrival):
                continue
            if not pose_clear_instant(next_layer, row, col, arrival):
                continue
            next_progress = _checkpoint_progress(
                progress, next_layer, row, col, specs, arrival
            )
            if not may_enter(progress, row, col, arrival):
                continue
            if (
                next_progress == progress
                and progress < len(specs)
                and arrival > specs[progress]["window_end"] + 1e-9
            ):
                continue
            relax((next_progress, next_layer, row, col), arrival, key, "turn")

    common = {
        "algorithm": ALGORITHM_VERSION,
        "path": geometric_path,
        "angular_resolution": angle_step,
    }
    if not completed:
        return {
            **common,
            "passable": False,
            "status": "search_incomplete",
            "reason": "search budget exhausted before the timed plan was proved feasible or infeasible",
            "schedule": [],
            "plans": [],
            "search_completed": False,
        }

    if goal_key is None:
        diagnosis = _diagnose_timed_failure(
            sweep=sweep,
            can_translate=can_translate,
            specs=specs,
            start_layer=start_layer,
            start_row=start_row,
            start_col=start_col,
        )
        return {
            **common,
            "passable": False,
            "status": diagnosis["status"],
            "reason": diagnosis["reason"],
            "schedule": [],
            "plans": [],
            "search_completed": True,
        }

    schedule = _reconstruct_schedule(
        goal_key, parents, orientations, resolution, rows, map_origin, specs
    )
    segments = _build_segments(schedule)
    plan = {
        "id": 0,
        "label": "最早到达方案",
        "feasible": True,
        "arrival_time": schedule[-1]["time"],
        "schedule": schedule,
        "segments": segments,
        "waiting_cells": sorted(
            {
                (item["row"], item["col"])
        for item in schedule
                if item["event"].startswith("wait")
            }
        ),
    }
    return {
        **common,
        "passable": True,
        "status": "feasible",
        "reason": "all ordered checkpoints are reached inside their time windows with the required orientation",
        "schedule": schedule,
        "segments": segments,
        "plans": [plan],
        "search_completed": True,
    }


def _diagnose_timed_failure(
    *,
    sweep: np.ndarray,
    can_translate: tuple[np.ndarray, ...],
    specs: list[dict[str, Any]],
    start_layer: int,
    start_row: int,
    start_col: int,
) -> dict[str, str]:
    """Distinguish an unreachable pose (orientation/geometry) from timing."""

    rows, cols = sweep.shape[1], sweep.shape[2]
    moves = ((-1, 0), (1, 0), (0, -1), (0, 1))
    seen: set[tuple[int, int, int, int]] = set()
    start_progress = 0
    start = (start_progress, start_layer, start_row, start_col)
    seen.add(start)
    queue = [start]
    head = 0
    while head < len(queue):
        progress, layer, row, col = queue[head]
        head += 1
        candidates: list[tuple[int, int, int]] = []
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
            next_progress = _checkpoint_progress(
                progress, cand_layer, cand_row, cand_col, specs, 0.0
            )
            candidate = (next_progress, cand_layer, cand_row, cand_col)
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)

    for index, spec in enumerate(specs):
        row_lo = max(0, spec["row"] - spec["tolerance_cells"])
        row_hi = min(rows, spec["row"] + spec["tolerance_cells"] + 1)
        col_lo = max(0, spec["col"] - spec["tolerance_cells"])
        col_hi = min(cols, spec["col"] + spec["tolerance_cells"] + 1)

        def reachable_in(layers) -> bool:
            for layer in layers:
                for row in range(row_lo, row_hi):
                    for col in range(col_lo, col_hi):
                        for progress in range(len(specs) + 1):
                            # Entering the region already advances the
                            # checkpoint progress, so a passed checkpoint
                            # shows up under a later progress label.
                            if (progress, layer, row, col) in seen:
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
    return {
        "status": "window_missed",
        "reason": (
            "the checkpoint pose is geometrically reachable, but no allowed waiting "
            "area lets the robot pass inside every ordered time window"
        ),
    }


def _reconstruct_schedule(
    goal_key: tuple[int, int, int, int],
    parents: dict[tuple[int, int, int, int], tuple[Any, str, float]],
    orientations: np.ndarray,
    resolution: float,
    rows: int,
    map_origin: Point,
    specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # Walk the parent chain once so waits can be split into a stop at the
    # waiting pose followed by the delayed departure action.
    chain: list[tuple[Any, Any, str, float, float | None]] = []
    key: Any = goal_key
    while key is not None:
        parent, action, time, wait_resume = parents[key]
        chain.append((key, parent, action, time, wait_resume))
        key = parent
    chain.reverse()

    events: list[dict[str, Any]] = []
    previous_progress = 0
    for index, (key, parent, action, time, wait_resume) in enumerate(chain):
        progress, layer, row, col = key
        point = grid_to_world(row, col, resolution, rows, map_origin)
        if action.startswith("wait") and parent is not None:
            _, parent_layer, parent_row, parent_col = parent
            wait_point = grid_to_world(parent_row, parent_col, resolution, rows, map_origin)
            # The stop happens at the waiting pose; only then does the delayed
            # action depart, so a replay can hold the robot inside the area.
            events.append(
                {
                    "event": "wait",
                    "time": events[-1]["time"] if events else time,
                    "wait_until": wait_resume,
                    "wait_action": action,
                    "x": wait_point.x,
                    "y": wait_point.y,
                    "orientation": float(orientations[parent_layer]),
                    "row": parent_row,
                    "col": parent_col,
                    "checkpoint": previous_progress,
                }
            )
            action = {"wait_move": "move", "wait_turn": "turn", "wait_here": "hold"}[action]
        events.append(
            {
                "event": action,
                "time": time,
                "x": point.x,
                "y": point.y,
                "orientation": float(orientations[layer]),
                "row": row,
                "col": col,
                "checkpoint": progress,
            }
        )
        if progress > previous_progress:
            # Record exactly which ordered checkpoints this state passed so the
            # conclusion can be replayed checkpoint by checkpoint.
            events[-1]["checkpoints_passed"] = [
                specs[step]["order"] for step in range(previous_progress, progress)
            ]
        previous_progress = progress
    return events


def _build_segments(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compress the event stream into consecutive replay segments.

    Each segment is one atomic phase (move / turn / wait / hold) with its own
    time interval and endpoint poses, so a feasible answer can be replayed
    segment by segment instead of trusting the final conclusion.
    """
    segments: list[dict[str, Any]] = []
    for index in range(1, len(events)):
        previous = events[index - 1]
        current = events[index]
        if current["event"] == "wait":
            action = "wait"
            t_end = current["wait_until"]
        else:
            action = current["event"]
            t_end = current["time"]
        if current["event"] != "wait" and previous["event"] == "wait":
            # The departure after a stop leaves at the resume time, not at
            # the moment the robot arrived at the waiting pose.
            t_start = previous["wait_until"]
        elif current["event"] == "wait":
            t_start = current["time"]
        else:
            t_start = previous["time"]
        segment = {
            "seq": len(segments),
            "action": action,
            "t_start": t_start,
            "t_end": t_end,
            "from": {
                "x": previous["x"],
                "y": previous["y"],
                "orientation": previous["orientation"],
            },
            "to": {
                "x": current["x"],
                "y": current["y"],
                "orientation": current["orientation"],
            },
        }
        if current["event"] == "wait":
            segment["to"] = segment["from"]
        if "checkpoints_passed" in current:
            segment["checkpoints_passed"] = current["checkpoints_passed"]
        segments.append(segment)
    return segments


def _schedule_fixed_path(
    *,
    path: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    closures: list[dict[str, Any]],
    free: np.ndarray,
    sweep: np.ndarray,
    can_translate: tuple[np.ndarray, ...],
    wait_cells: set[tuple[int, int]],
    start_layer: int,
    end_layer: int,
    orientations: np.ndarray,
    resolution: float,
    rows: int,
    map_origin: Point,
    linear_speed: float,
    angular_speed: float,
    start_time: float,
) -> dict[str, Any]:
    """Reschedule a previously found geometry under new windows/closures.

    The route is fixed; only departure times and waiting stops are chosen.
    Failure separates a pose that no longer fits (``pose_mismatch``) from a
    route that simply cannot be timed any more (``unschedulable``).
    """
    cols = free.shape[2]
    angle_step = 2.0 * math.pi / ORIENTATION_STEPS
    move_cost = resolution / linear_speed
    turn_cost = angle_step / angular_speed

    def fail(status: str, reason: str) -> dict[str, Any]:
        return {
            "passable": False,
            "status": status,
            "reason": reason,
            "algorithm": ALGORITHM_VERSION,
            "path": path,
            "schedule": [],
            "segments": [],
            "plans": [],
            "angular_resolution": angle_step,
            "search_completed": True,
            "geometry_kept": True,
        }

    nodes: list[tuple[int, int, int]] = []
    for node in path:
        row, col = world_to_grid(
            Point(float(node["x"]), float(node["y"])), resolution, rows, map_origin
        )
        layer = nearest_orientation_layer(float(node.get("orientation", 0.0)))
        nodes.append((layer, row, col))
    if not nodes:
        return fail("pose_mismatch", "the kept geometric path is empty")
    if nodes[0][0] != start_layer or nodes[-1][0] != end_layer:
        return fail(
            "pose_mismatch",
            "the kept geometric path does not connect the required start and end poses",
        )

    for index, (layer, row, col) in enumerate(nodes):
        if not (0 <= row < rows and 0 <= col < cols) or not free[layer, row, col]:
            return fail(
                "pose_mismatch",
                f"kept path pose {index} collides with an obstacle for the complete rectangle footprint",
            )

    # Steps between consecutive nodes: in-place turns layer by layer, or
    # single-cell translations. Anything else cannot be replayed as-is.
    steps: list[tuple[str, float, int | None, int]] = []
    for index in range(1, len(nodes)):
        layer0, row0, col0 = nodes[index - 1]
        layer1, row1, col1 = nodes[index]
        if (row0, col0) == (row1, col1) and layer0 == layer1:
            continue
        if (row0, col0) == (row1, col1):
            clockwise = (layer1 - layer0) % ORIENTATION_STEPS
            counter = (layer0 - layer1) % ORIENTATION_STEPS
            count = min(clockwise, counter)
            direction = 1 if clockwise <= counter else -1
            layer = layer0
            for _ in range(count):
                nxt = (layer + direction) % ORIENTATION_STEPS
                if not sweep[nxt, row0, col0]:
                    return fail(
                        "pose_mismatch",
                        f"kept path turn at node {index} sweeps the rectangle through an obstacle",
                    )
                layer = nxt
            steps.append(("turn", count * turn_cost, None, index))
        elif layer0 == layer1 and abs(row1 - row0) + abs(col1 - col0) == 1:
            move_index = MOVES.index((row1 - row0, col1 - col0))
            if not can_translate[move_index][layer0, row0, col0]:
                return fail(
                    "pose_mismatch",
                    f"kept path move at node {index} collides for the complete rectangle footprint",
                )
            steps.append(("move", move_cost, move_index, index))
        else:
            return fail(
                "pose_mismatch",
                "the kept geometric path is not replayable cell by cell with the required poses",
            )

    def pose_event(action: str, time: float, node_index: int, progress: int) -> dict[str, Any]:
        layer, row, col = nodes[node_index]
        point = grid_to_world(row, col, resolution, rows, map_origin)
        return {
            "event": action,
            "time": time,
            "x": point.x,
            "y": point.y,
            "orientation": float(orientations[layer]),
            "row": row,
            "col": col,
            "checkpoint": progress,
        }

    progress = _checkpoint_progress(
        0, nodes[0][0], nodes[0][1], nodes[0][2], specs, float(start_time)
    )
    for zone in closures:
        if closed_at(zone["close_start"], zone["close_end"], float(start_time)) and not zone[
            "free"
        ][nodes[0]]:
            return fail(
                "unschedulable",
                "the kept geometry starts inside a region that is closed at the departure time",
            )

    events = [pose_event("start", float(start_time), 0, progress)]
    time = float(start_time)
    node_index = 0
    for action, duration, move_index, target_index in steps:
        layer, row, col = nodes[node_index]
        target_layer, target_row, target_col = nodes[target_index]
        while True:
            arrival = time + duration
            resume: float | None = None
            for zone in closures:
                if not intervals_overlap(
                    time, arrival, zone["close_start"], zone["close_end"]
                ):
                    continue
                if action == "move":
                    clear = bool(zone["moves"][move_index][layer, row, col])
                else:
                    clear = bool(zone["sweep"][target_layer, row, col])
                if not clear:
                    resume = zone["close_end"] if resume is None else max(resume, zone["close_end"])
                # A pose held exactly at close_start counts as still inside.
                if closed_at(zone["close_start"], zone["close_end"], arrival) and not zone[
                    "free"
                ][target_layer, target_row, target_col]:
                    resume = (
                        zone["close_end"]
                        if resume is None
                        else max(resume, zone["close_end"])
                    )
            if progress < len(specs):
                spec = specs[progress]
                # Entering the next checkpoint tolerance region before its
                # window opens means the kept route arrives too early.
                if (
                    abs(target_row - spec["row"]) <= spec["tolerance_cells"]
                    and abs(target_col - spec["col"]) <= spec["tolerance_cells"]
                    and target_layer in spec["layers"]
                    and arrival < spec["window_start"] - EPS
                ):
                    resume = (
                        spec["window_start"]
                        if resume is None
                        else max(resume, spec["window_start"])
                    )
            if resume is None:
                break
            if (row, col) not in wait_cells:
                return fail(
                    "unschedulable",
                    "the kept geometry needs a stop outside every declared waiting area",
                )
            shelter = all(
                not intervals_overlap(time, resume, zone["close_start"], zone["close_end"])
                or bool(zone["free"][layer, row, col])
                for zone in closures
            )
            if not shelter:
                return fail(
                    "unschedulable",
                    "the only waiting position on the kept geometry is itself closed during the wait",
                )
            wait_event = pose_event("wait", time, node_index, progress)
            wait_event["wait_until"] = resume
            events.append(wait_event)
            time = resume
        time = arrival
        progress = _checkpoint_progress(
            progress, target_layer, target_row, target_col, specs, time
        )
        events.append(pose_event(action, time, target_index, progress))
        node_index = target_index
        if progress < len(specs) and time > specs[progress]["window_end"] + EPS:
            return fail(
                "unschedulable",
                "a checkpoint window closes before the kept geometry reaches it",
            )

    if progress < len(specs):
        return fail(
            "unschedulable",
            "the kept geometry cannot pass every ordered checkpoint inside its window",
        )
    end_layer_actual, end_row, end_col = nodes[-1]
    for zone in closures:
        if zone["close_end"] > time + EPS and not zone["free"][end_layer_actual, end_row, end_col]:
            return fail(
                "unschedulable",
                "the kept geometry ends inside a region that closes after the arrival time",
            )

    segments = _build_segments(events)
    plan = {
        "id": 0,
        "label": "保留几何重排时间",
        "feasible": True,
        "arrival_time": events[-1]["time"],
        "schedule": events,
        "segments": segments,
        "waiting_cells": sorted(
            {(event["row"], event["col"]) for event in events if event["event"] == "wait"}
        ),
    }
    return {
        "passable": True,
        "status": "feasible",
        "reason": (
            "kept the previous geometry and rescheduled arrivals, turns and "
            "waits under the new checkpoint windows and closures"
        ),
        "algorithm": ALGORITHM_VERSION,
        "path": path,
        "schedule": events,
        "segments": segments,
        "plans": [plan],
        "angular_resolution": angle_step,
        "search_completed": True,
        "geometry_kept": True,
    }
