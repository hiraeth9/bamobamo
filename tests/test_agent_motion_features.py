"""Kinematic candidate ranking and opt-in source edits, without model calls."""
import copy
import math
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import agent_motion_features as features


def feature(side='Left', *, heading=0., speed=1., time=0.):
    return {'motion_id': 'sample', 'source_time_s': time, 'heading_radians': heading,
            'root_velocity_m_s': [0., -speed, 0.], 'hip_height_m': 1.,
            'feet': {hand: {'relative_position_m': [sign*.15, -.2 if hand == side else .2, -.8],
                            'support_score': float(hand == side)}
                     for hand, sign in (('Left', 1), ('Right', -1))}}


def test_ranking_prefers_same_support_and_body_relative_pose():
    old = feature()
    matched = features.transition_cost(old, feature())
    opposite = features.transition_cost(old, feature('Right'))
    assert matched['score'] == 0
    assert opposite['foot_pose_gap_m'] > .3
    assert opposite['support_mismatch'] == 1
    assert opposite['score'] > matched['score']


def test_speed_and_hip_height_mismatches_remain_visible_in_feedback():
    old, new = feature(), feature(speed=3)
    new['hip_height_m'] = .8
    result = features.transition_cost(old, new)
    assert result['root_velocity_gap_m_s'] == 2
    assert result['hip_height_gap_m'] == pytest.approx(.2)
    assert result['score'] > 0


def compile_candidates(outgoing, incoming, **kwargs):
    armature = object()
    def measured(arm, name):
        assert arm is armature
        return {'motion_id': name, 'duration_s': 3., 'key_timebase_fps': 30.,
                'default_source_window': [1/30, 3.]}
    def sampled(arm, name, time, **options):
        assert arm is armature
        # The incoming first pose has the wrong support; an allowed later
        # entry matches it, without changing the actual incoming recovery.
        result = feature('Right' if name == 'b' and time < .6 else 'Left',
                         time=time, heading=math.pi/2 if name == 'a' else 0.)
        result['motion_id'] = name
        result['time_scale'] = options['time_scale']
        return result
    with patch('agent_character.resolve_actor', return_value={'armature': armature}), \
         patch('agent_motion.prepare_motion', side_effect=measured), \
         patch('agent_motion_catalog.motion_info', side_effect=lambda name: {'motion_id': name}), \
         patch.object(features, 'sample_features', side_effect=sampled):
        return features.transition_candidates('job', 'actor', outgoing, incoming, **kwargs)


def test_default_candidate_preserves_both_selected_windows_and_changes_no_timeline():
    old = {'motion_id': 'a', 'source_window': [.2, 2.]}
    new = {'motion_id': 'b', 'source_window': [.4, 2.8], 'time_scale': 1.2}
    original = copy.deepcopy([old, new])
    result = compile_candidates(old, new)
    assert result['candidate_count'] == 1
    choice = result['candidates'][0]
    assert choice['outgoing_source_window'] == old['source_window']
    assert choice['incoming_source_window'] == new['source_window']
    assert choice['heading_offset_radians'] == pytest.approx(math.pi/2)
    assert choice['incoming']['time_scale'] == 1.2
    assert not result['timeline_modified'] and not result['naturalness_verified']
    assert [old, new] == original


def test_permitted_entry_search_prefers_matching_foot_and_retains_recovery():
    result = compile_candidates({'motion_id': 'a', 'source_window': [.2, 2.]},
                                {'motion_id': 'b', 'source_window': [.4, 2.8]},
                                entry_search_seconds=.4, sample_step_seconds=.1)
    first = result['candidates'][0]
    assert .6 <= first['incoming_source_window'][0] <= .8
    assert first['incoming_source_window'][1] == 2.8
    assert first['outgoing_source_window'] == [.2, 2.]
    assert first['score'] == 0
    assert result['candidates'][-1]['score'] > first['score']


def test_phase_cost_compares_concurrent_overlap_instead_of_separated_seam_poses():
    result = compile_candidates({'motion_id': 'a', 'source_window': [.2, 2.]},
                                {'motion_id': 'b', 'source_window': [.4, 2.8]},
                                transition_seconds=.4)
    # Incoming entry has opposite support, then matches halfway through the
    # overlap. Feedback must reflect both states rather than entry alone.
    cost = result['candidates'][0]
    assert 0 < cost['support_mismatch'] < 1
    assert cost['scored_overlap_seconds'] == .4


def test_search_never_exhausts_a_clip_and_reports_all_pairs_count():
    result = compile_candidates({'motion_id': 'a', 'source_window': [1., 1.2]},
                                {'motion_id': 'b', 'source_window': [1., 1.2]},
                                exit_search_seconds=3, entry_search_seconds=3,
                                sample_step_seconds=.05, limit=100)
    assert result['candidate_count'] == len(result['candidates'])
    for item in result['candidates']:
        assert item['outgoing_source_window'][1]-item['outgoing_source_window'][0] >= 1/30-1e-8
        assert item['incoming_source_window'][1]-item['incoming_source_window'][0] >= 1/30-1e-8


@pytest.mark.parametrize('kwargs', [{'entry_search_seconds': -1}, {'sample_step_seconds': 0}, {'limit': 0}])
def test_obvious_bad_search_arguments_explain_how_to_change_them(kwargs):
    with pytest.raises(ValueError):
        compile_candidates({'motion_id': 'a'}, {'motion_id': 'b'}, **kwargs)
