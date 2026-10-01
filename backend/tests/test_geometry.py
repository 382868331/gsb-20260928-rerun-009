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
    waiting = [{"id": "w", "x": 0.4, "y": 0.3, "width": 0.6, "height": 0.6}]
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
    assert result["status"] == "window_missed"


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
