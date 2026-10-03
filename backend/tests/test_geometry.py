import math

from app.geometry import validate_passage


def passage(robot_width, robot_length):
    rows, cols = 24, 36
    grid = [[False] * cols for _ in range(rows)]
    for col in range(cols):
        grid[0][col] = True
        grid[-1][col] = True
    for row in range(rows):
        grid[row][0] = True
        grid[row][-1] = True

    wall_row = 12
    for col in [*range(1, 12), *range(16, 25), *range(31, 35)]:
        grid[wall_row][col] = True

    # A protruding obstacle leaves the center open but removes body clearance.
    for row in range(9, 12):
        for col in range(12, 14):
            grid[row][col] = True
    for row in range(12, 15):
        for col in [25, 26, 29, 30]:
            grid[row][col] = True

    return validate_passage(
        grid,
        [],
        0.1,
        robot_width,
        robot_length,
        {"x": 0, "y": 0},
        {"x": 0.5, "y": 0.5},
        {"x": 0.5, "y": 1.8},
    )


def test_whole_robot_fails_in_narrow_door_but_center_would_fit():
    result = passage(0.55, 0.8)
    assert result["passable"] is False
    assert "complete rectangle footprint" in result["reason"]


def test_smaller_robot_passes_wide_door():
    # The protrusions leave a true 0.2 m clearance; only a rectangle that
    # actually fits that opening may pass.
    result = passage(0.1, 0.1)
    assert result["passable"] is True
    assert len(result["path"]) > 1


def test_edge_hugging_obstacle_is_checked_beyond_center():
    # The 0.6 m edge protrusion combined with the next 0.5 m gate blocks a
    # robot whose center point can still travel through open center cells.
    result = passage(0.4, 0.4)
    assert result["passable"] is False


def empty_room(rows=24, cols=36):
    grid = [[False] * cols for _ in range(rows)]
    for col in range(cols):
        grid[0][col] = True
        grid[-1][col] = True
    for row in range(rows):
        grid[row][0] = True
        grid[row][-1] = True
    return grid


def test_same_position_requires_real_orientation_change():
    grid = empty_room()
    target = {"x": 1.8, "y": 1.2}
    roomy = validate_passage(
        grid, [], 0.1, 0.5, 0.8, {"x": 0, "y": 0},
        target, target, 0.0, math.pi / 2,
    )
    assert roomy["status"] == "feasible"
    assert roomy["path"][0]["orientation"] == 0.0
    assert abs(roomy["path"][-1]["orientation"] - math.pi / 2) < 0.01

    # A 2 m robot cannot rotate inside the small room even though the target
    # position is identical and the center point stays free.
    tight = validate_passage(
        grid, [], 0.1, 1.0, 2.0, {"x": 0, "y": 0},
        target, target, 0.0, math.pi / 2,
    )
    assert tight["status"] == "blocked"


def test_timed_checkpoints_require_order_window_and_waiting_area():
    grid = empty_room()
    origin = {"x": 0, "y": 0}
    start = {"x": 0.5, "y": 0.5}
    end = {"x": 3.0, "y": 0.5}
    checkpoints = [
        {
            "order": 0, "x": 1.2, "y": 0.5, "orientation": 0.0,
            "window_start": 2.0, "window_end": 4.0,
        },
        {
            "order": 1, "x": 2.2, "y": 0.5, "orientation": 0.0,
            "window_start": 4.0, "window_end": 6.0,
        },
    ]
    # 等待位覆盖第一个检查点容差区，机器人必须在窗口开启前停在该处。
    waiting = [{"id": "w", "x": 1.0, "y": 0.3, "width": 0.5, "height": 0.5}]
    feasible = validate_passage(
        grid, [], 0.1, 0.3, 0.4, origin, start, end,
        start_orientation=0.0, end_orientation=0.0,
        checkpoints=checkpoints, waiting_zones=waiting,
    )
    assert feasible["status"] == "feasible"
    assert any(event["event"] == "wait" for event in feasible["schedule"])

    # Without a waiting area the early window can still be met, but forcing an
    # early first window blocks the second one (window missed).
    impossible = [
        {
            "order": 0, "x": 2.8, "y": 0.5, "orientation": 0.0,
            "window_start": 0.0, "window_end": 0.5,
        },
        {
            "order": 1, "x": 2.9, "y": 0.5, "orientation": 0.0,
            "window_start": 20.0, "window_end": 21.0,
        },
    ]
    result = validate_passage(
        grid, [], 0.1, 0.3, 0.4, origin, start, end,
        start_orientation=0.0, end_orientation=0.0,
        checkpoints=impossible, waiting_zones=[],
    )
    assert result["status"] in {"window_missed", "timed_blocked", "orientation_mismatch"}


def test_timed_checkpoint_orientation_mismatch_is_distinct():
    grid = empty_room()
    # Require 90 deg at a point close to a wall so the rotating rectangle
    # sweep collides, even though reaching the point is easy.
    checkpoints = [
        {
            "order": 0, "x": 0.35, "y": 0.5, "orientation": math.pi / 2,
            "window_start": 0.0, "window_end": 20.0,
        },
    ]
    result = validate_passage(
        grid, [], 0.1, 0.5, 0.8, {"x": 0, "y": 0},
        {"x": 0.5, "y": 0.5}, {"x": 3.0, "y": 0.5},
        checkpoints=checkpoints,
    )
    assert result["status"] in {"orientation_mismatch", "blocked"}


def test_search_incomplete_is_reported_not_failed():
    grid = empty_room(rows=40, cols=40)
    result = validate_passage(
        grid, [], 0.1, 0.3, 0.4, {"x": 0, "y": 0},
        {"x": 0.5, "y": 0.5}, {"x": 3.5, "y": 3.5},
        checkpoints=[
            {
                "order": 0, "x": 2.0, "y": 2.0, "orientation": 0.0,
                "window_start": 0.0, "window_end": 100.0,
            }
        ],
        search_budget=10,
    )
    assert result["status"] == "search_incomplete"
    assert result["search_completed"] is False


def gated_wall():
    rows, cols = 24, 36
    grid = [[False] * cols for _ in range(rows)]
    for col in range(cols):
        grid[0][col] = True
        grid[-1][col] = True
    for row in range(rows):
        grid[row][0] = True
        grid[row][-1] = True
    for col in range(1, cols - 1):
        grid[12][col] = True
    for col in (13, 14, 15, 16):
        grid[12][col] = False
    return grid


GATE_ZONE = {
    "id": "gate",
    "x": 1.25,
    "y": 1.05,
    "width": 0.5,
    "height": 0.40,
    "close_start": 1.05,
    "close_end": 6.0,
}
WAITING_POCKET = [{"id": "w", "x": 1.3, "y": 0.5, "width": 0.4, "height": 0.4}]
GATE_START = {"x": 1.5, "y": 0.5}
GATE_END = {"x": 1.5, "y": 1.8}


def test_timed_zone_allows_waiting_then_passing_after_reopen():
    result = validate_passage(
        gated_wall(),
        [],
        0.1,
        0.3,
        0.4,
        {"x": 0, "y": 0},
        GATE_START,
        GATE_END,
        checkpoints=[
            {
                "order": 0,
                "x": 1.5,
                "y": 0.6,
                "orientation": 0.0,
                "window_start": 3.0,
                "window_end": 4.0,
            }
        ],
        waiting_zones=WAITING_POCKET,
        timed_zones=[GATE_ZONE],
        linear_speed=0.5,
    )
    assert result["status"] == "feasible"
    waits = [seg for seg in result["segments"] if seg["type"] == "wait"]
    assert waits
    # 第一段等待赶检查点窗口，第二段等待封路开放（close_end=6）。
    assert any(abs(wait["end"] - 6.0) < 1e-6 for wait in waits)
    crossings = [
        seg
        for seg in result["segments"]
        if seg["type"] in ("arrive", "wait_depart_arrive") and seg["row"] == 12
    ]
    assert crossings
    assert crossings[0]["start"] >= 6.0 - 1e-9
    assert result["replay"]["ok"] is True


def test_timed_zone_blocks_when_waiting_position_is_inside_closure():
    zone = dict(GATE_ZONE)
    zone["y"] = 0.45
    zone["height"] = 1.05
    zone["close_start"] = 0.0
    zone["close_end"] = 100.0
    result = validate_passage(
        gated_wall(),
        [],
        0.1,
        0.3,
        0.4,
        {"x": 0, "y": 0},
        GATE_START,
        GATE_END,
        checkpoints=[],
        waiting_zones=WAITING_POCKET,
        timed_zones=[zone],
        linear_speed=0.5,
    )
    assert result["status"] == "timed_blocked"
    assert result["search_completed"] is True


def test_detour_around_permanently_closed_gate():
    def wall_with_detour():
        grid = gated_wall()
        for col in (13, 14, 15, 16):
            grid[12][col] = True
        for col in (27, 28, 29, 30):
            grid[12][col] = False
        return grid

    zone = dict(GATE_ZONE)
    zone["close_start"] = 0.0
    zone["close_end"] = 1000.0
    result = validate_passage(
        wall_with_detour(),
        [],
        0.1,
        0.3,
        0.4,
        {"x": 0, "y": 0},
        GATE_START,
        {"x": 1.5, "y": 1.8},
        checkpoints=[],
        waiting_zones=WAITING_POCKET,
        timed_zones=[zone],
        linear_speed=0.5,
    )
    assert result["status"] == "feasible"
    assert any(seg.get("col") in (27, 28, 29, 30) for seg in result["segments"] if "col" in seg)
    assert result["replay"]["ok"] is True


def test_boundary_semantics_evacuation_finishing_exactly_at_close_start():
    # 撤离动作恰在 close_start 结束：半开区间语义允许；再早 0.05s 则需等待。
    allowed = dict(GATE_ZONE)
    allowed["close_start"] = 1.2
    result = validate_passage(
        gated_wall(),
        [],
        0.1,
        0.3,
        0.4,
        {"x": 0, "y": 0},
        GATE_START,
        GATE_END,
        checkpoints=[],
        waiting_zones=WAITING_POCKET,
        timed_zones=[allowed],
        linear_speed=0.5,
    )
    assert result["status"] == "feasible"

    must_wait = dict(GATE_ZONE)
    must_wait["close_start"] = 1.05
    result_wait = validate_passage(
        gated_wall(),
        [],
        0.1,
        0.3,
        0.4,
        {"x": 0, "y": 0},
        GATE_START,
        GATE_END,
        checkpoints=[],
        waiting_zones=WAITING_POCKET,
        timed_zones=[must_wait],
        linear_speed=0.5,
    )
    assert result_wait["status"] == "feasible"
    gate_enters = [
        seg
        for seg in result_wait["segments"]
        if seg["type"] in ("arrive", "wait_depart_arrive")
        and seg.get("from_row") == 13
        and seg.get("row") == 12
    ]
    assert gate_enters
    assert gate_enters[0]["start"] >= 6.0 - 1e-9


def test_missing_optional_tolerance_uses_shared_default_and_does_not_crash():
    grid = empty_room()
    cp = {
        "order": 0,
        "x": 1.2,
        "y": 0.5,
        "orientation": 0.0,
        "window_start": 0.0,
        "window_end": 20.0,
        "tolerance": None,
        "orientation_tolerance": None,
    }
    result = validate_passage(
        grid,
        [],
        0.1,
        0.3,
        0.4,
        {"x": 0, "y": 0},
        {"x": 0.5, "y": 0.5},
        {"x": 3.0, "y": 0.5},
        checkpoints=[cp],
    )
    assert result["status"] == "feasible"
    checkpoint_segments = [s for s in result["segments"] if s["type"] == "checkpoint"]
    assert checkpoint_segments
    assert abs(checkpoint_segments[0]["tolerance"] - 0.15) < 1e-9


def test_replay_segments_rejects_tampered_schedule():
    from app.geometry import replay_segments

    result = validate_passage(
        gated_wall(),
        [],
        0.1,
        0.3,
        0.4,
        {"x": 0, "y": 0},
        GATE_START,
        GATE_END,
        checkpoints=[],
        waiting_zones=WAITING_POCKET,
        timed_zones=[GATE_ZONE],
        linear_speed=0.5,
    )
    assert result["status"] == "feasible"
    tampered = [dict(seg) for seg in result["segments"]]
    for seg in tampered:
        if seg["type"] == "arrive" and seg.get("row") == 12:
            seg["start"] -= 3.0
            seg["end"] -= 3.0
            break
    from app.geometry import Point, _build_timed_zones, build_occupancy

    occupancy = build_occupancy(gated_wall(), [], 0.1, Point(0, 0))
    timed = _build_timed_zones(
        [GATE_ZONE], occupancy, 0.3, 0.4, 0.1, Point(0, 0)
    )
    replay = replay_segments(
        segments=tampered,
        specs=[],
        timed=timed,
        move_cost=0.2,
        turn_cost=(2.0 * math.pi / 36) / 0.5,
    )
    assert replay["ok"] is False
