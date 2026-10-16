"""Source timing and sequence scheduling without Blender, rendering or assets."""
import copy
from pathlib import Path
import sys
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import agent_motion_sequence as sequence


def compile_sequence(segments, *, fps=30., durations=None, definitions=None, events=None, **kwargs):
    durations = durations or {'a': 2., 'b': 2., 'c': 2.}
    definitions = definitions or {}
    context = {'armature': object(), 'fps': fps, 'frame_end': 1000, 'events': events or []}
    def info(name):
        return {'motion_id': name, **definitions.get(name, {})}
    def prepared(armature, name):
        assert armature is context['armature']
        return {'motion_id': name, 'duration_s': durations[name], 'source_fps': 30.,
                'source_frame_range': [1., 1.+durations[name]*30.]}
    with patch.object(sequence, '_context', return_value=context), patch.object(sequence, 'motion_info', side_effect=info), patch.object(sequence, 'prepare_motion', side_effect=prepared):
        return sequence.library_sequence_events('job', 'Actor', 'combo', segments, **kwargs)


def test_measured_duration_at_original_speed_does_not_fit_catalog_claim():
    plan = compile_sequence([{'motion_id': 'a'}], definitions={'a': {'duration_s': 100}})
    event = plan['events'][0]
    assert event['start_frame'] == 1 and event['end_frame'] == 61
    assert event['time_scale'] == 1 and event['fit'] is False and event['loop'] is False
    assert plan['sequence']['duration_s'] == 2


def test_explicit_speed_and_crop_have_physical_scene_duration():
    plan = compile_sequence([{'motion_id': 'a', 'source_window': [.5, 2.], 'time_scale': 2}])
    event = plan['events'][0]
    assert event['source_window'] == [.5, 2.]
    assert event['time_scale'] == 2
    assert event['end_frame']-event['start_frame'] == 23  # includes final source pose; never fit stretched
    assert plan['sequence']['segments'][0]['playback_duration_s'] == .75


def test_facing_alignment_is_opt_in_and_individual_segments_can_preserve_source_facing():
    ordinary = compile_sequence([{'motion_id': 'a'}])
    assert 'align_heading' not in ordinary['events'][0]
    plan = compile_sequence([{'motion_id': 'a'}, {'motion_id': 'b', 'align_heading': False}], align_heading=True)
    assert plan['events'][0]['align_heading'] is True
    assert 'align_heading' not in plan['events'][1]
    assert plan['sequence']['segments'][1]['align_heading'] is False
    assert all(event['time_scale'] == 1 and not event['fit'] for event in plan['events'])


def test_old_short_annotation_does_not_cut_recovery_and_is_only_an_edit_suggestion():
    plan = compile_sequence([{'motion_id': 'a'}], definitions={'a': {'source_window_frames': [16, 42], 'source_frame_range': [1, 61], 'source_fps': 30}})
    event = plan['events'][0]
    assert event['source_window'] == [0, 2.]
    assert event['end_frame']-event['start_frame'] == 60
    assert plan['sequence']['segments'][0]['suggested_source_window_seconds'] == [.5, 41/30]
    assert any('not applied automatically' in note for note in plan['sequence']['notes'])


def test_explicit_seconds_override_the_curated_annotation():
    plan = compile_sequence([{'motion_id': 'a', 'source_window': [1, 2]}], definitions={'a': {'source_window_frames': [16, 42]}})
    assert plan['events'][0]['source_window'] == [1, 2]
    assert plan['sequence']['segments'][0]['source_window_origin'] == 'explicit_seconds'


def test_verified_calibration_prefix_is_removed_without_shortening_recovery():
    definition = {'requires_source_window': True, 'valid_motion_window_seconds': [.1, 2.]}
    plan = compile_sequence([{'motion_id': 'a'}], definitions={'a': definition})
    assert plan['events'][0]['source_window'] == [.1, 2.]
    assert plan['events'][0]['end_frame'] == 58
    assert plan['sequence']['segments'][0]['source_window_origin'] == 'verified_motion_window_excluding_calibration'


def test_explicit_window_can_override_verified_calibration_exclusion():
    definition = {'requires_source_window': True, 'valid_motion_window_seconds': [.1, 2.]}
    plan = compile_sequence([{'motion_id': 'a', 'source_window': [0., .5]}], definitions={'a': definition})
    assert plan['events'][0]['source_window'] == [0., .5]
    assert plan['sequence']['segments'][0]['source_window_origin'] == 'explicit_seconds'


def test_pairwise_overlap_keeps_both_sources_moving_without_frozen_pose_keys():
    plan = compile_sequence([{'motion_id': name} for name in ('a', 'b', 'c')], transition_frames=6)
    first, second, third = plan['events']
    assert [(e['start_frame'], e['end_frame']) for e in plan['events']] == [(1, 61), (55, 115), (109, 169)]
    assert second['blend_in_frames'] == third['blend_in_frames'] == 6
    assert first['blend_in_frames'] == 0
    assert all(e['source'] == 'library' and 'keyframes' not in e and 'blend_out_frames' not in e for e in plan['events'])
    assert all(e['weight'] == 1 for e in plan['events'])
    assert plan['sequence']['neutral_pose_fillers_added'] is False
    assert third['start_frame'] > first['end_frame']


def test_short_internal_clip_reduces_neighbors_instead_of_three_way_overlap():
    plan = compile_sequence([{'motion_id': name} for name in ('a', 'b', 'c')], durations={'a': 2, 'b': 10/30, 'c': 2}, transition_frames=6)
    events = plan['events']
    assert events[1]['blend_in_frames']+events[2]['blend_in_frames'] == 9
    for frame in range(plan['sequence']['frame_start'], plan['sequence']['frame_end']+1):
        assert sum(e['start_frame'] <= frame <= e['end_frame'] for e in events) <= 2
    assert any('shortened' in note for note in plan['sequence']['notes'])


def test_impossibly_short_internal_clip_gives_an_editable_error():
    with pytest.raises(ValueError, match='at least three scene-frame intervals'):
        compile_sequence([{'motion_id': name} for name in ('a', 'b', 'c')], durations={'a': 2, 'b': 2/30, 'c': 2})


def test_full_clip_returns_review_note_and_does_not_add_neutral_motion():
    plan = compile_sequence([{'motion_id': 'a'}])
    assert len(plan['events']) == 1
    assert any('calibration/neutral' in note for note in plan['sequence']['notes'])
    assert plan['status'] == 'events_compiled_not_registered_or_baked'
    assert plan['naturalness_verified'] is False


def test_plan_ids_and_higher_priority_allow_normal_replacement_without_mutating_input():
    segments = [{'motion_id': 'a'}, {'motion_id': 'b', 'intent': '自然语言选中的下一个动作'}]
    before = copy.deepcopy(segments)
    old = [{'event_id': 'unrelated', 'priority': 7}]
    first = compile_sequence(segments, events=old)
    second = compile_sequence(segments, events=old)
    assert [e['event_id'] for e in first['events']] == [e['event_id'] for e in second['events']]
    assert all(e['priority'] == 8 for e in first['events'])
    assert segments == before
    assert old == [{'event_id': 'unrelated', 'priority': 7}]


def test_scene_rate_changes_frame_count_without_changing_source_speed():
    plan = compile_sequence([{'motion_id': 'a'}], fps=24., start_frame=10)
    assert plan['events'][0]['end_frame'] == 58
    assert plan['events'][0]['time_scale'] == 1
    assert plan['sequence']['duration_s'] == 2


def test_obvious_timing_errors_explain_the_parameter_to_change():
    for segment, text in (({'motion_id': 'a', 'time_scale': 0}, 'time_scale'), ({'motion_id': 'a', 'source_window': [1, 3]}, 'source_window')):
        with pytest.raises(ValueError, match=text):
            compile_sequence([segment])
