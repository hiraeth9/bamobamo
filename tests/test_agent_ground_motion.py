"""Timing and measured support selection for optional ground-motion priors."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from agent_ground_motion import source_times, support_windows


def test_source_window_keeps_endpoint_seconds_and_continuous_timing():
    times = source_times(7, 13, [10.5, 12.])
    assert times[0] == (7, 10.5)
    assert times[-1] == (13, 12.)
    assert times[3] == (10, 11.25)


def test_source_window_does_not_wrap_or_accept_empty_range():
    for args in ((1, 1, [0, 1]), (1, 10, [3, 2]), (1, 10, [-1, 2])):
        with pytest.raises(ValueError):
            source_times(*args)


def points_at(frames, value):
    return {frame: {chain: value(frame, chain) for chain in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot')}
            for frame in frames}


def test_support_is_actual_low_and_slow_surface_not_a_file_label():
    points = points_at(range(1, 11), lambda f, c: [f*.001, 0, 0] if c == 'LeftHand' else [0, 0, .3])
    assert support_windows(points, 30) == [{'chain': 'LeftHand', 'start_frame': 1, 'end_frame': 10}]


def test_fast_floor_slide_is_not_planted_support():
    points = points_at(range(1, 11), lambda f, c: [f*.08, 0, 0])
    assert support_windows(points, 30) == []


def test_support_height_uses_the_requested_floor_and_rejects_short_noise():
    points = points_at(range(1, 11), lambda f, c: [0, 0, .5 if 4 <= f <= 5 else .9])
    assert support_windows(points, 30, floor_z=.5) == []
    points = points_at(range(1, 11), lambda f, c: [0, 0, .5])
    assert len(support_windows(points, 30, floor_z=.5)) == 4
