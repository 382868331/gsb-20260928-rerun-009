import httpx
import pytest

from app.geometry import closed_at, intervals_overlap, validate_passage

ORIGIN = {"x": 0.0, "y": 0.0}
START = {"x": 0.5, "y": 0.5}
END = {"x": 3.0, "y": 0.5}
WAITING = [{"id": "w", "x": 0.8, "y": 0.2, "width": 0.4, "height": 0.7}]
CLOSURE = [
    {"id": "c", "x": 1.4, "y": 0.3, "width": 0.3, "height": 0.6,
     "close_start": 1.0, "close_end": 3.0}
]


def corridor_map():
    # 0.5 m straight corridor (rows 16-20) with one waiting alcove; the
    # 0.3 x 0.4 rectangle can neither turn nor loop inside it.
    rows, cols = 24, 36
    grid = [[True] * cols for _ in range(rows)]
    for row in range(16, 21):
        for col in range(1, 35):
            grid[row][col] = False
    for row in range(15, 22):
        for col in range(8, 12):
            grid[row][col] = False
    return grid


def detour_map():
    # Main corridor with a parallel bypass joined at both ends.
    rows, cols = 24, 36
    grid = [[True] * cols for _ in range(rows)]
    for row in range(16, 21):
        for col in range(1, 35):
            grid[row][col] = False
    for row in range(10, 15):
        for col in range(1, 35):
            grid[row][col] = False
    for row in range(10, 21):
        for col in list(range(1, 6)) + list(range(30, 35)):
            grid[row][col] = False
    return grid


def run(grid, **overrides):
    kwargs = dict(
        start_orientation=0.0,
        end_orientation=0.0,
    )
    kwargs.update(overrides)
    return validate_passage(
        grid, [], 0.1, 0.3, 0.4, ORIGIN, START, END, **kwargs
    )


def assert_body_clear_of_closures(result, closures, grid_rows=24, grid_cols=36):
    """Every pose held during a closure must satisfy the same per-layer
    clearance arrays the search itself uses."""
    from app.geometry import Point, build_closure_layers, nearest_orientation_layer

    layers = build_closure_layers(
        closures, grid_rows, grid_cols, 0.1, Point(0.0, 0.0), 0.3, 0.4
    )
    for event in result["schedule"]:
        if event["event"] == "wait":
            spans = [(event["time"], event["wait_until"])]
        else:
            spans = [(event["time"], event["time"])]
        layer = nearest_orientation_layer(event["orientation"])
        for t0, t1 in spans:
            for zone in layers:
                involved = (
                    intervals_overlap(t0, t1, zone["close_start"], zone["close_end"])
                    or closed_at(zone["close_start"], zone["close_end"], t0)
                    or closed_at(zone["close_start"], zone["close_end"], t1)
                )
                if involved:
                    assert bool(zone["free"][layer, event["row"], event["col"]]), (
                        "body inside closure at t=%s: %s" % (t0, event)
                    )


def assert_replay_consistent(result, closures, margin=0.25):
    """The timeline must replay segment by segment and never leave part of
    the body inside a closure while that closure is active."""
    events = result["schedule"]
    assert events[0]["event"] == "start"
    assert result["segments"], "a feasible answer must expose replay segments"
    for segment in result["segments"]:
        assert segment["t_end"] >= segment["t_start"] - 1e-9
        assert segment["action"] in {"move", "turn", "wait", "hold"}
    for previous, current in zip(events, events[1:]):
        if current["event"] == "wait":
            assert current["wait_until"] > current["time"]
        else:
            assert current["time"] >= previous["time"] - 1e-9
    assert_body_clear_of_closures(result, closures)


def test_closure_boundary_semantics():
    # [close_start, close_end): finishing exactly at close_start counts as
    # evacuated, starting exactly at close_end counts as allowed re-entry.
    assert not intervals_overlap(0.8, 1.0, 1.0, 3.0)
    assert intervals_overlap(0.8, 1.2, 1.0, 3.0)
    assert not intervals_overlap(3.0, 3.2, 1.0, 3.0)
    assert intervals_overlap(2.9, 3.2, 1.0, 3.0)
    assert closed_at(1.0, 3.0, 1.0)
    assert not closed_at(1.0, 3.0, 3.0)
    assert not closed_at(1.0, 3.0, 0.5)


def test_closure_forces_wait_and_schedule_replays():
    result = run(corridor_map(), waiting_zones=WAITING, closure_zones=CLOSURE)
    assert result["status"] == "feasible"
    waits = [event for event in result["schedule"] if event["event"] == "wait"]
    assert len(waits) == 1
    # The robot resumes exactly when the closure opens again, not earlier.
    assert waits[0]["wait_until"] == 3.0
    assert waits[0]["time"] < 3.0
    assert_replay_consistent(result, CLOSURE)


def test_closed_waiting_spot_cannot_shelter():
    closures = CLOSURE + [
        {"id": "c2", "x": 0.8, "y": 0.2, "width": 0.4, "height": 0.7,
         "close_start": 0.5, "close_end": 5.0}
    ]
    result = run(corridor_map(), waiting_zones=WAITING, closure_zones=closures)
    assert result["status"] != "feasible"
    assert result["passable"] is False


def test_detour_around_closure_and_kept_geometry_comparison():
    grid = detour_map()
    closure = [
        {"id": "c", "x": 1.4, "y": 0.3, "width": 0.3, "height": 0.6,
         "close_start": 1.0, "close_end": 10.0}
    ]
    baseline = run(grid)
    assert baseline["status"] == "feasible"

    # Keeping the old straight geometry cannot be retimed: the closure needs
    # a stop and no waiting area exists on that route.
    kept = run(
        grid,
        closure_zones=closure,
        keep_geometry=True,
        geometric_path=baseline["path"],
    )
    assert kept["status"] == "unschedulable"
    assert kept["geometry_kept"] is True

    # Replanning the route instead goes around through the bypass.
    reroute = run(grid, closure_zones=closure)
    assert reroute["status"] == "feasible"
    assert any(event["row"] < 15 for event in reroute["schedule"])
    assert_replay_consistent(reroute, closure)


def test_keep_geometry_reschedules_after_window_change():
    grid = corridor_map()
    checkpoints = [
        {"order": 0, "x": 1.2, "y": 0.5, "orientation": 0.0,
         "window_start": 4.0, "window_end": 6.0}
    ]
    baseline = run(grid, checkpoints=checkpoints, waiting_zones=WAITING)
    assert baseline["status"] == "feasible"

    moved = [dict(checkpoints[0], window_start=6.0, window_end=8.0)]
    kept = run(
        grid,
        checkpoints=moved,
        waiting_zones=WAITING,
        keep_geometry=True,
        geometric_path=baseline["path"],
    )
    assert kept["status"] == "feasible"
    assert kept["geometry_kept"] is True
    waits = [event for event in kept["schedule"] if event["event"] == "wait"]
    assert waits and waits[0]["wait_until"] == 6.0

    # The same retiming is impossible once the waiting alcove is removed.
    stripped = run(
        grid,
        checkpoints=moved,
        waiting_zones=[],
        keep_geometry=True,
        geometric_path=baseline["path"],
    )
    assert stripped["status"] == "unschedulable"


def test_keep_geometry_pose_mismatch_when_path_blocked():
    grid = detour_map()
    baseline = run(grid)
    assert baseline["status"] == "feasible"
    forbidden = [{"id": "f", "x": 1.4, "y": 0.3, "width": 0.3, "height": 0.6}]
    kept = validate_passage(
        grid, forbidden, 0.1, 0.3, 0.4, ORIGIN, START, END,
        start_orientation=0.0, end_orientation=0.0,
        keep_geometry=True, geometric_path=baseline["path"],
    )
    assert kept["status"] == "pose_mismatch"
    reroute = validate_passage(
        grid, forbidden, 0.1, 0.3, 0.4, ORIGIN, START, END,
        start_orientation=0.0, end_orientation=0.0,
    )
    assert reroute["status"] == "feasible"


def test_search_budget_reports_incomplete_not_failure():
    result = run(
        corridor_map(),
        checkpoints=[
            {"order": 0, "x": 1.2, "y": 0.5, "orientation": 0.0,
             "window_start": 4.0, "window_end": 6.0}
        ],
        waiting_zones=WAITING,
        closure_zones=CLOSURE,
        search_budget=5,
    )
    assert result["status"] == "search_incomplete"
    assert result["search_completed"] is False


def test_checkpoint_optional_tolerance_defaults_are_shared():
    grid = corridor_map()
    base = {"order": 0, "x": 1.2, "y": 0.5, "orientation": 0.0,
            "window_start": 4.0, "window_end": 6.0}
    missing = run(grid, checkpoints=[dict(base)], waiting_zones=WAITING)
    explicit_none = run(
        grid,
        checkpoints=[dict(base, tolerance=None, orientation_tolerance=None)],
        waiting_zones=WAITING,
    )
    assert missing["status"] == "feasible"
    assert explicit_none["status"] == "feasible"
    assert [e["time"] for e in missing["schedule"]] == [
        e["time"] for e in explicit_none["schedule"]
    ]


@pytest.fixture
async def client(tmp_path):
    from app import main
    from app.storage import Store

    main.store = Store(tmp_path / "test.db")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def fresh_client(tmp_path):
    from app import main
    from app.storage import Store

    main.store = Store(tmp_path / "test.db")
    transport = httpx.ASGITransport(app=main.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def sample_state(project):
    state = project["state"]
    state["start"] = {"x": 0.5, "y": 0.5}
    state["end"] = {"x": 3.0, "y": 0.5}
    state["robot_width"] = 0.1
    state["robot_length"] = 0.1
    state["checkpoints"] = [
        {
            "id": "cp0",
            "order": 0,
            "x": 1.5,
            "y": 0.5,
            "orientation": 0.0,
            "window_start": 0.0,
            "window_end": 100.0,
            # optional tolerance fields deliberately omitted
        }
    ]
    state["closure_zones"] = [
        {
            "id": "roadwork",
            "x": 2.0,
            "y": 1.0,
            "width": 0.3,
            "height": 0.5,
            "close_start": 0.0,
            "close_end": 100.0,
        }
    ]
    return state


@pytest.mark.anyio
async def test_validate_accepts_checkpoint_without_tolerance(client):
    project = (await client.post("/api/projects", data={"name": "null-tol"})).json()
    pid = project["id"]
    state = sample_state(project)
    response = await client.post(f"/api/projects/{pid}/validate", json={"state": state})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "feasible"
    assert result["segments"], "feasible answers must be replayable"


@pytest.mark.anyio
async def test_saved_plan_revalidates_identically_after_reopen(client, tmp_path):
    project = (await client.post("/api/projects", data={"name": "reopen"})).json()
    pid = project["id"]
    state = sample_state(project)
    await client.put(f"/api/projects/{pid}", json={"state": state, "note": "setup"})
    first = (await client.post(f"/api/projects/{pid}/validate", json={"state": state})).json()
    assert first["status"] == "feasible"

    # The saved plan keeps its own map and closure basis.
    plans = (await client.get(f"/api/projects/{pid}/plans")).json()
    assert plans, "validation must persist a plan"
    saved = plans[0]
    assert saved["result"]["map_updated_at"] == first["map_version"]
    assert saved["state"]["closure_zones"] == state["closure_zones"]
    assert saved["result"]["segments"]

    async with await fresh_client(tmp_path) as reopened:
        loaded = (await reopened.get(f"/api/projects/{pid}")).json()
        second = (
            await reopened.post(f"/api/projects/{pid}/validate", json={"state": loaded["state"]})
        ).json()
    assert second["status"] == first["status"]
    assert second["algorithm"] == first["algorithm"]
    assert [e["time"] for e in second["schedule"]] == [
        e["time"] for e in first["schedule"]
    ]
    assert [s["action"] for s in second["segments"]] == [
        s["action"] for s in first["segments"]
    ]
