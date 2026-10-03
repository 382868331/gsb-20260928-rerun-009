import httpx
import pytest


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
