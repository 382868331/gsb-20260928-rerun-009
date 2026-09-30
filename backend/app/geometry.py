from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


ALGORITHM_VERSION = "continuous-footprint-se2-bfs-v2"
HEADING_BINS = 72
ROTATION_SUBDIVISIONS = 3


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


def _rotated_rectangle_mask(
    robot_width: float, robot_length: float, heading: float, resolution: float
) -> np.ndarray:
    corners = np.array([
        [-robot_length / 2, -robot_width / 2],
        [robot_length / 2, -robot_width / 2],
        [robot_length / 2, robot_width / 2],
        [-robot_length / 2, robot_width / 2],
    ])
    cosine, sine = math.cos(heading), math.sin(heading)
    half_diagonal = math.hypot(robot_length, robot_width) / 2 + resolution
    extent = int(math.ceil(half_diagonal / resolution))
    axis = np.arange(-extent, extent + 1)
    cells_x, cells_y = np.meshgrid(axis, axis)
    world_x = cells_x * resolution
    world_y = -cells_y * resolution

    local_x = world_x * cosine + world_y * sine
    local_y = -world_x * sine + world_y * cosine
    mask = (
        (local_x >= -robot_length / 2 - resolution / 2)
        & (local_x <= robot_length / 2 + resolution / 2)
        & (local_y >= -robot_width / 2 - resolution / 2)
        & (local_y <= robot_width / 2 + resolution / 2)
    )
    rows = cells_y[mask]
    cols = cells_x[mask]
    return np.column_stack([rows, cols]).astype(np.int64)


class FootprintField:
    def __init__(self, occupancy: np.ndarray, robot_width: float, robot_length: float, resolution: float):
        self.occupancy = occupancy
        self.rows, self.cols = occupancy.shape
        self.robot_width = robot_width
        self.robot_length = robot_length
        self.resolution = resolution
        self.angle_step = 2.0 * math.pi / HEADING_BINS
        self.bin_offsets = [
            _rotated_rectangle_mask(robot_width, robot_length, layer * self.angle_step, resolution)
            for layer in range(HEADING_BINS)
        ]
        self.cache: dict[tuple[int, int], bool] = {}

    def offsets_clear(self, row: float, col: float, offsets: np.ndarray) -> bool:
        sample_rows = np.rint(row + offsets[:, 0]).astype(np.int64)
        sample_cols = np.rint(col + offsets[:, 1]).astype(np.int64)
        inside = (
            (sample_rows >= 0)
            & (sample_rows < self.rows)
            & (sample_cols >= 0)
            & (sample_cols < self.cols)
        )
        if not bool(inside.all()):
            return False
        return not bool(self.occupancy[sample_rows, sample_cols].any())

    def free_bin(self, layer: int, row: int, col: int) -> bool:
        key = (layer, row * self.cols + col)
        try:
            return self.cache[key]
        except KeyError:
            result = self.offsets_clear(row, col, self.bin_offsets[layer])
            self.cache[key] = result
            return result

    def heading_clear(self, row: float, col: float, heading: float) -> bool:
        offsets = _rotated_rectangle_mask(
            self.robot_width, self.robot_length, heading, self.resolution
        )
        return self.offsets_clear(row, col, offsets)


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
    end_orientation: float = 0.0,
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

    field = FootprintField(occupancy, robot_width, robot_length, resolution)
    angle_step = 2.0 * math.pi / HEADING_BINS

    def nearest_layer(degrees: float) -> int:
        radians = math.radians(degrees) % (2.0 * math.pi)
        return int(round(radians / angle_step)) % HEADING_BINS

    start_layer = nearest_layer(start_orientation)
    end_layer = nearest_layer(end_orientation)

    if not field.free_bin(start_layer, start_row, start_col):
        return {
            "passable": False,
            "reason": "start cannot contain the complete robot footprint at the requested heading",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
        }
    if not field.free_bin(end_layer, end_row, end_col):
        return {
            "passable": False,
            "reason": "end cannot contain the complete robot footprint at the requested heading",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
        }

    start_node = (start_layer, start_row, start_col)
    distance = {start_node: 0}
    previous: dict[tuple[int, int, int], tuple[int, int, int] | None] = {start_node: None}
    queue = [start_node]
    head = 0
    target = None

    while head < len(queue):
        layer, row, col = queue[head]
        node = (layer, row, col)
        head += 1
        if row == end_row and col == end_col and layer == end_layer:
            target = node
            break

        for delta_row, delta_col in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            next_row, next_col = row + delta_row, col + delta_col
            next_node = (layer, next_row, next_col)
            if not (0 <= next_row < rows and 0 <= next_col < cols) or next_node in distance:
                continue
            heading = layer * angle_step
            midpoint_clear = field.heading_clear(
                row + 0.5 * delta_row, col + 0.5 * delta_col, heading
            )
            if midpoint_clear and field.free_bin(layer, next_row, next_col):
                distance[next_node] = distance[node] + 1
                previous[next_node] = node
                queue.append(next_node)

        current_heading = layer * angle_step
        for delta_layer in (-1, 1):
            next_layer = (layer + delta_layer) % HEADING_BINS
            next_node = (next_layer, row, col)
            if next_node in distance:
                continue
            fractions = (index / ROTATION_SUBDIVISIONS for index in range(ROTATION_SUBDIVISIONS + 1))
            sweep_clear = all(
                field.heading_clear(row, col, current_heading + delta_layer * fraction * angle_step)
                for fraction in fractions
            )
            if sweep_clear and field.free_bin(next_layer, row, col):
                distance[next_node] = distance[node] + 1
                previous[next_node] = node
                queue.append(next_node)

    if target is None:
        return {
            "passable": False,
            "reason": "no collision-free trajectory exists while continuously checking the swept complete footprint",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
        }

    nodes = []
    current: tuple[int, int, int] | None = target
    while current is not None:
        layer, row, col = current
        point = grid_to_world(row, col, resolution, rows, map_origin)
        nodes.append({
            "x": point.x,
            "y": point.y,
            "orientation": (layer * angle_step) % (2.0 * math.pi),
        })
        current = previous[current]
    nodes.reverse()
    return {
        "passable": True,
        "reason": "collision-free passage exists while continuously checking the swept complete footprint",
        "algorithm": ALGORITHM_VERSION,
        "path": nodes,
    }
