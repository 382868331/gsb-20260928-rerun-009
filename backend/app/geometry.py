from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


ALGORITHM_VERSION = "continuous-orientation-footprint-bfs-v3"

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
    # instead of trusting a finite set of displayed poses. A zero-width
    # interval (theta_start == theta_end) yields the exact single-heading
    # footprint with no angular inflation.
    sample_step = (theta_end - theta_start) / max(1, ANGLE_SUBDIVISIONS - 1)
    angular_slack = corner_radius * math.sin(sample_step / 2.0)
    half_length = robot_length / 2.0 + angular_slack
    half_width = robot_width / 2.0 + angular_slack

    for sample in range(ANGLE_SUBDIVISIONS):
        theta = theta_start + sample_step * sample
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        along = local_x * sin_theta + local_y * cos_theta
        across = local_x * cos_theta - local_y * sin_theta
        # Cell-center containment test: a cell belongs to the footprint when
        # the rectangle covers the cell center. This keeps doors that exactly
        # fit the robot body passable instead of demanding phantom clearance.
        cell_slack = 0.0
        hit |= (np.abs(along) < half_length + cell_slack) & (
            np.abs(across) < half_width + cell_slack
        )

    return hit


def _dilate_mask(mask: np.ndarray, axis: int) -> np.ndarray:
    """One-cell dilation along a single axis, covering a one-cell translation
    sweep in that direction without inflating perpendicular clearance."""

    dilated = mask.copy()
    if axis == 0:
        dilated[1:, :] |= mask[:-1, :]
        dilated[:-1, :] |= mask[1:, :]
    else:
        dilated[:, 1:] |= mask[:, :-1]
        dilated[:, :-1] |= mask[:, 1:]
    return dilated


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
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    angle_step = 2.0 * math.pi / ORIENTATION_STEPS
    pose = np.zeros((ORIENTATION_STEPS, *occupancy.shape), dtype=bool)
    pose_x = np.zeros_like(pose)
    pose_y = np.zeros_like(pose)
    sweep = np.zeros_like(pose)
    orientations = np.arange(ORIENTATION_STEPS, dtype=float) * angle_step
    for layer, center_angle in enumerate(orientations):
        # Exact footprint at the layer-center heading: translations are
        # executed at a fixed heading, so no angular inflation is needed and
        # an exactly fitting door stays passable.
        center_mask = _footprint_mask(
            float(center_angle),
            float(center_angle),
            robot_width,
            robot_length,
            resolution,
        )
        pose[layer] = _free_centers_for_mask(occupancy, center_mask)
        # Translation sweeps are checked per movement direction: sliding one
        # cell horizontally only sweeps the footprint along x, so the
        # perpendicular clearance of the base mask stays exact.
        pose_x[layer] = _free_centers_for_mask(occupancy, _dilate_mask(center_mask, axis=1))
        pose_y[layer] = _free_centers_for_mask(occupancy, _dilate_mask(center_mask, axis=0))
        # Conservative whole-interval footprint used to validate in-place
        # rotation between adjacent layers.
        sweep_mask = _footprint_mask(
            float(center_angle - angle_step / 2.0),
            float(center_angle + angle_step / 2.0),
            robot_width,
            robot_length,
            resolution,
        )
        sweep[layer] = _free_centers_for_mask(occupancy, sweep_mask)
    return pose, pose_x, pose_y, sweep, orientations


def normalize_orientation(orientation: float) -> float:
    wrapped = float(orientation) % (2.0 * math.pi)
    if wrapped < 0:
        wrapped += 2.0 * math.pi
    return wrapped


def nearest_orientation_layer(orientation: float) -> int:
    layer = round(normalize_orientation(orientation) / (2.0 * math.pi) * ORIENTATION_STEPS)
    return int(layer) % ORIENTATION_STEPS


def configuration_neighbors(
    layer: int,
    row: int,
    col: int,
    pose: np.ndarray,
    free_x: np.ndarray,
    free_y: np.ndarray,
    sweep: np.ndarray,
) -> list[tuple[int, int, int]]:
    """Collision-checked adjacent configurations.

    Horizontal slides require the x-swept layer, vertical slides the y-swept
    layer, and in-place rotation steps require the conservative sweep mask on
    both layers so the whole rotated range is collision-free.
    """

    rows, cols = pose.shape[1], pose.shape[2]
    moves = []
    if row > 0 and free_y[layer, row, col]:
        moves.append((layer, row - 1, col))
    if row + 1 < rows and free_y[layer, row, col]:
        moves.append((layer, row + 1, col))
    if col > 0 and free_x[layer, row, col]:
        moves.append((layer, row, col - 1))
    if col + 1 < cols and free_x[layer, row, col]:
        moves.append((layer, row, col + 1))
    if sweep[layer, row, col]:
        for delta in (-1, 1):
            next_layer = (layer + delta) % ORIENTATION_STEPS
            if sweep[next_layer, row, col]:
                moves.append((next_layer, row, col))
    return moves


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
) -> dict[str, Any]:
    if robot_width <= 0 or robot_length <= 0:
        raise ValueError("robot dimensions must be positive")
    if resolution <= 0:
        raise ValueError("resolution must be positive")

    map_origin = Point(origin["x"], origin["y"])
    occupancy = build_occupancy(base_grid, forbidden_zones, resolution, map_origin)
    rows, cols = occupancy.shape
    start_row, start_col = world_to_grid(Point(start["x"], start["y"]), resolution, rows, map_origin)
    end_row, end_col = world_to_grid(Point(end["x"], end["y"]), resolution, rows, map_origin)
    if not (0 <= start_row < rows and 0 <= start_col < cols):
        raise ValueError("start is outside the map")
    if not (0 <= end_row < rows and 0 <= end_col < cols):
        raise ValueError("end is outside the map")

    pose, free_x, free_y, sweep, orientations = build_orientation_layers(
        occupancy, robot_width, robot_length, resolution
    )
    angular_resolution = 2.0 * math.pi / ORIENTATION_STEPS
    start_layer = nearest_orientation_layer(start_orientation)
    end_layer = nearest_orientation_layer(
        start_orientation if end_orientation is None else end_orientation
    )

    if not pose[start_layer, start_row, start_col]:
        return {
            "passable": False,
            "reason": "start pose collides: the complete rectangle footprint does not fit",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
            "angular_resolution": angular_resolution,
        }
    if not pose[end_layer, end_row, end_col]:
        same_spot = start_row == end_row and start_col == end_col
        if same_spot and start_layer != end_layer:
            reason = (
                "目标位置与起点相同但朝向不同：目标朝向下长方形外廓与障碍冲突，"
                "此处无法安全转向到该姿态"
            )
        else:
            reason = "end pose collides: the complete rectangle footprint does not fit"
        return {
            "passable": False,
            "reason": reason,
            "algorithm": ALGORITHM_VERSION,
            "path": [],
            "angular_resolution": angular_resolution,
        }

    dist = np.full((ORIENTATION_STEPS, rows, cols), -1, dtype=np.int32)
    previous: dict[tuple[int, int, int], tuple[int, int, int] | None] = {}
    queue: list[tuple[int, int, int]] = []
    dist[start_layer, start_row, start_col] = 0
    previous[(start_layer, start_row, start_col)] = None
    queue.append((start_layer, start_row, start_col))

    head = 0
    target = None
    while head < len(queue):
        layer, row, col = queue[head]
        head += 1
        if layer == end_layer and row == end_row and col == end_col:
            target = (layer, row, col)
            break
        for next_layer, next_row, next_col in configuration_neighbors(
            layer, row, col, pose, free_x, free_y, sweep
        ):
            if not (0 <= next_row < rows and 0 <= next_col < cols):
                continue
            if dist[next_layer, next_row, next_col] != -1:
                continue
            dist[next_layer, next_row, next_col] = dist[layer, row, col] + 1
            previous[(next_layer, next_row, next_col)] = (layer, row, col)
            queue.append((next_layer, next_row, next_col))

    if target is None:
        same_spot = start_row == end_row and start_col == end_col
        if same_spot and start_layer != end_layer:
            reason = (
                "目标位置与起点相同但朝向不同：在此处原地转向时，长方形机器人"
                "的旋转扫掠与障碍冲突，无法安全完成转向"
            )
        else:
            reason = (
                "no collision-free continuous-turn trajectory exists for the "
                "complete rectangle footprint"
            )
        return {
            "passable": False,
            "reason": reason,
            "algorithm": ALGORITHM_VERSION,
            "path": [],
            "angular_resolution": angular_resolution,
        }

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
        "reason": "collision-free continuous-turn passage exists for the complete rectangle footprint",
        "algorithm": ALGORITHM_VERSION,
        "path": nodes,
        "angular_resolution": angular_resolution,
    }
