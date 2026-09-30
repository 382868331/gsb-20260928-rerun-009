import math

from app.geometry import validate_passage


def blank_grid(rows=30, cols=40):
    grid = [[False] * cols for _ in range(rows)]
    for col in range(cols):
        grid[0][col] = True
        grid[-1][col] = True
    for row in range(rows):
        grid[row][0] = True
        grid[row][-1] = True
    return grid


def hwall(grid, row, cols):
    for col in cols:
        grid[row][col] = True


def vwall(grid, col, rows):
    for row in rows:
        grid[row][col] = True


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
    assert result["status"] in {
        "passage-blocked",
        "route-not-found",
        "start-invalid",
    }


def test_smaller_robot_passes_wide_door():
    # The bundled sample doors are tight; use an open 0.8 m gate instead.
    grid = blank_grid(rows=30, cols=30)
    hwall(grid, 15, [c for c in range(1, 29) if c not in range(10, 18)])
    result = validate_passage(
        grid, [], 0.1, 0.2, 0.3, {"x": 0, "y": 0},
        {"x": 1.3, "y": 1.0},
        {"x": 1.3, "y": 2.2},
    )
    assert result["passable"] is True
    assert len(result["path"]) > 1


def test_edge_hugging_obstacle_is_checked_beyond_center():
    # The 0.6 m edge protrusion combined with the next 0.5 m gate blocks a
    # robot whose center point can still travel through open center cells.
    result = passage(0.4, 0.4)
    assert result["passable"] is False


# Non-square robot: 0.4 m wide, 0.9 m long.
W, L = 0.4, 0.9


def test_same_point_turn_succeeds_in_open_area_and_ends_at_end_heading():
    grid = blank_grid()
    result = validate_passage(
        grid, [], 0.1, W, L, {"x": 0, "y": 0},
        {"x": 1.5, "y": 1.5},
        {"x": 1.5, "y": 1.5},
        start_orientation=0.0,
        end_orientation=math.pi / 2,
    )
    assert result["passable"] is True
    assert abs(result["path"][-1]["orientation"] - math.pi / 2) < 0.2


def test_same_point_different_heading_fails_when_rotation_clips_obstacle():
    grid = blank_grid(rows=30, cols=40)
    # Tight pocket: open interior x in [0.1, 1.1], y in [0.1, 1.5].
    vwall(grid, 11, range(15, 29))
    hwall(grid, 15, range(1, 12))
    result = validate_passage(
        grid, [], 0.1, W, L, {"x": 0, "y": 0},
        {"x": 0.6, "y": 0.6},
        {"x": 0.6, "y": 0.6},
        start_orientation=0.0,
        end_orientation=math.pi / 2,
    )
    assert result["passable"] is False
    assert result["status"] in {
        "orientation-unreachable",
        "passage-blocked",
        "start-invalid",
    }
    assert result["path"] == []


def _gate_map(gap_cols):
    grid = blank_grid(rows=40, cols=40)
    wall_row = 20
    hwall(grid, wall_row, [c for c in range(1, 39) if c not in gap_cols])
    return grid, wall_row


def test_narrow_checkpoint_requires_specified_heading():
    # 0.5 m vertical gap; the 0.9 m long robot can only pass upright.
    gap = [15, 16, 17, 18, 19]
    grid, wall_row = _gate_map(gap)
    gate_y = (40 - 1 - wall_row) * 0.1
    args = (grid, [], 0.1, W, L, {"x": 0, "y": 0})
    kwargs = dict(
        start={"x": 1.7, "y": 1.2},
        end={"x": 1.7, "y": 3.2},
        start_orientation=math.pi / 2,
        end_orientation=math.pi / 2,
    )
    ok = validate_passage(
        *args, checkpoints=[{"x": 1.7, "y": gate_y, "orientation": math.pi / 2}], **kwargs
    )
    assert ok["passable"] is True
    wrong = validate_passage(
        *args, checkpoints=[{"x": 1.7, "y": gate_y, "orientation": 0.0}], **kwargs
    )
    assert wrong["passable"] is False
    assert wrong["status"] == "orientation-unreachable"


def test_gate_open_but_wrong_heading_at_end_is_distinguished():
    gap = [15, 16, 17, 18, 19]
    grid, wall_row = _gate_map(gap)
    gate_y = (40 - 1 - wall_row) * 0.1
    result = validate_passage(
        grid, [], 0.1, W, L, {"x": 0, "y": 0},
        start={"x": 1.7, "y": 1.2},
        end={"x": 1.7, "y": gate_y},
        start_orientation=math.pi / 2,
        end_orientation=0.0,
    )
    assert result["passable"] is False
    assert result["status"] == "orientation-unreachable"


def test_multiple_heading_checkpoints_in_sequence():
    grid = blank_grid(rows=50, cols=50)
    hwall(grid, 15, [c for c in range(1, 49) if c not in range(10, 15)])
    hwall(grid, 35, [c for c in range(1, 49) if c not in range(36, 41)])
    result = validate_passage(
        grid, [], 0.1, W, L, {"x": 0, "y": 0},
        start={"x": 1.2, "y": 3.1},
        end={"x": 3.8, "y": 0.8},
        start_orientation=math.pi / 2,
        end_orientation=math.pi / 2,
        checkpoints=[
            {"x": 1.2, "y": 2.4, "orientation": math.pi / 2},
            {"x": 3.8, "y": 1.4, "orientation": math.pi / 2},
        ],
    )
    assert result["passable"] is True


def test_position_only_checkpoint_allows_waiting_and_reorientation():
    gap = [15, 16, 17, 18, 19]
    grid, wall_row = _gate_map(gap)
    gate_y = (40 - 1 - wall_row) * 0.1
    result = validate_passage(
        grid, [], 0.1, W, L, {"x": 0, "y": 0},
        start={"x": 1.7, "y": 1.2},
        end={"x": 2.8, "y": 3.2},
        start_orientation=0.0,
        end_orientation=0.0,
        checkpoints=[{"x": 1.7, "y": gate_y, "orientation": None}],
    )
    assert result["passable"] is True


def test_sealed_wall_is_route_not_found():
    grid = blank_grid(rows=40, cols=40)
    hwall(grid, 20, range(1, 39))
    result = validate_passage(
        grid, [], 0.1, W, L, {"x": 0, "y": 0},
        {"x": 1.5, "y": 1.2},
        {"x": 1.5, "y": 3.2},
    )
    assert result["passable"] is False
    assert result["status"] == "route-not-found"


def test_gate_narrower_than_body_is_passage_blocked():
    gap = [17, 18, 19]  # 0.3 m gap, robot is 0.4 m wide even edge-on
    grid, wall_row = _gate_map(gap)
    gate_y = (40 - 1 - wall_row) * 0.1
    result = validate_passage(
        grid, [], 0.1, W, L, {"x": 0, "y": 0},
        {"x": 1.7, "y": 1.2},
        {"x": 1.7, "y": 3.2},
        checkpoints=[{"x": 1.8, "y": gate_y, "orientation": None}],
    )
    assert result["passable"] is False
    assert result["status"] == "passage-blocked"


def test_reverse_entry_into_dead_end_meets_opposite_heading():
    robot_w, robot_l = 0.3, 0.9
    grid = blank_grid(rows=40, cols=40)
    # Narrow 0.6 m corridor (x in [1.4, 2.0]) entered from a chamber below.
    vwall(grid, 14, range(1, 25))
    vwall(grid, 20, range(1, 25))
    result = validate_passage(
        grid, [], 0.1, robot_w, robot_l, {"x": 0, "y": 0},
        start={"x": 1.0, "y": 0.8},
        end={"x": 1.7, "y": 2.4},
        start_orientation=0.0,
        # Robot turns around in the chamber and reverses into the corridor,
        # ending pointing back out of the dead end (toward -y).
        end_orientation=3 * math.pi / 2,
    )
    assert result["passable"] is True
    assert abs(result["path"][-1]["orientation"] - 3 * math.pi / 2) < 0.2
