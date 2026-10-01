from __future__ import annotations

import hashlib
import heapq
import json
import math
from typing import Any

import numpy as np

from .geometry import (
    ORIENTATION_STEPS,
    Point,
    build_occupancy,
    build_orientation_layers,
    configuration_neighbors,
    grid_to_world,
    nearest_orientation_layer,
    world_to_grid,
)


PLAN_ALGORITHM_VERSION = "timed-checkpoint-dijkstra-v1"
DEFAULT_EXPANSION_BUDGET = 400_000

STATUS_OK = "ok"
STATUS_ORIENTATION = "orientation_unsatisfiable"
STATUS_WINDOW = "window_missed"
STATUS_BLOCKED = "blocked"
STATUS_INCOMPLETE = "search_incomplete"
STATUS_WAITING = "waiting_forbidden"

STATUS_LABELS = {
    STATUS_OK: "可以通过",
    STATUS_ORIENTATION: "朝向不满足",
    STATUS_WINDOW: "窗口错过",
    STATUS_BLOCKED: "道路受阻",
    STATUS_INCOMPLETE: "搜索尚未完成",
    STATUS_WAITING: "禁止停留区无法等待",
}


def map_revision(grid: list[list[bool]], state: dict[str, Any]) -> str:
    payload = json.dumps({"grid": grid, "state": state}, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def _zone_mask(
    zones: list[dict[str, float]],
    rows: int,
    cols: int,
    resolution: float,
    origin: Point,
) -> np.ndarray:
    mask = np.zeros((rows, cols), dtype=bool)
    for zone in zones:
        zx = float(zone["x"]) - origin.x
        zy = float(zone["y"]) - origin.y
        x1 = max(0.0, min(zx, zx + float(zone["width"])))
        x2 = min(cols * resolution, max(zx, zx + float(zone["width"])))
        y1 = max(0.0, min(zy, zy + float(zone["height"])))
        y2 = min(rows * resolution, max(zy, zy + float(zone["height"])))
        min_col = int(np.floor(x1 / resolution))
        max_col = int(np.ceil(x2 / resolution))
        min_row = int(np.floor((rows * resolution - y2) / resolution))
        max_row = int(np.ceil((rows * resolution - y1) / resolution))
        if max_row > min_row and max_col > min_col:
            mask[min_row:max_row, min_col:max_col] = True
    return mask


def _result(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "status_label": STATUS_LABELS[status],
        "reason": reason,
        "algorithm": PLAN_ALGORITHM_VERSION,
        **extra,
    }


def _dijkstra_segment(
    start: tuple[int, int, int],
    target: tuple[int, int, int],
    pose: np.ndarray,
    free_x: np.ndarray,
    free_y: np.ndarray,
    sweep: np.ndarray,
    move_cost: float,
    turn_cost: float,
    budget: int,
) -> tuple[float | None, list[tuple[int, int, int]] | None, int]:
    """Earliest-arrival search between two configurations.

    Returns (cost, path, expansions). path is None when unreachable, cost is
    None when the expansion budget ran out first.
    """

    dist = {start: 0.0}
    previous: dict[tuple[int, int, int], tuple[int, int, int] | None] = {start: None}
    heap: list[tuple[float, tuple[int, int, int]]] = [(0.0, start)]
    expansions = 0
    found = False
    while heap:
        cost, node = heapq.heappop(heap)
        if cost > dist.get(node, math.inf):
            continue
        if node == target:
            found = True
            break
        expansions += 1
        if expansions > budget:
            return None, None, expansions
        layer, row, col = node
        for nxt in configuration_neighbors(layer, row, col, pose, free_x, free_y, sweep):
            step = turn_cost if nxt[0] != layer else move_cost
            new_cost = cost + step
            if new_cost < dist.get(nxt, math.inf):
                dist[nxt] = new_cost
                previous[nxt] = node
                heapq.heappush(heap, (new_cost, nxt))
    if not found:
        return math.inf, None, expansions
    path = []
    current: tuple[int, int, int] | None = target
    while current is not None:
        path.append(current)
        current = previous[current]
    path.reverse()
    return dist[target], path, expansions


def _pose_failure(
    label: str,
    layer: int,
    row: int,
    col: int,
    pose: np.ndarray,
) -> dict[str, Any] | None:
    if pose[layer, row, col]:
        return None
    if bool(pose[:, row, col].any()):
        return _result(
            STATUS_ORIENTATION,
            f"{label}：位置可放置机器人，但要求的朝向下长方形外廓与障碍冲突",
        )
    return _result(
        STATUS_BLOCKED,
        f"{label}：任何朝向下长方形机器人外廓都无法放置",
    )


def plan_timed(
    base_grid: list[list[bool]],
    forbidden_zones: list[dict[str, float]],
    no_wait_zones: list[dict[str, float]],
    resolution: float,
    robot_width: float,
    robot_length: float,
    origin: dict[str, float],
    start: dict[str, float],
    start_orientation: float,
    checkpoints: list[dict[str, Any]],
    end: dict[str, float],
    end_orientation: float,
    linear_speed: float,
    angular_speed: float,
    expansion_budget: int = DEFAULT_EXPANSION_BUDGET,
) -> dict[str, Any]:
    if linear_speed <= 0 or angular_speed <= 0:
        raise ValueError("speeds must be positive")
    map_origin = Point(origin["x"], origin["y"])
    occupancy = build_occupancy(base_grid, forbidden_zones, resolution, map_origin)
    rows, cols = occupancy.shape
    no_wait = _zone_mask(no_wait_zones, rows, cols, resolution, map_origin)
    pose, free_x, free_y, sweep, orientations = build_orientation_layers(
        occupancy, robot_width, robot_length, resolution
    )
    move_cost = resolution / linear_speed
    turn_cost = (2.0 * math.pi / ORIENTATION_STEPS) / angular_speed

    def to_cell(point: dict[str, float], label: str) -> tuple[int, int]:
        row, col = world_to_grid(Point(point["x"], point["y"]), resolution, rows, map_origin)
        if not (0 <= row < rows and 0 <= col < cols):
            raise ValueError(f"{label} is outside the map")
        return row, col

    start_row, start_col = to_cell(start, "start")
    end_row, end_col = to_cell(end, "end")
    start_layer = nearest_orientation_layer(start_orientation)
    end_layer = nearest_orientation_layer(end_orientation)

    failure = _pose_failure("起点", start_layer, start_row, start_col, pose)
    if failure:
        return failure

    targets: list[dict[str, Any]] = []
    for index, checkpoint in enumerate(checkpoints):
        row, col = to_cell(checkpoint, f"checkpoint {index + 1}")
        layer = nearest_orientation_layer(float(checkpoint["orientation"]))
        failure = _pose_failure(
            f"检查点 {checkpoint.get('name') or index + 1}", layer, row, col, pose
        )
        if failure:
            return failure
        targets.append(
            {
                "kind": "checkpoint",
                "name": checkpoint.get("name") or f"检查点 {index + 1}",
                "node": (layer, row, col),
                "earliest": float(checkpoint["earliest"]),
                "latest": float(checkpoint["latest"]),
            }
        )
    failure = _pose_failure("终点", end_layer, end_row, end_col, pose)
    if failure:
        return failure
    targets.append(
        {
            "kind": "end",
            "name": "终点",
            "node": (end_layer, end_row, end_col),
            "earliest": None,
            "latest": None,
        }
    )

    current = (start_layer, start_row, start_col)
    current_time = 0.0
    full_path: list[tuple[int, int, int]] = [current]
    schedule: list[dict[str, Any]] = []
    total_expansions = 0

    for target in targets:
        cost, segment, expansions = _dijkstra_segment(
            current,
            target["node"],
            pose,
            free_x,
            free_y,
            sweep,
            move_cost,
            turn_cost,
            expansion_budget,
        )
        total_expansions += expansions
        if cost is None:
            return _result(
                STATUS_INCOMPLETE,
                f"搜索在 {expansion_budget} 次扩展内尚未完成，无法断定到{target['name']}是否可达",
                expansions=total_expansions,
            )
        if segment is None:
            return _result(
                STATUS_BLOCKED,
                f"从上一位置到{target['name']}不存在无碰撞的连续转向轨迹",
                expansions=total_expansions,
            )
        arrival = current_time + cost
        full_path.extend(segment[1:])
        entry: dict[str, Any] = {
            "name": target["name"],
            "kind": target["kind"],
            "arrival": round(arrival, 3),
            "path_index": len(full_path) - 1,
        }
        if target["kind"] == "checkpoint":
            entry["earliest"] = target["earliest"]
            entry["latest"] = target["latest"]
            if arrival > target["latest"] + 1e-9:
                return _result(
                    STATUS_WINDOW,
                    f"最早 {arrival:.2f}s 才能以要求朝向到达{target['name']}，"
                    f"错过窗口 [{target['earliest']:.1f}, {target['latest']:.1f}]s",
                    schedule=schedule + [entry],
                    expansions=total_expansions,
                )
            wait = max(0.0, target["earliest"] - arrival)
            layer, row, col = target["node"]
            wait_index = len(full_path) - 1
            if wait > 1e-9 and no_wait[row, col]:
                wait_index = _find_wait_cell(full_path, no_wait)
                if wait_index is None:
                    return _result(
                        STATUS_WAITING,
                        f"{target['name']}需要等待 {wait:.2f}s，但它位于禁止停留通道，"
                        "且路径前方没有可停留的格子",
                        schedule=schedule + [entry],
                        expansions=total_expansions,
                    )
            entry["wait"] = round(wait, 3)
            entry["wait_path_index"] = wait_index
            arrival += wait
        entry["depart"] = round(arrival, 3)
        schedule.append(entry)
        current = target["node"]
        current_time = arrival

    nodes = []
    for layer, row, col in full_path:
        point = grid_to_world(row, col, resolution, rows, map_origin)
        nodes.append(
            {
                "x": point.x,
                "y": point.y,
                "orientation": float(orientations[layer]),
                "row": row,
                "col": col,
                "layer": layer,
            }
        )
    return _result(
        STATUS_OK,
        "所有检查点均可在时间窗口内以要求朝向通过",
        path=nodes,
        schedule=schedule,
        total_time=round(current_time, 3),
        expansions=total_expansions,
    )


def _find_wait_cell(
    path: list[tuple[int, int, int]], no_wait: np.ndarray
) -> int | None:
    for index in range(len(path) - 1, -1, -1):
        _, row, col = path[index]
        if not no_wait[row, col]:
            return index
    return None


def retime_path(
    path: list[dict[str, Any]],
    checkpoint_entries: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    no_wait_zones: list[dict[str, float]],
    map_rows: int,
    map_cols: int,
    resolution: float,
    origin: dict[str, float],
    linear_speed: float,
    angular_speed: float,
    relocate_waits: bool,
) -> dict[str, Any]:
    """Re-judge a stored geometric path against updated time windows.

    checkpoint_entries pair stored path indices with checkpoint names. When
    relocate_waits is False the wait must happen at the checkpoint cell
    itself (kept-geometry plan); otherwise the wait slides to the nearest
    preceding cell outside the no-wait corridors (adjusted-waiting plan).
    """

    map_origin = Point(origin["x"], origin["y"])
    no_wait = _zone_mask(no_wait_zones, map_rows, map_cols, resolution, map_origin)
    move_cost = resolution / linear_speed
    turn_cost = (2.0 * math.pi / ORIENTATION_STEPS) / angular_speed

    time = 0.0
    schedule: list[dict[str, Any]] = []
    checkpoint_by_index = {entry["path_index"]: entry for entry in checkpoint_entries}
    checkpoint_queue = list(checkpoints)
    for index, node in enumerate(path):
        if index > 0:
            previous = path[index - 1]
            if node["layer"] != previous["layer"]:
                time += turn_cost
            else:
                time += move_cost
        entry = checkpoint_by_index.get(index)
        if entry is None:
            continue
        checkpoint = checkpoint_queue.pop(0)
        record: dict[str, Any] = {
            "name": entry["name"],
            "kind": "checkpoint",
            "arrival": round(time, 3),
            "path_index": index,
            "earliest": float(checkpoint["earliest"]),
            "latest": float(checkpoint["latest"]),
        }
        if time > float(checkpoint["latest"]) + 1e-9:
            return _result(
                STATUS_WINDOW,
                f"沿原路线最早 {time:.2f}s 到达{entry['name']}，"
                f"错过新窗口 [{checkpoint['earliest']:.1f}, {checkpoint['latest']:.1f}]s",
                schedule=schedule + [record],
            )
        wait = max(0.0, float(checkpoint["earliest"]) - time)
        if wait > 1e-9:
            wait_index = index
            if no_wait[node["row"], node["col"]]:
                if not relocate_waits:
                    return _result(
                        STATUS_WAITING,
                        f"{entry['name']}位于禁止停留通道，保留几何路线时无法在此等待 "
                        f"{wait:.2f}s；请对比调整等待位置的方案",
                        schedule=schedule + [record],
                    )
                wait_index = _find_wait_cell(
                    [(0, p["row"], p["col"]) for p in path[: index + 1]], no_wait
                )
                if wait_index is None:
                    return _result(
                        STATUS_WAITING,
                        f"{entry['name']}需要等待但路径前方没有允许停留的格子",
                        schedule=schedule + [record],
                    )
            record["wait"] = round(wait, 3)
            record["wait_path_index"] = wait_index
            time += wait
        record["depart"] = round(time, 3)
        schedule.append(record)
    return _result(
        STATUS_OK,
        "原几何路线在新时间窗口下仍然可行" if not relocate_waits else "调整等待位置后满足新时间窗口",
        schedule=schedule,
        total_time=round(time, 3),
    )
