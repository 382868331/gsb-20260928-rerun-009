import httpx
import pytest


@pytest.mark.anyio
async def test_legacy_project_state_gets_explicit_defaults(client):
    from app import main

    project = (await client.post("/api/projects", data={"name": "legacy"})).json()
    project_id = project["id"]
    legacy_state = {
        "origin": {"x": 0.0, "y": 0.0},
        "forbidden_zones": [],
        "start": {"x": 0.1, "y": 0.1},
        "end": {"x": 0.1, "y": 2.0},
        "robot_width": 0.5,
        "robot_length": 0.8,
    }
    import json

    with main.store.connect() as conn:
        conn.execute(
            "UPDATE projects SET state = ? WHERE id = ?",
            (json.dumps(legacy_state), project_id),
        )
        conn.execute(
            "INSERT INTO versions (project_id, name, state, created_at) VALUES (?, ?, ?, ?)",
            (project_id, "旧版存档", json.dumps(legacy_state), "2026-01-01T00:00:00+00:00"),
        )

    loaded = (await client.get(f"/api/projects/{project_id}")).json()
    assert loaded["state"]["checkpoints"] == []
    assert loaded["state"]["timed_zones"] == []
    assert loaded["state"]["waiting_zones"] == []
    assert loaded["state"]["start_orientation"] == 0.0
    assert "checkpoints" in loaded["state_defaults"]
    assert "timed_zones" in loaded["state_defaults"]

    versions = (await client.get(f"/api/projects/{project_id}/versions")).json()
    legacy_version = next(item for item in versions if item["name"] == "旧版存档")
    restored = (await client.post(f"/api/versions/{legacy_version['id']}/restore")).json()
    assert restored["state"]["checkpoints"] == []
    assert "checkpoints" in restored["state_defaults"]


@pytest.fixture
async def client(tmp_path):
    from app import main
    from app.storage import Store

    main.store = Store(tmp_path / "test.db")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def refresh_client(tmp_path):
    from app import main
    from app.storage import Store

    main.store = Store(tmp_path / "test.db")
    transport = httpx.ASGITransport(app=main.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.anyio
async def test_project_persistence_keeps_validation_result(client, tmp_path):
    created = (await client.post(
        "/api/projects",
        data={"name": "persist", "resolution": "0.1"},
    )).json()
    project_id = created["id"]
    state = created["state"]
    state["robot_width"] = 0.55
    state["robot_length"] = 0.8

    first = (await client.post(
        f"/api/projects/{project_id}/validate", json={"state": state}
    )).json()
    assert first["passable"] is False

    reopened = (await client.get(f"/api/projects/{project_id}")).json()
    assert reopened["grid"] == created["grid"]
    assert (await client.get(f"/api/projects/{project_id}/original")).status_code == 200

    await client.put(
        f"/api/projects/{project_id}", json={"state": state, "note": "revision"}
    )
    async with await refresh_client(tmp_path) as reopened_client:
        loaded = (await reopened_client.get(f"/api/projects/{project_id}")).json()
        second = (await reopened_client.post(
            f"/api/projects/{project_id}/validate", json={"state": loaded["state"]}
        )).json()
    assert second["passable"] == first["passable"]
    assert second["algorithm"] == first["algorithm"]


@pytest.mark.anyio
async def test_versions_preserve_original_and_restore_state(client):
    project = (await client.post("/api/projects", data={"name": "versions"})).json()
    state = project["state"]
    state["origin"] = {"x": -2.5, "y": 1.0}
    version = (await client.post(
        f"/api/projects/{project['id']}/versions",
        json={"name": "移动原点", "state": state},
    )).json()
    modified = (await client.get(f"/api/projects/{project['id']}")).json()
    assert modified["state"]["origin"] == {"x": -2.5, "y": 1.0}

    restored = (await client.post(f"/api/versions/{version['id']}/restore")).json()
    assert restored["state"]["origin"] == {"x": -2.5, "y": 1.0}
    original = await client.get(f"/api/projects/{project['id']}/original")
    assert original.headers["content-type"] == "image/png"


@pytest.mark.anyio
async def test_checkpoint_without_optional_tolerance_validates(client):
    project = (await client.post("/api/projects", data={"name": "cp"})).json()
    state = project["state"]
    state["start"] = {"x": 0.5, "y": 0.5}
    state["end"] = {"x": 3.0, "y": 0.5}
    state["robot_width"] = 0.1
    state["robot_length"] = 0.1
    state["start_orientation"] = 0.0
    state["end_orientation"] = 0.0
    state["linear_speed"] = 0.5
    state["angular_speed"] = 0.5
    state["checkpoints"] = [
        {
            "id": "c0",
            "order": 0,
            "x": 1.5,
            "y": 0.5,
            "orientation": 0.0,
            "window_start": 0.0,
            "window_end": 20.0,
            "tolerance": None,
            "orientation_tolerance": None,
        }
    ]
    response = await client.post(
        f"/api/projects/{project['id']}/validate", json={"state": state}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "feasible"
    assert data["replay"]["ok"] is True
    assert any(seg["type"] == "checkpoint" for seg in data["segments"])


@pytest.mark.anyio
async def test_saved_plan_replay_is_consistent_after_reopen(client, tmp_path):
    project = (await client.post("/api/projects", data={"name": "replay"})).json()
    state = project["state"]
    state["start"] = {"x": 1.4, "y": 0.5}
    state["end"] = {"x": 2.8, "y": 1.8}
    state["robot_width"] = 0.1
    state["robot_length"] = 0.1
    state["linear_speed"] = 0.5
    state["angular_speed"] = 0.5
    state["waiting_zones"] = [
        {"id": "w", "x": 1.2, "y": 0.9, "width": 0.4, "height": 0.3}
    ]
    state["timed_zones"] = [
        {
            "id": "gate",
            "x": 1.1,
            "y": 1.05,
            "width": 0.6,
            "height": 0.4,
            "close_start": 1.05,
            "close_end": 6.0,
        }
    ]
    state["checkpoints"] = [
        {
            "id": "c0",
            "order": 0,
            "x": 1.4,
            "y": 1.0,
            "orientation": 0.0,
            "window_start": 3.0,
            "window_end": 4.0,
            "tolerance": None,
            "orientation_tolerance": None,
        }
    ]
    validated = (
        await client.post(
            f"/api/projects/{project['id']}/validate", json={"state": state}
        )
    ).json()
    assert validated["status"] == "feasible"

    plans = (await client.get(f"/api/projects/{project['id']}/plans")).json()
    plan_id = plans[0]["id"]

    async with await refresh_client(tmp_path) as reopened_client:
        replay = (
            await reopened_client.post(f"/api/plans/{plan_id}/replay")
        ).json()
    assert replay["consistent"] is True
    assert replay["saved_status"] == "feasible"
    assert replay["fresh_status"] == "feasible"
    assert replay["replay"]["ok"] is True


@pytest.mark.anyio
async def test_rejected_plan_persists_verdict_reason_and_path(client):
    project = (await client.post("/api/projects", data={"name": "reject"})).json()
    state = project["state"]
    # 宽窄门示例中 0.55 x 0.8 的机器人在窄门处整机外廓无法通过。
    state["robot_width"] = 0.55
    state["robot_length"] = 0.8
    validated = (
        await client.post(
            f"/api/projects/{project['id']}/validate", json={"state": state}
        )
    ).json()
    assert validated["passable"] is False
    assert validated["search_completed"] is True
    assert validated["reason"]
    assert validated["algorithm"]

    plans = (await client.get(f"/api/projects/{project['id']}/plans")).json()
    saved = plans[0]
    assert saved["result_complete"] is True
    assert saved["missing_fields"] == []
    assert saved["map_changed"] is False
    result = saved["result"]
    assert result["passable"] is False
    assert result["status"] == validated["status"]
    assert result["reason"] == validated["reason"]
    assert "path" in result
    assert result["search_completed"] is True
    assert result["robot_params"]["robot_width"] == 0.55
    assert result["map_version"] == project["updated_at"]

    detail = (await client.get(f"/api/plans/{saved['id']}")).json()
    assert detail["result"]["reason"] == validated["reason"]

    replay = (await client.post(f"/api/plans/{saved['id']}/replay")).json()
    assert replay["consistent"] is True
    assert replay["saved_passable"] is False
    assert replay["fresh_passable"] is False
    assert replay["saved_reason"] == validated["reason"]
    assert replay["map_changed"] is False
    assert replay["map_snapshot_missing"] is False


@pytest.mark.anyio
async def test_search_incomplete_plan_persists_unknown_state(client, tmp_path):
    import json as json_module

    from app import main

    project = (await client.post("/api/projects", data={"name": "budget"})).json()
    state = project["state"]
    # 换成 40x40 空房间，确保预算内确实无法完成搜索（而不是提前撞到障碍）。
    room = [[False] * 40 for _ in range(40)]
    for col in range(40):
        room[0][col] = True
        room[-1][col] = True
    for row in range(40):
        room[row][0] = True
        room[row][-1] = True
    with main.store.connect() as conn:
        conn.execute(
            "UPDATE projects SET map_data = ? WHERE id = ?",
            (json_module.dumps({"grid": room}), project["id"]),
        )
    state["start"] = {"x": 0.5, "y": 0.5}
    state["end"] = {"x": 3.5, "y": 3.5}
    state["robot_width"] = 0.3
    state["robot_length"] = 0.4
    state["checkpoints"] = [
        {
            "id": "c0",
            "order": 0,
            "x": 2.0,
            "y": 2.0,
            "orientation": 0.0,
            "window_start": 0.0,
            "window_end": 100.0,
            "tolerance": None,
            "orientation_tolerance": None,
        }
    ]
    validated = (
        await client.post(
            f"/api/projects/{project['id']}/validate",
            json={"state": state, "search_budget": 10},
        )
    ).json()
    assert validated["status"] == "search_incomplete"
    assert validated["passable"] is False
    assert validated["search_completed"] is False
    assert "budget" in validated["reason"]

    async with await refresh_client(tmp_path) as reopened_client:
        plans = (
            await reopened_client.get(f"/api/projects/{project['id']}/plans")
        ).json()
        saved_result = plans[0]["result"]
        assert plans[0]["result_complete"] is True
        assert saved_result["status"] == "search_incomplete"
        assert saved_result["search_completed"] is False
        assert saved_result["reason"] == validated["reason"]

        replay = (
            await reopened_client.post(f"/api/plans/{plans[0]['id']}/replay")
        ).json()
    assert replay["saved_search_completed"] is False
    assert replay["fresh_search_completed"] is False
    assert replay["fresh_status"] == "search_incomplete"
    assert replay["consistent"] is True


@pytest.mark.anyio
async def test_report_is_marked_stale_after_grid_changes(client):
    import json as json_module

    from app import main

    project = (await client.post("/api/projects", data={"name": "stale"})).json()
    state = project["state"]
    state["start"] = {"x": 1.4, "y": 0.5}
    state["end"] = {"x": 2.8, "y": 1.8}
    state["robot_width"] = 0.1
    state["robot_length"] = 0.1
    validated = (
        await client.post(
            f"/api/projects/{project['id']}/validate", json={"state": state}
        )
    ).json()
    assert validated["status"] == "feasible"

    # 地图修订后来发生变化（改栅格），但保存报告与状态快照保持原样。
    row = main.store.get_project(project["id"])
    changed_grid = json_module.loads(row["map_data"])["grid"]
    changed_grid[5][10] = not changed_grid[5][10]
    with main.store.connect() as conn:
        conn.execute(
            "UPDATE projects SET map_data = ?, updated_at = ? WHERE id = ?",
            (
                json_module.dumps({"grid": changed_grid}),
                "2027-01-01T00:00:00+00:00",
                project["id"],
            ),
        )

    plans = (await client.get(f"/api/projects/{project['id']}/plans")).json()
    assert plans[0]["map_changed"] is True
    assert plans[0]["result_complete"] is True
    # 旧报告的结论不能被改写成新地图的结果。
    assert plans[0]["result"]["status"] == "feasible"
    assert plans[0]["map_version"] == project["updated_at"]
    assert plans[0]["current_map_version"] == "2027-01-01T00:00:00+00:00"

    replay = (await client.post(f"/api/plans/{plans[0]['id']}/replay")).json()
    assert replay["map_changed"] is True
    # 重放基于保存时地图快照，原理由与结论保持一致。
    assert replay["consistent"] is True
    assert replay["saved_status"] == "feasible"
    assert replay["fresh_status"] == "feasible"
    assert replay["saved_reason"] == validated["reason"]


@pytest.mark.anyio
async def test_legacy_plan_missing_fields_is_not_rewritten_as_new_verdict(client):
    import json as json_module

    from app import main

    project = (await client.post("/api/projects", data={"name": "legacy-plan"})).json()
    state = project["state"]
    with main.store.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO plans
            (project_id, result, state, map_grid, map_resolution,
             map_version, map_fingerprint, created_at)
            VALUES (?, ?, ?, NULL, NULL, NULL, NULL, ?)
            """,
            (
                project["id"],
                json_module.dumps(
                    {
                        "status": "feasible",
                        "schedule": [],
                        "segments": [],
                        "algorithm": "legacy-v0",
                    }
                ),
                json_module.dumps(state),
                "2026-01-01T00:00:00+00:00",
            ),
        )
        plan_id = cursor.lastrowid

    loaded = (await client.get(f"/api/plans/{plan_id}")).json()
    assert loaded["result_complete"] is False
    assert set(loaded["missing_fields"]) >= {"passable", "reason", "path"}
    assert loaded["map_snapshot_missing"] is True
    assert "passable" not in loaded["result"]

    replay = (await client.post(f"/api/plans/{plan_id}/replay")).json()
    assert replay["result_complete"] is False
    assert replay["consistent"] is False
    assert replay["saved_passable"] is None
    assert replay["map_snapshot_missing"] is True
