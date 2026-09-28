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
    result = passage(0.2, 0.3)
    assert result["passable"] is True
    assert len(result["path"]) > 1


def test_edge_hugging_obstacle_is_checked_beyond_center():
    # The 0.6 m edge protrusion combined with the next 0.5 m gate blocks a
    # robot whose center point can still travel through open center cells.
    result = passage(0.4, 0.4)
    assert result["passable"] is False
