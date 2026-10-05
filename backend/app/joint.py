from __future__ import annotations

import heapq
import math
from typing import Any

from .geometry import Point, validate_passage, world_to_grid


JOINT_ALGORITHM_VERSION = "joint-departure-scheduler-v1"
OBJECTIVES = ("min_total_wait", "min_makespan")
SINGLE_SEARCH_BUDGET = 1_500_000
EPS = 1e-9


def _merge_task_state(project_state: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """任务只覆盖起点/终点/姿态/速度/机体尺寸，限时封闭区与检查点窗口来自工程共享状态。"""

    state = dict(project_state)
    state["start"] = task["start"]
    state["end"] = task["end"]
    state["start_orientation"] = task.get("start_orientation") or 0.0
    state["end_orientation"] = task.get("end_orientation")
    for key in ("linear_speed", "angular_speed", "robot_width", "robot_length"):
        if task.get(key) is not None:
            state[key] = task[key]
    return state


def _departure_options(task: dict[str, Any], quantum: float) -> list[float]:
    """可调任务的候选出发时刻按时间量子离散；已承诺任务只有固定时刻。"""

    if task.get("committed"):
        return [float(task["committed_departure"])]
    earliest = float(task["earliest_departure"])
    latest = float(task["latest_departure"])
    steps = int(math.floor((latest - earliest) / quantum + EPS))
    options = [earliest + index * quantum for index in range(steps + 1)]
    if options[-1] < latest - EPS:
        options.append(latest)
    return options


def _segment_occupancy(
    segments: list[dict[str, Any]],
    resolution: float,
    rows: int,
    origin: Point,
    robot_width: float,
    robot_length: float,
) -> dict[tuple[int, int], list[tuple[float, float]]]:
    """把逐段时序还原成栅格占用区间，并按整机外接圆保守膨胀。

    同一栅格被两项任务在同一半开区间 [start, end) 占用即为冲突。
    """

    corner_radius = 0.5 * math.hypot(robot_width, robot_length)
    reach = int(math.ceil(corner_radius / resolution))
    occupancy: dict[tuple[int, int], list[tuple[float, float]]] = {}

    def occupy(x: float, y: float, start: float, end: float) -> None:
        if end <= start + EPS:
            return
        row, col = world_to_grid(Point(x, y), resolution, rows, origin)
        for d_row in range(-reach, reach + 1):
            for d_col in range(-reach, reach + 1):
                cell = (row + d_row, col + d_col)
                occupancy.setdefault(cell, []).append((start, end))

    for segment in segments:
        if segment["type"] not in ("arrive", "wait_depart_arrive", "turn", "wait"):
            continue
        start = float(segment["start"])
        end = float(segment["end"])
        occupy(float(segment["x"]), float(segment["y"]), start, end)
        if "from_x" in segment:
            occupy(float(segment["from_x"]), float(segment["from_y"]), start, end)
    return occupancy


def _find_conflicts(
    occupancies: dict[str, dict[tuple[int, int], list[tuple[float, float]]]],
) -> list[dict[str, Any]]:
    """返回任务间栅格-时间重叠，按开始时刻排序，最多保留前 20 条。"""

    cells: set[tuple[int, int]] = set()
    for occupancy in occupancies.values():
        cells.update(occupancy.keys())
    conflicts: list[dict[str, Any]] = []
    for cell in cells:
        intervals: list[tuple[float, float, str]] = []
        for task_id, occupancy in occupancies.items():
            for start, end in occupancy.get(cell, []):
                intervals.append((start, end, task_id))
        intervals.sort()
        for index in range(len(intervals)):
            start_a, end_a, task_a = intervals[index]
            for other in range(index + 1, len(intervals)):
                start_b, end_b, task_b = intervals[other]
                if start_b >= end_a - EPS:
                    break
                if task_a == task_b:
                    continue
                conflicts.append(
                    {
                        "tasks": sorted([task_a, task_b]),
                        "cell": [cell[0], cell[1]],
                        "intervals": [[start_a, end_a], [start_b, end_b]],
                    }
                )
                if len(conflicts) >= 20:
                    return conflicts
    return conflicts


def _summarize_single(result: dict[str, Any]) -> dict[str, Any]:
    segments = result.get("segments", [])
    arrival = 0.0
    wait = 0.0
    for segment in segments:
        if segment["type"] == "wait":
            wait += float(segment["end"]) - float(segment["start"])
        moment = segment.get("end", segment.get("time", 0.0))
        arrival = max(arrival, float(moment))
    return {
        "feasible": bool(result.get("passable")),
        "status": result.get("status"),
        "reason": result.get("reason"),
        "arrival": arrival,
        "wait": wait,
    }


def plan_joint(
    *,
    grid: list[list[bool]],
    resolution: float,
    project_state: dict[str, Any],
    tasks: list[dict[str, Any]],
    search_budget: int = 200_000,
    time_quantum: float | None = None,
) -> dict[str, Any]:
    """为一组共享地图的任务联合选择出发时刻。

    - 只调整出发时刻，不改路线几何；每条路线的封路/窗口时序仍由单任务搜索负责；
    - 已承诺任务出发时刻固定，只作为不可移动的约束参与冲突核对；
    - 预算耗尽（search_incomplete）与证实无解（infeasible）分别表达。
    """

    if not tasks:
        raise ValueError("at least one task is required")
    task_ids = [task["id"] for task in tasks]
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("task ids must be unique")

    rows = len(grid)
    origin = Point(project_state["origin"]["x"], project_state["origin"]["y"])
    if time_quantum is None:
        speeds = [
            float(
                task.get("linear_speed")
                if task.get("linear_speed") is not None
                else project_state["linear_speed"]
            )
            for task in tasks
        ]
        time_quantum = resolution / max(speeds)
    if time_quantum <= 0:
        raise ValueError("time quantum must be positive")

    option_lists = [_departure_options(task, time_quantum) for task in tasks]
    total_combinations = 1
    for options in option_lists:
        total_combinations *= len(options)

    memo: dict[tuple[str, float], dict[str, Any]] = {}
    occupancies: dict[tuple[str, float], dict[tuple[int, int], list[tuple[float, float]]]] = {}

    def run_single(index: int, departure: float) -> dict[str, Any]:
        task = tasks[index]
        key = (task["id"], round(departure, 9))
        if key not in memo:
            state = _merge_task_state(project_state, task)
            result = validate_passage(
                grid,
                state["forbidden_zones"],
                resolution,
                state["robot_width"],
                state["robot_length"],
                state["origin"],
                state["start"],
                state["end"],
                start_orientation=state["start_orientation"],
                end_orientation=state["end_orientation"],
                checkpoints=state["checkpoints"],
                waiting_zones=state["waiting_zones"],
                timed_zones=state.get("timed_zones", []),
                linear_speed=state["linear_speed"],
                angular_speed=state["angular_speed"],
                start_time=departure,
                search_budget=SINGLE_SEARCH_BUDGET,
            )
            summary = _summarize_single(result)
            memo[key] = {"result": result, "summary": summary}
            if summary["feasible"]:
                occupancies[key] = _segment_occupancy(
                    result.get("segments", []),
                    resolution,
                    rows,
                    origin,
                    state["robot_width"],
                    state["robot_length"],
                )
        return memo[key]

    single_feasible: dict[str, bool] = {}
    for index, task in enumerate(tasks):
        first_departure = option_lists[index][0]
        single_feasible[task["id"]] = run_single(index, first_departure)["summary"][
            "feasible"
        ]

    best: dict[str, dict[str, Any]] = {}
    evaluated = 0
    task_failure_combinations = 0
    conflict_combinations = 0
    sample_conflict: dict[str, Any] | None = None
    sample_task_failure: dict[str, Any] | None = None

    start_indices = tuple(0 for _ in tasks)
    heap: list[tuple[float, tuple[int, ...]]] = [
        (sum(option_lists[i][0] for i in range(len(tasks))), start_indices)
    ]
    seen = {start_indices}
    heap_exhausted = False
    while heap:
        if evaluated >= search_budget:
            break
        _, indices = heapq.heappop(heap)
        evaluated += 1
        departures = [option_lists[i][indices[i]] for i in range(len(tasks))]

        failed: tuple[int, dict[str, Any]] | None = None
        singles: list[dict[str, Any]] = []
        for i, departure in enumerate(departures):
            single = run_single(i, departure)
            singles.append(single)
            if not single["summary"]["feasible"] and failed is None:
                failed = (i, single)
        if failed is not None:
            task_failure_combinations += 1
            if sample_task_failure is None:
                index, single = failed
                sample_task_failure = {
                    "task_id": tasks[index]["id"],
                    "departure": departures[index],
                    "status": single["summary"]["status"],
                    "reason": single["summary"]["reason"],
                }
        else:
            combo_occupancy = {
                tasks[i]["id"]: occupancies[(tasks[i]["id"], round(departures[i], 9))]
                for i in range(len(tasks))
            }
            conflicts = _find_conflicts(combo_occupancy)
            if conflicts:
                conflict_combinations += 1
                if sample_conflict is None:
                    sample_conflict = conflicts[0]
            else:
                total_wait = 0.0
                makespan = 0.0
                for i, departure in enumerate(departures):
                    summary = singles[i]["summary"]
                    total_wait += (
                        departure - float(tasks[i]["earliest_departure"])
                    ) + summary["wait"]
                    makespan = max(makespan, summary["arrival"])
                candidate = {
                    "departures": {
                        tasks[i]["id"]: departures[i] for i in range(len(tasks))
                    },
                    "total_wait": total_wait,
                    "makespan": makespan,
                    "tasks": {
                        tasks[i]["id"]: {
                            "departure": departures[i],
                            "arrival_time": singles[i]["summary"]["arrival"],
                            "wait": singles[i]["summary"]["wait"],
                            "status": singles[i]["summary"]["status"],
                            "segments": singles[i]["result"].get("segments", []),
                            "schedule": singles[i]["result"].get("schedule", []),
                            "path": singles[i]["result"].get("path", []),
                        }
                        for i in range(len(tasks))
                    },
                }
                wait_key = (round(total_wait, 9), round(makespan, 9))
                if "min_total_wait" not in best or wait_key < best[
                    "min_total_wait"
                ]["_key"]:
                    kept = dict(candidate)
                    kept["_key"] = wait_key
                    best["min_total_wait"] = kept
                span_key = (round(makespan, 9), round(total_wait, 9))
                if "min_makespan" not in best or span_key < best["min_makespan"]["_key"]:
                    kept = dict(candidate)
                    kept["_key"] = span_key
                    best["min_makespan"] = kept

        for dim in range(len(tasks)):
            if indices[dim] + 1 >= len(option_lists[dim]):
                continue
            neighbor = list(indices)
            neighbor[dim] += 1
            neighbor_key = tuple(neighbor)
            if neighbor_key in seen:
                continue
            seen.add(neighbor_key)
            heapq.heappush(
                heap,
                (
                    sum(option_lists[i][neighbor[i]] for i in range(len(tasks))),
                    neighbor_key,
                ),
            )
    else:
        heap_exhausted = True
    search_completed = heap_exhausted

    candidates = []
    for objective in OBJECTIVES:
        if objective not in best:
            continue
        candidate = best[objective]
        candidate.pop("_key", None)
        candidate["objective"] = objective
        candidate["conflict_free"] = True
        candidate["provisional"] = not search_completed
        candidates.append(candidate)

    rejection = {
        "task_failure_combinations": task_failure_combinations,
        "conflict_combinations": conflict_combinations,
        "sample_task_failure": sample_task_failure,
        "sample_conflict": sample_conflict,
    }
    if not search_completed:
        status = "search_incomplete"
        reason = (
            "联合搜索预算耗尽，已评估 "
            f"{evaluated}/{total_combinations} 种出发组合；"
            "可行性与无解均未证明，当前候选仅为部分结果"
        )
    elif candidates:
        status = "feasible"
        reason = "全部出发组合评估完毕，候选方案在共享区域内无栅格-时间冲突"
    else:
        status = "infeasible"
        reason = (
            "全部出发组合评估完毕，不存在无冲突安排："
            f"{task_failure_combinations} 种因单任务不可行失败，"
            f"{conflict_combinations} 种因共享区占用冲突失败"
        )
    return {
        "status": status,
        "reason": reason,
        "algorithm": JOINT_ALGORITHM_VERSION,
        "search_completed": search_completed,
        "evaluated_combinations": evaluated,
        "total_combinations": total_combinations,
        "time_quantum": time_quantum,
        "single_feasible": single_feasible,
        "candidates": candidates,
        "rejection": rejection,
    }
