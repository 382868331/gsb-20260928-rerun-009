from app.geometry import ALGORITHM_VERSION, validate_passage
from app.maps import make_sample_map, make_turn_sample_map


def passage(robot_width, robot_length, end_orientation=90.0):
    _, grid = make_sample_map()
    return validate_passage(
        grid,
        [],
        0.1,
        robot_width,
        robot_length,
        {"x": 0, "y": 0},
        {"x": 0.5, "y": 0.5},
        {"x": 2.8, "y": 0.5} if end_orientation is None else {"x": 0.5, "y": 1.8},
        0.0,
        0.0 if end_orientation is None else end_orientation,
    )


def turn_passage(open_corner, start_orientation, end_orientation, turn=False):
    _, grid = make_turn_sample_map(open_corner=open_corner)
    end = {"x": 1.8, "y": 0.6} if turn else {"x": 1.8, "y": 2.7}
    return validate_passage(
        grid,
        [],
        0.1,
        0.6,
        0.8,
        {"x": 0, "y": 0},
        {"x": 0.4, "y": 2.7},
        end,
        start_orientation,
        end_orientation,
    )


def test_whole_robot_fails_in_narrow_door_but_center_would_fit():
    result = passage(0.55, 0.8)
    assert result["passable"] is False
    assert "swept complete footprint" in result["reason"]
    assert result["algorithm"] == ALGORITHM_VERSION


def test_small_robot_drives_through_wide_door():
    result = passage(0.2, 0.3, None)
    assert result["passable"] is True
    assert len(result["path"]) > 1


def test_straight_and_reverse_passage_pass_but_continuous_turn_is_blocked():
    assert turn_passage(False, 0, 0)["passable"] is True
    assert turn_passage(False, 180, 180)["passable"] is True
    blocked = turn_passage(False, 0, 90, turn=True)
    assert blocked["passable"] is False
    reverse_blocked = turn_passage(False, 180, 270, turn=True)
    assert reverse_blocked["passable"] is False


def test_roomy_corner_allows_the_same_continuous_turn():
    result = turn_passage(True, 0, 90, turn=True)
    assert result["passable"] is True
    orientations = [point["orientation"] for point in result["path"]]
    assert max(orientations) - min(orientations) > 1.0


def test_edge_hugging_obstacle_is_checked_beyond_center():
    _, grid = make_sample_map()
    result = validate_passage(
        grid,
        [],
        0.1,
        0.4,
        0.4,
        {"x": 0, "y": 0},
        {"x": 0.5, "y": 0.5},
        {"x": 0.5, "y": 1.8},
        0.0,
        90.0,
    )
    assert result["passable"] is False
