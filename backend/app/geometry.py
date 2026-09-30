from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


ALGORITHM_VERSION = "pose-checkpoint-bfs-v3"

# 10 deg per configuration layer. Each layer conservatively covers the whole
# angular interval around its center, so changing between adjacent layers can
# never skip an intermediate heading.
ORIENTATION_STEPS = 36
ANGLE_SUBDIVISIONS = 5


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
    """Conservative raster footprint for every heading in an angle interval."""

    corner_radius = 0.5 * math.hypot(robot_width, robot_length)
    patch_radius = int(math.ceil(corner_radius / resolution)) + 1
    offsets = np.arange(-patch_radius, patch_radius + 1)
    local_x, local_y = np.meshgrid(offsets * resolution, offsets * resolution)
    hit = np.zeros_like(local_x, dtype=bool)

    # Inflation between sampled headings makes the angular sweep conservative
    # instead of trusting a finite set of displayed poses.
    sample_step = (theta_end - theta_start) / max(1, ANGLE_SUBDIVISIONS - 1)
    angular_slack = corner_radius * math.sin(sample_step / 2.0)
    half_length = robot_length / 2.0 + angular_slack
    half_width = robot_width / 2.0 + angular_slack

    for sample in range(ANGLE_SUBDIVISIONS):
        theta = theta_start + sample_step * sample
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        along = local_x * cos_theta + local_y * sin_theta
        across = -local_x * sin_theta + local_y * cos_theta
        # Cell-center test plus half a cell covers every overlapping cell.
        cell_slack = resolution * 0.5
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
) -> tuple[np.ndarray, np.ndarray]:
    angle_step = 2.0 * math.pi / ORIENTATION_STEPS
    layers = np.zeros((ORIENTATION_STEPS, *occupancy.shape), dtype=bool)
    orientations = np.arange(ORIENTATION_STEPS, dtype=float) * angle_step
    for layer, center_angle in enumerate(orientations):
        mask = _footprint_mask(
            float(center_angle - angle_step / 2.0),
            float(center_angle + angle_step / 2.0),
            robot_width,
            robot_length,
            resolution,
        )
        layers[layer] = _free_centers_for_mask(occupancy, mask)
    return layers, orientations


def normalize_orientation(orientation: float) -> float:
    wrapped = float(orientation) % (2.0 * math.pi)
    if wrapped < 0:
        wrapped += 2.0 * math.pi
    return wrapped


def nearest_orientation_layer(orientation: float) -> int:
    layer = round(normalize_orientation(orientation) / (2.0 * math.pi) * ORIENTATION_STEPS)
    return int(layer) % ORIENTATION_STEPS


def _encode(layer: int, row: int, col: int, rows: int, cols: int) -> int:
    return (layer * rows + row) * cols + col


def _decode(index: int, rows: int, cols: int) -> tuple[int, int, int]:
    layer, rem = divmod(index, rows * cols)
    row, col = divmod(rem, cols)
    return layer, row, col


def _bfs(
    free: np.ndarray,
    sources: list[tuple[int, int, int]],
    goal_row: int,
    goal_col: int,
    goal_layer: int | None,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int] | None]:
    """Layer/cell BFS.

    A layer edge is an in-place rotation: it is only open when the center is
    free in that orientation layer, so the full angular sweep is collision
    checked instead of treating the robot as a stationary point.
    goal_layer=None accepts every orientation at the goal cell.
    """

    layers, rows, cols = free.shape
    size = layers * rows * cols
    dist = np.full(size, -1, dtype=np.int32)
    previous = np.full(size, -1, dtype=np.int32)
    queue: list[int] = []
    for layer, row, col in sources:
        index = _encode(layer, row, col, rows, cols)
        if dist[index] == -1 and free[layer, row, col]:
            dist[index] = 0
            queue.append(index)

    target = None
    head = 0
    while head < len(queue):
        index = queue[head]
        head += 1
        layer, row, col = _decode(index, rows, cols)
        if row == goal_row and col == goal_col and (goal_layer is None or layer == goal_layer):
            target = (layer, row, col)
            break
        for next_layer, next_row, next_col in (
            (layer, row - 1, col),
            (layer, row + 1, col),
            (layer, row, col - 1),
            (layer, row, col + 1),
            ((layer - 1) % layers, row, col),
            ((layer + 1) % layers, row, col),
        ):
            if not (0 <= next_row < rows and 0 <= next_col < cols):
                continue
            next_index = _encode(next_layer, next_row, next_col, rows, cols)
            if not free[next_layer, next_row, next_col] or dist[next_index] != -1:
                continue
            dist[next_index] = dist[index] + 1
            previous[next_index] = index
            queue.append(next_index)
    return dist, previous, target


def _reconstruct(
    previous: np.ndarray,
    target: tuple[int, int, int],
    rows: int,
    cols: int,
) -> list[tuple[int, int, int]]:
    nodes = []
    current: int | None = _encode(*target, rows, cols)
    while current is not None and current != -1:
        nodes.append(_decode(current, rows, cols))
        current = int(previous[current]) if previous[current] != -1 else None
    nodes.reverse()
    return nodes


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
) -> dict[str, Any]:
    if robot_width <= 0 or robot_length <= 0:
        raise ValueError("robot dimensions must be positive")
    if resolution <= 0:
        raise ValueError("resolution must be positive")

    map_origin = Point(origin["x"], origin["y"])
    occupancy = build_occupancy(base_grid, forbidden_zones, resolution, map_origin)
    rows, cols = occupancy.shape

    def cell_of(pose: dict[str, float], label: str) -> tuple[int, int]:
        row, col = world_to_grid(Point(pose["x"], pose["y"]), resolution, rows, map_origin)
        if not (0 <= row < rows and 0 <= col < cols):
            raise ValueError(f"{label} is outside the map")
        return row, col

    start_row, start_col = cell_of(start, "start")
    end_row, end_col = cell_of(end, "end")
    checkpoint_specs: list[tuple[int, int, int | None]] = []
    for index, checkpoint in enumerate(checkpoints or []):
        cp_row, cp_col = cell_of(checkpoint, f"checkpoint {index + 1}")
        cp_layer = (
            nearest_orientation_layer(float(checkpoint["orientation"]))
            if checkpoint.get("orientation") is not None
            else None
        )
        checkpoint_specs.append((cp_row, cp_col, cp_layer))

    free, orientations = build_orientation_layers(occupancy, robot_width, robot_length, resolution)
    angular_resolution = 2.0 * math.pi / ORIENTATION_STEPS
    start_layer = nearest_orientation_layer(start_orientation)
    end_layer = None if end_orientation is None else nearest_orientation_layer(end_orientation)

    def failure(status: str, reason: str, stage: str) -> dict[str, Any]:
        return {
            "passable": False,
            "status": status,
            "reason": reason,
            "failed_stage": stage,
            "algorithm": ALGORITHM_VERSION,
            "path": [],
            "angular_resolution": angular_resolution,
        }

    if not free[start_layer, start_row, start_col]:
        return failure(
            "start-invalid",
            "start cannot contain the complete robot footprint at the start heading",
            "start",
        )

    stages = [
        (row_, col_, layer_, label)
        for (row_, col_, layer_), label in zip(
            checkpoint_specs + [(end_row, end_col, end_layer)],
            [f"checkpoint {i + 1}" for i in range(len(checkpoint_specs))] + ["end"],
            strict=True,
        )
    ]

    sources: list[tuple[int, int, int]] = [(start_layer, start_row, start_col)]
    pose_nodes: list[tuple[int, int, int]] = []
    for goal_row, goal_col, goal_layer, label in stages:
        # The passage itself is impossible when no orientation can ever place
        # the complete rectangle at a required point.
        if not bool(np.any(free[:, goal_row, goal_col])):
            return failure(
                "passage-blocked",
                f"{label} cannot contain the complete rectangle footprint in any heading; the passage is too narrow",
                label,
            )
        dist, previous, target = _bfs(free, sources, goal_row, goal_col, goal_layer)
        if target is None:
            reachable = False
            for layer in range(ORIENTATION_STEPS):
                index = (layer * rows + goal_row) * cols + goal_col
                if dist[index] != -1:
                    reachable = True
                    break
            if reachable:
                return failure(
                    "orientation-unreachable",
                    f"{label} is reachable but the robot cannot attain the required heading there",
                    label,
                )
            return failure(
                "route-not-found",
                f"no collision-free trajectory reaches {label} with the complete rectangle footprint",
                label,
            )
        segment = _reconstruct(previous, target, rows, cols)
        if pose_nodes:
            segment = segment[1:]
        pose_nodes.extend(segment)
        if goal_layer is None:
            # A position-only checkpoint prescribes no heading, so every
            # orientation reached at its cell remains available afterwards.
            sources = [
                (layer, goal_row, goal_col)
                for layer in range(ORIENTATION_STEPS)
                if dist[(layer * rows + goal_row) * cols + goal_col] != -1
            ]
        else:
            sources = [target]

    nodes = []
    for layer, row, col in pose_nodes:
        point = grid_to_world(row, col, resolution, rows, map_origin)
        nodes.append({"x": point.x, "y": point.y, "orientation": float(orientations[layer])})
    return {
        "passable": True,
        "status": "passable",
        "failed_stage": None,
        "reason": "collision-free passage reaches every required pose (position and heading) with the complete rectangle footprint",
        "algorithm": ALGORITHM_VERSION,
        "path": nodes,
        "angular_resolution": angular_resolution,
    }
