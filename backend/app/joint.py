from __future__ import annotations

import hashlib
import json
from typing import Any

from .geometry import (
    DEFAULT_SEARCH_BUDGET,
    EPS,
    Point,
    _build_timed_zones,
    build_occupancy,
    nearest_orientation_layer,
    validate_passage,
    world_to_grid,
)

JOINT_ALGORITHM = "joint-departure-enumeration-v1"
# 共享冲突区视为永远封闭的资源，只借用其占位掩码做互斥判定。
_SHARED_CLOSE_END = 1e18
# 目标值比较容差：路径耗时是 0.2s 级浮点累加，1e-9 级噪声不应左右最优选择。
_OBJ_EPS = 1e-6


def commitment_hash(tasks: list[dict[str, Any]]) -> str:
    committed = [
        {
            "id": task["id"],
            "departure": _task_departure(task),
            "state": task["state"],
        }
        for task in tasks
        if task.get("committed")
    ]
    committed.sort(key=lambda item: item["id"])
    encoded = json.dumps(committed, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def environment_hash(tasks: list[dict[str, Any]], shared_zones: list[dict[str, Any]]) -> str:
    """规划所依据的环境指纹：共享冲突区 + 各任务状态（封路、检查点、机器人等）。

    中途新增封闭、改动检查点窗口或共享区都会改变该指纹，使既有候选在采用前
    核对时被拒绝；与候选无关的其他候选各自保存指纹，互不影响。
    """

    env = {
        "shared_zones": shared_zones,
        "tasks": [
            {"id": task["id"], "state": task["state"]}
            for task in sorted(tasks, key=lambda item: item["id"])
        ],
    }
    encoded = json.dumps(env, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _task_departure(task: dict[str, Any]) -> float:
    if task.get("departure") is not None:
        return float(task["departure"])
    return float(task["state"].get("start_time", 0.0))


def _departure_candidates(task: dict[str, Any]) -> list[float]:
    if task.get("committed"):
        return [_task_departure(task)]
    start = float(task.get("adjust_start", 0.0))
    end = float(task.get("adjust_end", start))
    step = float(task.get("adjust_step", 1.0))
    if end < start - EPS:
        return []
    candidates = []
    value = start
    while value <= end + EPS and len(candidates) <= 4096:
        candidates.append(round(value, 9))
        value += step
    return candidates


def _task_resources(state, shared_zones, occupancy, resolution, origin):
    zones = []
    for zone in state.get("timed_zones", []):
        zones.append(("timed:" + str(zone.get("id", "")), zone))
    for zone in shared_zones:
        shared = dict(zone)
        shared["close_start"] = 0.0
        shared["close_end"] = _SHARED_CLOSE_END
        zones.append(("shared:" + str(zone.get("id", "")), shared))
    if not zones:
        return []
    built = _build_timed_zones(
        [zone for _, zone in zones],
        occupancy,
        float(state.get("robot_width", 0.5)),
        float(state.get("robot_length", 0.8)),
        resolution,
        origin,
    )
    for (resource_id, _), resource in zip(zones, built):
        resource["resource_id"] = resource_id
    return built


def _merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + EPS:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _segment_occupancy(segments, resources, resolution, rows, origin):
    occupancy = {resource["resource_id"]: [] for resource in resources}
    if not resources:
        return occupancy

    def locate(seg, prefix=""):
        row = seg.get(prefix + "row")
        col = seg.get(prefix + "col")
        if row is None or col is None:
            row, col = world_to_grid(
                Point(float(seg[prefix + "x"]), float(seg[prefix + "y"])),
                resolution,
                rows,
                origin,
            )
        return int(row), int(col)

    for seg in segments:
        start = seg.get("start")
        end = seg.get("end")
        if start is None or end is None or end < start - EPS:
            continue
        kind = seg.get("type")
        if kind not in ("wait", "turn", "arrive", "wait_depart_arrive"):
            continue
        row, col = locate(seg)
        layer = nearest_orientation_layer(float(seg.get("orientation", 0.0)))
        for resource in resources:
            hit = False
            if kind == "turn":
                hit = bool(resource["sweep_blocked"][layer, row, col])
            elif kind == "wait":
                hit = bool(resource["pose_blocked"][layer, row, col])
            else:
                from_row, from_col = locate(seg, "from_")
                hit = bool(
                    resource["pose_blocked"][layer, row, col]
                    or resource["pose_blocked"][layer, from_row, from_col]
                )
            if hit:
                occupancy[resource["resource_id"]].append((float(start), float(end)))
    return {key: _merge_intervals(value) for key, value in occupancy.items()}


def _intervals_conflict(left, right):
    index = 0
    for start, end in left:
        while index < len(right) and right[index][1] <= start + EPS:
            index += 1
        probe = index
        while probe < len(right) and right[probe][0] < end - EPS:
            if right[probe][1] > start + EPS:
                return True
            probe += 1
    return False


def _option_conflicts(option_occ, acc_occ):
    for resource_id, intervals in option_occ.items():
        taken = acc_occ.get(resource_id)
        if taken and _intervals_conflict(intervals, taken):
            return resource_id
    return None


def _merge_acc(acc_occ, option_occ):
    merged = dict(acc_occ)
    for resource_id, intervals in option_occ.items():
        merged[resource_id] = _merge_intervals(merged.get(resource_id, []) + intervals)
    return merged


def plan_joint(
    *,
    grid,
    resolution,
    tasks,
    shared_zones=None,
    search_budget=200_000,
    per_task_budget=DEFAULT_SEARCH_BUDGET,
):
    shared_zones = shared_zones or []
    single = []
    options_per_task = []

    for task in tasks:
        state = task["state"]
        origin = Point(float(state["origin"]["x"]), float(state["origin"]["y"]))
        occupancy = build_occupancy(
            grid, state.get("forbidden_zones", []), resolution, origin
        )
        rows = occupancy.shape[0]
        resources = _task_resources(state, shared_zones, occupancy, resolution, origin)
        options = []
        attempts = []
        for departure in _departure_candidates(task):
            result = validate_passage(
                grid,
                state.get("forbidden_zones", []),
                resolution,
                float(state.get("robot_width", 0.5)),
                float(state.get("robot_length", 0.8)),
                state["origin"],
                state["start"],
                state["end"],
                start_orientation=float(state.get("start_orientation", 0.0)),
                end_orientation=state.get("end_orientation"),
                checkpoints=state.get("checkpoints", []),
                waiting_zones=state.get("waiting_zones", []),
                timed_zones=state.get("timed_zones", []),
                linear_speed=float(state.get("linear_speed", 0.5)),
                angular_speed=float(state.get("angular_speed", 0.5)),
                start_time=departure,
                search_budget=per_task_budget,
            )
            attempts.append(
                {
                    "departure": departure,
                    "status": result["status"],
                    "reason": result.get("reason", ""),
                    "search_completed": result.get("search_completed", True),
                }
            )
            if result["status"] != "feasible":
                continue
            segments = result.get("segments", [])
            schedule = result.get("schedule", [])
            if schedule:
                arrival = float(schedule[-1]["time"])
            else:
                # 无检查点/封路时只返回压缩段，到达时刻取最后一段结束时间。
                arrival = max(
                    (float(seg["end"]) for seg in segments if seg.get("end") is not None),
                    default=departure,
                )
            wait_total = sum(
                float(seg["end"]) - float(seg["start"])
                for seg in segments
                if seg.get("type") == "wait"
            )
            options.append(
                {
                    "task_id": task["id"],
                    "departure": departure,
                    "arrival": arrival,
                    "wait": wait_total,
                    "schedule": result["schedule"],
                    "segments": segments,
                    "replay": result.get("replay"),
                    "occupancy": _segment_occupancy(
                        segments, resources, resolution, rows, origin
                    ),
                }
            )
        options_per_task.append(options)
        single.append(
            {
                "id": task["id"],
                "committed": bool(task.get("committed")),
                "feasible_departures": [opt["departure"] for opt in options],
                "attempts": attempts,
            }
        )

    base = {
        "algorithm": JOINT_ALGORITHM,
        "single": single,
        "candidates": [],
    }
    empty = next(
        (entry for entry, options in zip(single, options_per_task) if not options),
        None,
    )
    if empty is not None:
        incomplete = any(not attempt["search_completed"] for attempt in empty["attempts"])
        if incomplete:
            return {
                **base,
                "status": "search_incomplete",
                "reason": (
                    "任务 " + empty["id"] + " 在可调范围内搜索预算耗尽，"
                    "单条路线可行性尚未证明，联合可行性无法判定"
                ),
                "search_completed": False,
            }
        return {
            **base,
            "status": "task_infeasible",
            "reason": (
                "任务 " + empty["id"] + " 在其出发范围内没有任何单条可行安排："
                + (empty["attempts"][0]["reason"] if empty["attempts"] else "无可选出发时刻")
            ),
            "search_completed": True,
        }

    best_wait = None
    best_span = None
    conflict_tally = {}
    evaluated = 0
    nodes = 0
    incomplete = False

    def visit(index, chosen, acc_occ):
        nonlocal best_wait, best_span, evaluated, nodes, incomplete
        if incomplete:
            return
        nodes += 1
        if nodes > search_budget:
            incomplete = True
            return
        if index == len(options_per_task):
            evaluated += 1
            total_wait = sum(option["wait"] for option in chosen)
            makespan = max(option["arrival"] for option in chosen)
            if best_wait is None or total_wait < best_wait["total_wait"] - _OBJ_EPS:
                best_wait = {"total_wait": total_wait, "makespan": makespan, "options": list(chosen)}
            if best_span is None or makespan < best_span["makespan"] - _OBJ_EPS:
                best_span = {"total_wait": total_wait, "makespan": makespan, "options": list(chosen)}
            return
        for option in options_per_task[index]:
            resource_id = _option_conflicts(option["occupancy"], acc_occ)
            if resource_id is not None:
                conflict_tally[resource_id] = conflict_tally.get(resource_id, 0) + 1
                continue
            visit(index + 1, chosen + [option], _merge_acc(acc_occ, option["occupancy"]))
            if incomplete:
                return

    visit(0, [], {})

    if incomplete:
        return {
            **base,
            "status": "search_incomplete",
            "reason": "联合搜索预算耗尽，尚未证明可行或无解；可增大 search_budget 或缩小可调范围",
            "search_completed": False,
            "nodes_explored": nodes,
        }
    if best_wait is None:
        top = sorted(conflict_tally.items(), key=lambda item: -item[1])[:3]
        detail = "、".join(name + " 冲突 " + str(count) + " 次" for name, count in top) or "无可用组合"
        return {
            **base,
            "status": "joint_infeasible",
            "reason": (
                "已枚举全部出发组合：每条任务单独可行，但冲突区互斥无法满足（"
                + detail
                + "），证实联合无解"
            ),
            "search_completed": True,
            "nodes_explored": nodes,
            "conflict_tally": conflict_tally,
        }

    def package(objective, best):
        return {
            "objective": objective,
            "total_wait": best["total_wait"],
            "makespan": best["makespan"],
            "tasks": [
                {
                    "id": option["task_id"],
                    "departure": option["departure"],
                    "arrival": option["arrival"],
                    "wait": option["wait"],
                    "schedule": option["schedule"],
                    "segments": option["segments"],
                    "replay": option["replay"],
                }
                for option in best["options"]
            ],
        }

    candidates = [package("min_total_wait", best_wait), package("min_makespan", best_span)]
    return {
        **base,
        "status": "feasible",
        "reason": "联合枚举完成：候选组合在所有共享冲突区上互不占用，且逐段重放各自合法",
        "search_completed": True,
        "nodes_explored": nodes,
        "combinations_evaluated": evaluated,
        "candidates": candidates,
    }
