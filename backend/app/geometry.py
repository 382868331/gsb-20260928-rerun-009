from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


ALGORITHM_VERSION = "rectangle-footprint-bfs-v1"


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


def _free_centers(occupancy: np.ndarray, row_span: int, col_span: int) -> np.ndarray:
    rows, cols = occupancy.shape
    integral = np.zeros((rows + 1, cols + 1), dtype=np.int32)
    integral[1:, 1:] = np.cumsum(np.cumsum(occupancy.astype(np.int32), axis=0), axis=1)
    free = np.zeros_like(occupancy, dtype=bool)
    blocked = (
        integral[row_span:, col_span:]
        - integral[:-row_span, col_span:]
        - integral[row_span:, :-col_span]
        + integral[:-row_span, :-col_span]
    )
    valid_windows = blocked == 0
    before_rows = (row_span - 1) // 2
    before_cols = (col_span - 1) // 2
    free[before_rows:before_rows + valid_windows.shape[0], before_cols:before_cols + valid_windows.shape[1]] = valid_windows
    return free


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

    length_cells = max(1, int(np.ceil(robot_length / resolution)))
    width_cells = max(1, int(np.ceil(robot_width / resolution)))
    orientations = (0.0, 1.5707963267948966)
    free = np.stack(
        [
            _free_centers(occupancy, length_cells, width_cells),
            _free_centers(occupancy, width_cells, length_cells),
        ]
    )

    if not free[:, start_row, start_col].any():
        return {
            "passable": False,
            "reason": "start cannot contain the complete robot footprint",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
        }
    if not free[:, end_row, end_col].any():
        return {
            "passable": False,
            "reason": "end cannot contain the complete robot footprint",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
        }

    dist = np.full((2, rows, cols), -1, dtype=np.int32)
    previous: dict[tuple[int, int, int], tuple[int, int, int] | None] = {}
    queue: list[tuple[int, int, int]] = []
    for layer in range(2):
        if free[layer, start_row, start_col]:
            dist[layer, start_row, start_col] = 0
            previous[(layer, start_row, start_col)] = None
            queue.append((layer, start_row, start_col))

    head = 0
    target = None
    while head < len(queue):
        layer, row, col = queue[head]
        head += 1
        if row == end_row and col == end_col:
            target = (layer, row, col)
            break
        for next_layer, next_row, next_col in (
            (layer, row - 1, col),
            (layer, row + 1, col),
            (layer, row, col - 1),
            (layer, row, col + 1),
            (1 - layer, row, col),
        ):
            if not (0 <= next_row < rows and 0 <= next_col < cols):
                continue
            if not free[next_layer, next_row, next_col] or dist[next_layer, next_row, next_col] != -1:
                continue
            dist[next_layer, next_row, next_col] = dist[layer, row, col] + 1
            previous[(next_layer, next_row, next_col)] = (layer, row, col)
            queue.append((next_layer, next_row, next_col))

    if target is None:
        return {
            "passable": False,
            "reason": "no collision-free center trajectory exists for the complete rectangle footprint",
            "algorithm": ALGORITHM_VERSION,
            "path": [],
        }

    nodes = []
    current: tuple[int, int, int] | None = target
    while current is not None:
        layer, row, col = current
        point = grid_to_world(row, col, resolution, rows, map_origin)
        nodes.append({"x": point.x, "y": point.y, "orientation": orientations[layer]})
        current = previous[current]
    nodes.reverse()
    return {
        "passable": True,
        "reason": "collision-free passage exists for the complete rectangle footprint",
        "algorithm": ALGORITHM_VERSION,
        "path": nodes,
    }
