"""Direct library events exclude measured calibration frames by default."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import agent_motion
from agent_motion_timeline import _source_sample


def sample_event(metadata, window=None):
    event = {'source': 'library', 'motion_id': 'cmu.02_01', 'event_id': 'walk',
             'start_frame': 1, 'end_frame': 86, 'time_scale': 1, 'fit': False}
    if window is not None:
        event['source_window'] = window
    times = []
    def source(armature, identifier, time, **options):
        times.append((time, options.get('source_window')))
        return {'pose': {}, 'root': [time, 0, 0]}
    with patch.object(agent_motion, 'prepare_motion', return_value={'duration_s': 85/30, **metadata}), patch.object(agent_motion, 'sample_motion', side_effect=source):
        result = _source_sample(object(), event, 1, 30)
    return result, times


def test_first_frame_uses_real_motion_and_recenters_the_cropped_root():
    result, times = sample_event({'default_source_window': [1/30, 85/30]})
    assert all(time == 1/30 for time, window in times)
    assert all(window == [1/30, 85/30] for time, window in times)
    assert result['root'] == [0, 0, 0]


def test_explicit_window_can_still_sample_the_calibration_pose():
    _, times = sample_event({'default_source_window': [1/30, 85/30]}, [0, 1])
    assert all(time == 0 and window == [0, 1] for time, window in times)


def test_unmeasured_source_keeps_whole_clip_instead_of_guessing_a_crop():
    _, times = sample_event({})
    assert all(time == 0 and window is None for time, window in times)
