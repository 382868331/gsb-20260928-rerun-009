import math

from app.geometry import validate_passage
from app.planner import plan_timed, retime_path


def open_grid(rows=20, cols=20):
    grid = [[False] * cols for _ in range(rows)]
    for col in range(cols):
        grid[0][col] = True
        grid[-1][col] = True
    for row in range(rows):
        grid[row][0] = True
        grid[row][-1] = True
    return grid


def test_same_position_different_orientation_turns_in_open_space():
    result = validate_passage(
        open_grid(),
        [],
        0.1,
        0.4,
        0.6,
        {"x": 0, "y": 0},
        {"x": 1.0, "y": 1.0},
        {"x": 1.0, "y": 1.0},
        start_orientation=0.0,
        end_orientation=math.pi / 2,
    )
    assert result["passable"] is True
    headings = [node["orientation"] for node in result["path"]]
    assert len(set(headings)) > 1  # the path really rotates
    assert abs(result["path"][-1]["orientation"] - math.pi / 2) < 0.2


def test_same_position_rotation_blocked_by_walls_is_reported():
    # A 0.3x0.9 robot inside a 0.4 x 1.0 m pocket fits lengthwise but cannot
    # sweep its body through a 90 degree turn.
    grid = open_grid()
    for col in range(9, 15):
        grid[7][col] = True
        grid[18][col] = True
    for row in range(8, 18):
        grid[row][9] = True
        grid[row][14] = True
    result = validate_passage(
        grid,
        [],
        0.1,
        0.3,
        0.9,
        {"x": 0, "y": 0},
        {"x": 1.15, "y": 0.7},
        {"x": 1.15, "y": 0.7},
        start_orientation=0.0,
        end_orientation=math.pi / 2,
    )
    assert result["passable"] is False
    assert "转向" in result["reason"]


def timed_state(grid, checkpoints, **overrides):
    params = {
        "base_grid": grid,
        "forbidden_zones": [],
        "no_wait_zones": [],
        "resolution": 0.1,
        "robot_width": 0.3,
        "robot_length": 0.4,
        "origin": {"x": 0, "y": 0},
        "start": {"x": 0.5, "y": 0.5},
        "start_orientation": 0.0,
        "checkpoints": checkpoints,
        "end": {"x": 1.5, "y": 1.5},
        "end_orientation": 0.0,
        "linear_speed": 0.5,
        "angular_speed": math.pi,
    }
    params.update(overrides)
    return params


def test_timed_checkpoint_within_window():
    result = plan_timed(
        **timed_state(
            open_grid(),
            [
                {
                    "name": "A",
                    "x": 1.0,
                    "y": 0.5,
                    "orientation": 0.0,
                    "earliest": 0.0,
                    "latest": 5.0,
                }
            ],
        )
    )
    assert result["status"] == "ok"
    assert result["schedule"][0]["arrival"] <= 5.0
    assert result["total_time"] > 0


def test_timed_checkpoint_window_missed():
    result = plan_timed(
        **timed_state(
            open_grid(),
            [
                {
                    "name": "A",
                    "x": 1.5,
                    "y": 1.5,
                    "orientation": 0.0,
                    "earliest": 0.0,
                    "latest": 0.05,
                }
            ],
        )
    )
    assert result["status"] == "window_missed"


def test_timed_checkpoint_orientation_unsatisfiable():
    grid = open_grid()
    # A vertical wall sits just east of the checkpoint: the 0.3x0.7 robot
    # fits along heading 0 but its 0.7 m length hits the wall at heading 90.
    for row in range(2, 18):
        grid[row][12] = True
    result = plan_timed(
        **timed_state(
            grid,
            [
                {
                    "name": "A",
                    "x": 1.0,
                    "y": 1.0,
                    "orientation": math.pi / 2,
                    "earliest": 0.0,
                    "latest": 50.0,
                }
            ],
            robot_width=0.3,
            robot_length=0.7,
        )
    )
    assert result["status"] == "orientation_unsatisfiable"


def test_timed_waits_outside_no_wait_corridor():
    grid = open_grid()
    params = timed_state(
        grid,
        [
            {
                "name": "A",
                "x": 1.0,
                "y": 0.5,
                "orientation": 0.0,
                "earliest": 10.0,
                "latest": 20.0,
            }
        ],
        no_wait_zones=[{"id": "nw", "x": 0.8, "y": 0.3, "width": 0.6, "height": 0.4}],
    )
    result = plan_timed(**params)
    assert result["status"] == "ok"
    entry = result["schedule"][0]
    assert entry["wait"] > 0
    wait_node = result["path"][entry["wait_path_index"]]
    assert wait_node["x"] < 0.8  # wait happens before the no-wait corridor


def test_retime_kept_geometry_and_adjusted_waiting():
    grid = open_grid()
    checkpoints = [
        {
            "name": "A",
            "x": 1.0,
            "y": 0.5,
            "orientation": 0.0,
            "earliest": 10.0,
            "latest": 20.0,
        }
    ]
    params = timed_state(grid, checkpoints)
    first = plan_timed(**params)
    assert first["status"] == "ok"
    entries = [
        {"name": e["name"], "path_index": e["path_index"]}
        for e in first["schedule"]
        if e["kind"] == "checkpoint"
    ]
    new_windows = [{**checkpoints[0], "earliest": 30.0, "latest": 31.0}]
    common = {
        "path": first["path"],
        "checkpoint_entries": entries,
        "checkpoints": new_windows,
        "no_wait_zones": [],
        "map_rows": 20,
        "map_cols": 20,
        "resolution": 0.1,
        "origin": {"x": 0, "y": 0},
        "linear_speed": 0.5,
        "angular_speed": math.pi,
    }
    kept = retime_path(**common, relocate_waits=False)
    assert kept["status"] == "ok"
    assert kept["schedule"][0]["wait"] > 20  # waits longer for the new window
    missed = retime_path(
        **{**common, "checkpoints": [{**checkpoints[0], "earliest": 0.0, "latest": 0.05}]},
        relocate_waits=False,
    )
    assert missed["status"] == "window_missed"
