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
    linear_speed: float = 0.5,
    angular_speed: float = 0.5,
    start_time: float = 0.0,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
    forbidden_wait_cells: list[tuple[int, int]] | None = None,
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

    if not checkpoints:
        geometric["status"] = "feasible"
        geometric["search_completed"] = True
        return geometric

    ordered = sorted(checkpoints, key=lambda item: int(item["order"]))
    checkpoint_specs = []
    for item in ordered:
        point = Point(float(item["x"]), float(item["y"]))
        cp_row, cp_col = world_to_grid(point, resolution, rows, map_origin)
        if not (0 <= cp_row < rows and 0 <= cp_col < cols):
            raise ValueError("checkpoint is outside the map")
        tolerance = float(item.get("tolerance", max(resolution, 1.5 * resolution)))
        tolerance_cells = max(1, int(round(tolerance / resolution)))
        wanted_layer = nearest_orientation_layer(float(item["orientation"]))
        orient_tolerance = float(item.get("orientation_tolerance", angular_resolution / 2.0))
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
            goal_key = key
            break
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
        # window opens and then depart. Generate the delayed departures
        # explicitly: a plain self-edge would be dominated by the earlier
        # arrival label. Waiting in a corridor is impossible because only
        # declared waiting cells receive these edges.
        if (
            progress < len(specs)
            and (row, col) in wait_cells
            and time < specs[progress]["window_start"]
        ):
            resume = specs[progress]["window_start"]
            for move_index, (dr, dc) in enumerate(moves):
                next_row, next_col = row + dr, col + dc
                if not (0 <= next_row < rows and 0 <= next_col < cols):
                    continue
                if not can_translate[move_index][layer, row, col]:
                    continue
            arrival = resume + move_cost
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
                next_progress = _checkpoint_progress(
                    progress, next_layer, row, col, specs, arrival
                )
                if not may_enter(progress, row, col, arrival):
                    continue
                relax(
                    (next_progress, next_layer, row, col), arrival, key, "wait_turn", resume
                )
            # Record the delayed label at the same pose regardless of whether
            # the checkpoint is crossed on resume: the robot may hold any legal
            # waiting cell, and reconstruction must keep the stop.
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
        goal_key, parents, orientations, resolution, rows, map_origin
    )
    plan = {
        "id": 0,
        "label": "最早到达方案",
        "feasible": True,
        "arrival_time": schedule[-1]["time"],
        "schedule": schedule,
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
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    key: Any = goal_key
    while key is not None:
        parent, action, time, wait_resume = parents[key]
        progress, layer, row, col = key
        point = grid_to_world(row, col, resolution, rows, map_origin)
        events.append(
            {
                "event": action,
                "time": time,
                "wait_resume": wait_resume,
                "x": point.x,
                "y": point.y,
                "orientation": float(orientations[layer]),
                "row": row,
                "col": col,
                "checkpoint": progress,
            }
        )
        key = parent
    events.reverse()

    # A wait self-edge stores the resume time; surface both the start and end
    # of the stop so callers can verify the robot stays inside the waiting area.
    for index, event in enumerate(events):
        if event["event"].startswith("wait"):
            resume = event.pop("wait_resume")
            previous_time = events[index - 1]["time"] if index else resume
            action = event["event"]
            event["event"] = "wait"
            event["time"] = previous_time
            event["wait_until"] = resume
            event["wait_action"] = action
        else:
            event.pop("wait_resume", None)
    return events
