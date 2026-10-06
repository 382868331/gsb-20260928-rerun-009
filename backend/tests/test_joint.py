import io
import json
import math

import httpx
import pytest
from PIL import Image

from app.joint import plan_joint


@pytest.fixture
async def client(tmp_path):
    from app import main
    from app.storage import Store

    main.store = Store(tmp_path / "test.db")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


def corridor_png(cols=40, rows=12):
    pixels = [[255] * cols for _ in range(rows)]
    for col in range(cols):
        pixels[0][col] = 0
        pixels[-1][col] = 0
    for row in range(rows):
        pixels[row][0] = 0
        pixels[row][-1] = 0
    image = Image.new("L", (cols, rows))
    image.putdata([value for row in pixels for value in row])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def base_state(**overrides):
    state = {
        "origin": {"x": 0.0, "y": 0.0},
        "forbidden_zones": [],
        "timed_zones": [],
        "waiting_zones": [],
        "checkpoints": [],
        "start": {"x": 0.3, "y": 0.5},
        "end": {"x": 3.7, "y": 0.5},
        "robot_width": 0.1,
        "robot_length": 0.1,
        "start_orientation": 0.0,
        "end_orientation": None,
        "linear_speed": 0.5,
        "angular_speed": 0.5,
        "start_time": 0.0,
    }
    state.update(overrides)
    return state


SHARED_ZONE = {"id": "narrows", "x": 1.9, "y": 0.4, "width": 0.2, "height": 0.2}


def task_a(**overrides):
    task = {
        "id": "A",
        "state": base_state(),
        "committed": False,
        "adjust_start": 0.0,
        "adjust_end": 20.0,
        "adjust_step": 1.0,
    }
    task.update(overrides)
    return task


def task_b(**overrides):
    task = {
        "id": "B",
        "state": base_state(
            start={"x": 3.7, "y": 0.5},
            end={"x": 0.3, "y": 0.5},
            start_orientation=math.pi,
        ),
        "committed": True,
        "departure": 0.0,
    }
    task.update(overrides)
    return task


async def make_project(client):
    created = (
        await client.post(
            "/api/projects",
            data={"name": "corridor", "resolution": "0.1"},
            files={"file": ("corridor.png", corridor_png(), "image/png")},
        )
    ).json()
    return created["id"]


def zone_intervals(candidate, task_id):
    """从候选的逐段时序中还原共享区占用，用于独立核对互斥。"""

    from app.geometry import Point, _build_timed_zones, build_occupancy
    from app.joint import _segment_occupancy

    occupancy = build_occupancy(
        [[False] * 40 for _ in range(12)], [], 0.1, Point(0.0, 0.0)
    )
    resources = _build_timed_zones(
        [dict(SHARED_ZONE, close_start=0.0, close_end=1e18)],
        occupancy,
        0.1,
        0.1,
        0.1,
        Point(0.0, 0.0),
    )
    for resource in resources:
        resource["resource_id"] = "shared:narrows"
    segments = next(
        task["segments"] for task in candidate["tasks"] if task["id"] == task_id
    )
    return _segment_occupancy(segments, resources, 0.1, 12, Point(0.0, 0.0))[
        "shared:narrows"
    ]


@pytest.mark.anyio
async def test_joint_plan_serializes_opposite_direction_tasks(client):
    project_id = await make_project(client)
    before = (await client.get(f"/api/projects/{project_id}")).json()

    result = (
        await client.post(
            f"/api/projects/{project_id}/joint-plan",
            json={
                "tasks": [task_a(), task_b()],
                "shared_zones": [SHARED_ZONE],
            },
        )
    ).json()
    assert result["status"] == "feasible"
    assert result["search_completed"] is True
    # 两条任务单独在 0 时刻各自可行，但 0 时刻同时出发会在狭窄共享区相撞。
    single = {entry["id"]: entry for entry in result["single"]}
    assert 0.0 in single["A"]["feasible_departures"]
    assert single["B"]["feasible_departures"] == [0.0]

    objectives = {c["objective"]: c for c in result["candidates"]}
    assert set(objectives) == {"min_total_wait", "min_makespan"}
    best = objectives["min_makespan"]
    departures = {task["id"]: task["departure"] for task in best["tasks"]}
    assert departures["B"] == 0.0
    # A 必须推迟到 B 完全驶出共享区之后才能进入。
    assert departures["A"] >= 0.8
    # 独立重算占用区间：两任务在共享区互不重叠。
    occ_a = zone_intervals(best, "A")
    occ_b = zone_intervals(best, "B")
    for start_a, end_a in occ_a:
        for start_b, end_b in occ_b:
            assert start_a >= end_b - 1e-9 or start_b >= end_a - 1e-9
    for candidate in result["candidates"]:
        for task in candidate["tasks"]:
            if task["replay"] is not None:
                assert task["replay"]["ok"] is True

    # 规划只保存候选，不改写工程里任何任务的原始状态。
    after = (await client.get(f"/api/projects/{project_id}")).json()
    assert after["state"] == before["state"]


@pytest.mark.anyio
async def test_joint_infeasible_and_budget_are_distinct(client):
    project_id = await make_project(client)
    # 两任务都被钉在同一出发时刻：各自可行但证实联合无解。
    stuck_a = task_a(adjust_start=0.0, adjust_end=0.0)
    proved = (
        await client.post(
            f"/api/projects/{project_id}/joint-plan",
            json={"tasks": [stuck_a, task_b()], "shared_zones": [SHARED_ZONE]},
        )
    ).json()
    assert proved["status"] == "joint_infeasible"
    assert proved["search_completed"] is True
    assert "shared:narrows" in proved["conflict_tally"]
    assert "证实联合无解" in proved["reason"]

    # 预算不足：既不报可行也不报无解。
    starved = (
        await client.post(
            f"/api/projects/{project_id}/joint-plan",
            json={
                "tasks": [task_a(), task_b()],
                "shared_zones": [SHARED_ZONE],
                "search_budget": 1,
            },
        )
    ).json()
    assert starved["status"] == "search_incomplete"
    assert starved["search_completed"] is False


@pytest.mark.anyio
async def test_candidate_adoption_rechecks_map_and_commitments(client):
    project_id = await make_project(client)
    payload = {"tasks": [task_a(), task_b()], "shared_zones": [SHARED_ZONE]}
    planned = (
        await client.post(f"/api/projects/{project_id}/joint-plan", json=payload)
    ).json()
    candidate_id = planned["candidate_id"]
    assert candidate_id is not None

    listed = (await client.get(f"/api/projects/{project_id}/joint-candidates")).json()
    assert listed[0]["stale"] is False

    adopted = (
        await client.post(
            f"/api/joint-candidates/{candidate_id}/adopt",
            json={"candidate": "min_makespan", **payload},
        )
    ).json()
    assert adopted["adopted"] is True
    assert adopted["recheck"]["commitments_ok"] is True

    # 已承诺任务出发时刻被移动：采用前核对拒绝。
    moved = task_b(departure=2.0)
    rejected = await client.post(
        f"/api/joint-candidates/{candidate_id}/adopt",
        json={"candidate": "min_makespan", "tasks": [task_a(), moved], "shared_zones": [SHARED_ZONE]},
    )
    assert rejected.status_code == 409
    assert "commitment_changed" in rejected.json()["detail"]["failures"]

    # 中途新增封闭：环境指纹变化，既有候选失效但其它候选不受影响。
    closed_lane = task_a()
    closed_lane["state"]["timed_zones"] = [
        {
            "id": "mid-closure",
            "x": 2.4,
            "y": 0.1,
            "width": 0.2,
            "height": 1.0,
            "close_start": 3.0,
            "close_end": 5.0,
        }
    ]
    rejected = await client.post(
        f"/api/joint-candidates/{candidate_id}/adopt",
        json={
            "candidate": "min_makespan",
            "tasks": [closed_lane, task_b()],
            "shared_zones": [SHARED_ZONE],
        },
    )
    assert rejected.status_code == 409
    assert "environment_changed" in rejected.json()["detail"]["failures"]

    # 新增封闭后重新规划：A 的原定通行时段被封，给出新的合法时序。
    replanned = (
        await client.post(
            f"/api/projects/{project_id}/joint-plan",
            json={"tasks": [closed_lane, task_b()], "shared_zones": [SHARED_ZONE]},
        )
    ).json()
    assert replanned["status"] == "feasible"
    best = {c["objective"]: c for c in replanned["candidates"]}["min_makespan"]
    task_a_plan = next(task for task in best["tasks"] if task["id"] == "A")
    for segment in task_a_plan["segments"]:
        if segment["type"] in ("arrive", "wait_depart_arrive", "wait", "turn"):
            start = segment.get("start", 0.0)
            end = segment.get("end", 0.0)
            x = segment.get("x", 0.0)
            in_closure = 2.4 - 1e-9 <= x <= 2.6 + 1e-9
            if in_closure:
                assert end <= 3.0 + 1e-9 or start >= 5.0 - 1e-9

    # 把唯一车道彻底封死：拒绝原因来自单条任务的封路判定。
    fully_closed = task_a()
    fully_closed["state"]["timed_zones"] = [
        {
            "id": "mid-closure",
            "x": 2.4,
            "y": 0.1,
            "width": 0.2,
            "height": 1.0,
            "close_start": 0.0,
            "close_end": 1000.0,
        }
    ]
    blocked = (
        await client.post(
            f"/api/projects/{project_id}/joint-plan",
            json={"tasks": [fully_closed, task_b()], "shared_zones": [SHARED_ZONE]},
        )
    ).json()
    assert blocked["status"] == "task_infeasible"
    assert blocked["search_completed"] is True
    assert "A" in blocked["reason"]


@pytest.mark.anyio
async def test_map_change_marks_only_related_candidates_stale(client, tmp_path):
    from app import main

    project_id = await make_project(client)
    payload = {"tasks": [task_a(), task_b()], "shared_zones": [SHARED_ZONE]}
    planned = (
        await client.post(f"/api/projects/{project_id}/joint-plan", json=payload)
    ).json()
    candidate_id = planned["candidate_id"]

    # 直接改写库里的栅格（模拟地图修订），候选应被标记失效。
    row = main.store.get_project(project_id)
    map_data = json.loads(row["map_data"])
    map_data["grid"][6][20] = True
    with main.store.connect() as conn:
        conn.execute(
            "UPDATE projects SET map_data = ? WHERE id = ?",
            (json.dumps(map_data), project_id),
        )

    listed = (await client.get(f"/api/projects/{project_id}/joint-candidates")).json()
    assert listed[0]["stale"] is True
    assert "map_changed" in listed[0]["stale_reasons"]

    rejected = await client.post(
        f"/api/joint-candidates/{candidate_id}/adopt",
        json={"candidate": "min_makespan", **payload},
    )
    assert rejected.status_code == 409
    assert "map_changed" in rejected.json()["detail"]["failures"]


def test_objectives_min_wait_and_min_makespan_differ():
    rows, cols = 30, 30
    grid = [[False] * cols for _ in range(rows)]
    for col in range(cols):
        grid[0][col] = True
        grid[-1][col] = True
    for row in range(rows):
        grid[row][0] = True
        grid[row][-1] = True

    committed = {
        "id": "fixed",
        "state": base_state(
            start={"x": 0.2, "y": 2.7},
            end={"x": 1.2, "y": 2.7},
            linear_speed=0.1,
        ),
        "committed": True,
        "departure": 0.0,
    }
    adjustable = {
        "id": "flex",
        "state": base_state(
            start={"x": 1.2, "y": 0.2},
            end={"x": 1.2, "y": 2.2},
            start_orientation=math.pi / 2,
            waiting_zones=[
                {"id": "hold", "x": 1.1, "y": 1.1, "width": 0.2, "height": 0.2}
            ],
            checkpoints=[
                {
                    "id": "cp",
                    "order": 0,
                    "x": 1.2,
                    "y": 1.2,
                    "orientation": math.pi / 2,
                    "window_start": 8.0,
                    "window_end": 20.0,
                    "tolerance": 0.0,
                    "orientation_tolerance": None,
                }
            ],
        ),
        "committed": False,
        "adjust_start": 0.0,
        "adjust_end": 10.0,
        "adjust_step": 1.0,
    }
    result = plan_joint(
        grid=grid, resolution=0.1, tasks=[committed, adjustable], shared_zones=[]
    )
    assert result["status"] == "feasible"
    objectives = {c["objective"]: c for c in result["candidates"]}
    min_wait = objectives["min_total_wait"]
    min_span = objectives["min_makespan"]
    flex_wait = next(task for task in min_wait["tasks"] if task["id"] == "flex")
    flex_span = next(task for task in min_span["tasks"] if task["id"] == "flex")
    # 总等待最少：等到窗口开启再出发，不在检查点前空等。
    assert flex_wait["wait"] == pytest.approx(0.0)
    assert flex_wait["departure"] == pytest.approx(6.0)
    # 最晚完成时间最小：两者完成时间相同，但总等待更差。
    assert min_span["makespan"] == pytest.approx(min_wait["makespan"])
    assert flex_span["wait"] > flex_wait["wait"]
