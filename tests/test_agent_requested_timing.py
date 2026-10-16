"""Preserve explicit requested length when original-speed clips end early."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from agent_scene import validate_requested_timing


def test_six_seconds_does_not_accept_the_166_frame_boxing_sequence():
    with pytest.raises(ValueError, match='166 帧'):
        validate_requested_timing({'timing': {'requested_duration_seconds': 6}},
                                  {'duration_seconds': 166/30, 'fps': 30, 'frame_count': 166})


@pytest.mark.parametrize('frames', [179, 180, 181])
def test_explicit_duration_allows_one_frame_of_rounding(frames):
    validate_requested_timing({'timing': {'requested_duration_seconds': 6}},
                              {'duration_seconds': frames/30, 'fps': 30, 'frame_count': frames})


def test_unspecified_duration_does_not_invent_a_timing_requirement():
    validate_requested_timing({'requirements': ['一个人连续出拳']},
                              {'duration_seconds': 166/30, 'fps': 30, 'frame_count': 166})
