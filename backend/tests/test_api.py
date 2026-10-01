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
async def test_timed_plan_and_rejudge_flow(client):
    project = (await client.post("/api/projects", data={"name": "timed"})).json()
    state = project["state"]
    state["robot_width"] = 0.2
    state["robot_length"] = 0.3
    state["start"] = {"x": 0.2, "y": 0.2}
    state["end"] = {"x": 0.2, "y": 2.0}
    state["checkpoints"] = [
        {
            "id": "cp1",
            "name": "窄门",
            "x": 0.2,
            "y": 0.8,
            "orientation": 0.0,
            "earliest": 0.0,
            "latest": 60.0,
        }
    ]
    planned = (await client.post(
        f"/api/projects/{project['id']}/timed-plan",
        json={"state": state, "name": "初始方案"},
    )).json()
    assert planned["status"] == "ok"
    assert planned["schedule"][0]["name"] == "窄门"
    plan_id = planned["plan_id"]

    plans = (await client.get(f"/api/projects/{project['id']}/plans")).json()
    assert plans[0]["status"] == "ok"

    loaded = (await client.get(f"/api/plans/{plan_id}")).json()
    assert loaded["map_revision"]
    assert loaded["result"]["schedule"]

    new_windows = [dict(state["checkpoints"][0], earliest=0.0, latest=0.01)]
    rejudged = (await client.post(
        f"/api/plans/{plan_id}/rejudge", json={"checkpoints": new_windows}
    )).json()
    assert rejudged["kept"]["status"] == "window_missed"
    assert rejudged["adjusted"]["status"] == "window_missed"

    relaxed = [dict(state["checkpoints"][0], earliest=5.0, latest=60.0)]
    rejudged_ok = (await client.post(
        f"/api/plans/{plan_id}/rejudge", json={"checkpoints": relaxed}
    )).json()
    assert rejudged_ok["kept"]["status"] == "ok"
    assert rejudged_ok["kept"]["schedule"][0]["wait"] > 0

    plans_after = (await client.get(f"/api/projects/{project['id']}/plans")).json()
    assert len(plans_after) == 5
