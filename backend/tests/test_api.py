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
async def test_each_dimension_and_map_revision_keeps_its_own_check(client):
    project = (await client.post(
        "/api/projects",
        data={"name": "checks", "resolution": "0.1", "sample_kind": "turn"},
    )).json()
    project_id = project["id"]
    blocked_state = project["state"]
    blocked_state["start"] = {"x": 0.4, "y": 2.7}
    blocked_state["end"] = {"x": 1.8, "y": 0.6}
    blocked_state["start_orientation"] = 0
    blocked_state["end_orientation"] = 90

    blocked = (await client.post(
        f"/api/projects/{project_id}/validate", json={"state": blocked_state}
    )).json()
    assert blocked["passable"] is False

    smaller = dict(blocked_state)
    smaller["robot_width"] = 0.2
    smaller["robot_length"] = 0.3
    passing = (await client.post(
        f"/api/projects/{project_id}/validate", json={"state": smaller}
    )).json()
    assert passing["passable"] is True

    checks = (await client.get(f"/api/projects/{project_id}/checks")).json()
    assert len(checks) == 2
    assert checks[0]["state"]["robot_width"] == 0.2
    assert checks[0]["passable"] is True
    assert checks[1]["state"]["robot_width"] == 0.6
    assert checks[1]["passable"] is False
    assert checks[0]["result"]["algorithm"] == passing["algorithm"]


@pytest.mark.anyio
async def test_turn_sample_reverse_check_is_metric_stable(client):
    project = (await client.post(
        "/api/projects",
        data={"name": "reverse", "resolution": "0.1", "sample_kind": "turn"},
    )).json()
    state = project["state"]
    state["start"] = {"x": 0.4, "y": 2.7}
    state["end"] = {"x": 1.8, "y": 2.7}
    state["start_orientation"] = 180
    state["end_orientation"] = 180
    result = (await client.post(
        f"/api/projects/{project['id']}/validate", json={"state": state}
    )).json()
    assert result["passable"] is True
    reloaded = (await client.get(f"/api/projects/{project['id']}")).json()
    assert reloaded["resolution"] == 0.1
