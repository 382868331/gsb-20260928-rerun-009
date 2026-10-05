import io

import httpx
import pytest
from PIL import Image


def corridor_png(cols=30, rows=12, corridor_row=6):
    """单行窄通道地图：除中间一行外全部封闭，东西向只能共用这条窄道。"""
    pixels = [[20] * cols for _ in range(rows)]
    for col in range(1, cols - 1):
        pixels[corridor_row][col] = 245
    image = Image.new("L", (cols, rows))
    image.putdata([value for row in pixels for value in row])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
async def client(tmp_path):
    from app import main
    from app.storage import Store

    main.store = Store(tmp_path / "test.db")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def make_corridor_project(client, **state_overrides):
    created = (
        await client.post(
            "/api/projects",
            data={"name": "joint-corridor", "resolution": "0.5"},
            files={"file": ("corridor.png", corridor_png(), "image/png")},
        )
    ).json()
    state = created["state"]
    state["robot_width"] = 0.4
    state["robot_length"] = 0.4
    state["linear_speed"] = 0.5
    state["angular_speed"] = 1.0
    for key, value in state_overrides.items():
        state[key] = value
    await client.put(
        f"/api/projects/{created['id']}", json={"state": state, "note": "corridor"}
    )
    return created["id"], state


def task(task_id, start, end, earliest=0.0, latest=40.0, committed=False, departure=None):
    return {
        "id": task_id,
        "start": {"x": start[0], "y": start[1]},
        "end": {"x": end[0], "y": end[1]},
        "start_orientation": 0.0,
        "earliest_departure": earliest,
        "latest_departure": latest,
        "committed": committed,
        "committed_departure": departure,
    }


WEST = (0.5, 2.5)
EAST = (14.0, 2.5)


@pytest.mark.anyio
async def test_opposite_direction_tasks_are_serialized_in_shared_corridor(client):
    project_id, _ = await make_corridor_project(client)
    payload = {
        "tasks": [
            task("eastbound", WEST, EAST),
            task("westbound", EAST, WEST),
        ]
    }
    response = await client.post(f"/api/projects/{project_id}/joint-plan", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "feasible"
    assert data["search_completed"] is True
    assert data["single_feasible"] == {"eastbound": True, "westbound": True}
    objectives = {candidate["objective"] for candidate in data["candidates"]}
    assert objectives == {"min_total_wait", "min_makespan"}
    for candidate in data["candidates"]:
        assert candidate["conflict_free"] is True
        departures = candidate["departures"]
        # 窄通道不可同时对向占用：一方必须等另一方完全通过（27 段路程）。
        assert abs(departures["eastbound"] - departures["westbound"]) >= 27.0 - 1e-6
        assert candidate["makespan"] >= 54.0 - 1e-6
        assert candidate["total_wait"] > 0.0
        for task_id, detail in candidate["tasks"].items():
            assert detail["status"] == "feasible"
            assert detail["arrival_time"] > detail["departure"]
            assert any(seg["type"] == "arrive" for seg in detail["segments"])
    wait_best = next(c for c in data["candidates"] if c["objective"] == "min_total_wait")
    span_best = next(c for c in data["candidates"] if c["objective"] == "min_makespan")
    assert wait_best["total_wait"] <= span_best["total_wait"] + 1e-9
    assert span_best["makespan"] <= wait_best["makespan"] + 1e-9


@pytest.mark.anyio
async def test_same_direction_tasks_get_staggered_departures(client):
    project_id, _ = await make_corridor_project(client)
    payload = {
        "tasks": [
            task("first", WEST, EAST),
            task("second", (1.0, 2.5), EAST),
        ]
    }
    response = await client.post(f"/api/projects/{project_id}/joint-plan", json=payload)
    data = response.json()
    assert data["status"] == "feasible"
    for candidate in data["candidates"]:
        departures = candidate["departures"]
        assert departures["first"] != departures["second"]


@pytest.mark.anyio
async def test_single_feasible_does_not_imply_joint_feasible(client):
    checkpoint = {
        "id": "mid",
        "order": 0,
        "x": 7.5,
        "y": 2.5,
        "orientation": 0.0,
        "window_start": 0.0,
        "window_end": 30.0,
        "tolerance": None,
        "orientation_tolerance": None,
    }
    project_id, _ = await make_corridor_project(client, checkpoints=[checkpoint])
    payload = {
        "tasks": [
            task("eastbound", WEST, EAST),
            task("westbound", EAST, WEST),
        ]
    }
    response = await client.post(f"/api/projects/{project_id}/joint-plan", json=payload)
    data = response.json()
    # 每条路线单独都能在窗口内通过中点检查点，但窄通道串行后必有一方错过窗口。
    assert data["single_feasible"] == {"eastbound": True, "westbound": True}
    assert data["status"] == "infeasible"
    assert data["search_completed"] is True
    assert data["candidates"] == []
    rejection = data["rejection"]
    assert rejection["task_failure_combinations"] > 0
    assert rejection["conflict_combinations"] > 0
    assert rejection["sample_conflict"]["tasks"] == ["eastbound", "westbound"]
    assert "无解" in data["reason"] or "不存在" in data["reason"]


@pytest.mark.anyio
async def test_search_budget_exhaustion_is_distinct_from_proven_infeasible(client):
    project_id, _ = await make_corridor_project(client)
    payload = {
        "tasks": [
            task("a", WEST, EAST, latest=60.0),
            task("b", EAST, WEST, latest=60.0),
            task("c", (1.0, 2.5), (13.5, 2.5), latest=60.0),
        ],
        "search_budget": 3,
    }
    response = await client.post(f"/api/projects/{project_id}/joint-plan", json=payload)
    data = response.json()
    assert data["status"] == "search_incomplete"
    assert data["search_completed"] is False
    assert data["evaluated_combinations"] == 3
    assert data["total_combinations"] > 3
    for candidate in data["candidates"]:
        assert candidate["provisional"] is True


@pytest.mark.anyio
async def test_committed_task_is_immovable_and_adoption_rechecks(client):
    project_id, state = await make_corridor_project(client)
    versions_before = (await client.get(f"/api/projects/{project_id}/versions")).json()
    payload = {
        "tasks": [
            task("committed-a", WEST, EAST, earliest=5.0, latest=5.0,
                 committed=True, departure=5.0),
            task("flexible-b", EAST, WEST),
        ]
    }
    planned = (
        await client.post(f"/api/projects/{project_id}/joint-plan", json=payload)
    ).json()
    assert planned["status"] == "feasible"
    for candidate in planned["candidates"]:
        assert candidate["departures"]["committed-a"] == 5.0
        assert candidate["departures"]["flexible-b"] >= 32.0 - 1e-6

    listed = (await client.get(f"/api/projects/{project_id}/joint-plans")).json()
    assert len(listed) == 2
    assert all(record["valid"] for record in listed)

    candidate_id = planned["candidates"][0]["candidate_id"]
    adopted = await client.post(
        f"/api/joint-plans/{candidate_id}/adopt",
        json={"commitments": {"committed-a": 5.0}},
    )
    assert adopted.status_code == 200
    assert adopted.json()["adopted"] is True
    assert adopted.json()["recheck"]["status"] == "feasible"

    # 采用候选不修改原任务与工程状态。
    project_after = (await client.get(f"/api/projects/{project_id}")).json()
    assert project_after["state"] == state
    versions_after = (await client.get(f"/api/projects/{project_id}/versions")).json()
    assert len(versions_after) == len(versions_before)

    # 承诺变化只使记录了该承诺的候选失效。
    other_id = planned["candidates"][1]["candidate_id"]
    mismatched = await client.post(
        f"/api/joint-plans/{other_id}/adopt",
        json={"commitments": {"committed-a": 7.0}},
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["detail"]["reason"] == "commitment_changed"
    assert mismatched.json()["detail"]["task_id"] == "committed-a"

    # 不含该承诺任务的候选不受承诺变化影响。
    second = (
        await client.post(
            f"/api/projects/{project_id}/joint-plan",
            json={
                "tasks": [
                    task("flexible-b", EAST, WEST),
                    task("flexible-c", (1.0, 2.5), (13.5, 2.5)),
                ]
            },
        )
    ).json()
    assert second["status"] == "feasible"
    unrelated_id = second["candidates"][0]["candidate_id"]
    unaffected = await client.post(
        f"/api/joint-plans/{unrelated_id}/adopt",
        json={"commitments": {"committed-a": 99.0}},
    )
    assert unaffected.status_code == 200


@pytest.mark.anyio
async def test_mid_edit_closure_invalidates_candidate_with_reason(client):
    project_id, state = await make_corridor_project(client)
    payload = {
        "tasks": [
            task("eastbound", WEST, EAST),
            task("westbound", EAST, WEST),
        ]
    }
    planned = (
        await client.post(f"/api/projects/{project_id}/joint-plan", json=payload)
    ).json()
    assert planned["status"] == "feasible"
    candidate_id = planned["candidates"][0]["candidate_id"]

    # 中途新增封闭：在走廊中段加限时封闭区。
    updated = dict(state)
    updated["timed_zones"] = [
        {
            "id": "mid-closure",
            "x": 6.0,
            "y": 2.0,
            "width": 3.0,
            "height": 1.0,
            "close_start": 10.0,
            "close_end": 20.0,
        }
    ]
    await client.put(f"/api/projects/{project_id}", json={"state": updated, "note": "新增封闭"})

    listed = (await client.get(f"/api/projects/{project_id}/joint-plans")).json()
    stale = next(record for record in listed if record["id"] == candidate_id)
    assert stale["valid"] is False
    assert stale["invalid_reason"] == "map_changed"

    rejected = await client.post(
        f"/api/joint-plans/{candidate_id}/adopt",
        json={"commitments": {}},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["reason"] == "map_changed"


@pytest.mark.anyio
async def test_shared_timed_zone_shapes_joint_passing_times(client):
    closure = {
        "id": "mid-closure",
        "x": 6.0,
        "y": 2.0,
        "width": 3.0,
        "height": 1.0,
        "close_start": 10.0,
        "close_end": 20.0,
    }
    waiting = [
        {"id": "west-pocket", "x": 1.0, "y": 2.0, "width": 2.0, "height": 1.0},
        {"id": "east-pocket", "x": 11.0, "y": 2.0, "width": 2.5, "height": 1.0},
    ]
    project_id, _ = await make_corridor_project(
        client, timed_zones=[closure], waiting_zones=waiting
    )
    payload = {
        "tasks": [
            task("eastbound", WEST, EAST, latest=80.0),
            task("westbound", EAST, WEST, latest=80.0),
        ]
    }
    planned = (
        await client.post(f"/api/projects/{project_id}/joint-plan", json=payload)
    ).json()
    assert planned["status"] == "feasible"
    candidate = next(
        c for c in planned["candidates"] if c["objective"] == "min_total_wait"
    )
    # 封闭区 [10,20) 内不得有任何通行时序：进入封闭段的到达动作只能在开放后。
    for detail in candidate["tasks"].values():
        assert detail["status"] == "feasible"
        for segment in detail["segments"]:
            if segment["type"] not in ("arrive", "wait_depart_arrive"):
                continue
            inside = 6.0 <= segment["x"] <= 9.0 or 6.0 <= segment["from_x"] <= 9.0
            if inside:
                assert segment["start"] >= 20.0 - 1e-9
    # 窄通道同一时刻只容一方：两方的出发时刻必须拉开。
    departures = candidate["departures"]
    assert abs(departures["eastbound"] - departures["westbound"]) > 1.0
