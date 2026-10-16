"""Contact envelopes and composability, without requiring a Blender process."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import agent_contact_interactions as contact
from agent_contacts import sample_target
from agent_interactions import verify_interaction


def test_contact_obligation_is_full_weight_and_release_returns_to_fk():
    envelope = lambda frame: contact.contact_envelope(frame, 5, 15, 28, 40)
    assert envelope(5) == envelope(40) == 0
    assert all(envelope(frame) == 1 for frame in range(15, 29))
    assert all(envelope(frame) < envelope(frame+1) for frame in range(5, 15))
    assert all(envelope(frame) > envelope(frame+1) for frame in range(28, 40))
    assert envelope(5.001) < 1e-7
    assert envelope(39.999) < 1e-7


@pytest.mark.parametrize('timing', [(1, 1, 5, 8), (1, 5, 4, 8), (1, 5, 8, 8)])
def test_bad_timing_explains_required_sequence(timing):
    with pytest.raises(ValueError, match='start < contact <= release < end'):
        contact.contact_envelope(1, *timing)


@pytest.fixture
def compile_plan():
    poses = {frame: {'pose': {'right_actual': {'rotation_quaternion': [1, 0, 0, 0], 'location': [frame/100, 0, 0]},
                             'unrelated_leg': {'location': [0, 0, frame]}},
                     'bone_world': {'right_actual': SimpleNamespace(to_quaternion=lambda: [1, 0, 0, 0])}}
             for frame in range(1, 41)}
    with patch.object(contact, '_runtime', return_value=({'root': object(), 'armature': object()}, object(), 3)), \
         patch.object(contact, '_snapshot', return_value=poses), \
         patch.object(contact, '_bone', return_value=(None, SimpleNamespace(name='right_actual'))), \
         patch.object(contact, '_surface_effector', return_value=({'object': 'own_skin', 'vertex_index': 7}, [0, 0, 1])), \
         patch.object(contact, '_hand_elbow_target', return_value={'pole': [0, 0, 1]}), \
         patch('agent_contacts.resolve_binding'), patch('agent_contacts.resolve_effector'), \
         patch('agent_motion_timeline.timeline_info', return_value={'events': []}), \
         patch('agent_motion_timeline._mask', return_value=['right_actual']):
        yield contact.contact_events


def test_actor_ids_semantics_and_target_coordinates_remain_open(compile_plan):
    binding = {'actor_id': 'performer-other.27', 'bone': 'LeftShoulder', 'local_point': [3, -2, 6]}
    original = copy.deepcopy(binding)
    plan = compile_plan('job', 'helper-nine', 'steady_person', binding, start_frame=5,
                        contact_frame=14, release_frame=22, end_frame=35, kind='steady shoulder')
    event = plan['events'][0]
    target = event['targets']['RightHand']
    assert target['binding'] == binding == original
    assert plan['actor_id'] == 'helper-nine'
    assert target['contact']['kind'] == 'steady shoulder'
    assert all(sample_target(target, frame)['influence'] == 1 for frame in range(14, 23))
    assert sample_target(target, 35)['influence'] == 0
    assert plan['expectations']['impact_contact_ids'] == []
    assert not plan['hit_confirmed']


def test_multiple_grips_do_not_author_object_or_actor_root_motion(compile_plan):
    plans = [compile_plan('job', actor, 'cooperate', {'object': 'table', 'local_point': point},
                         start_frame=1, contact_frame=8, release_frame=20, end_frame=30,
                         chain=chain, kind='carry')
             for actor, chain, point in [('porterL', 'RightHand', [-.3, 0, 0]),
                                          ('porterR', 'LeftHand', [.3, 0, 0])]]
    for plan in plans:
        assert not plan['expectations']['root_translation_generated']
        assert not plan['expectations']['base_pose_replaced']
        for event in plan['events']:
            assert event['root_strategy'] == 'hold'
            assert 'root_path' not in event
            assert all(set(key['pose']) == {'right_actual'} and 'root' not in key for key in event['keyframes'])
            assert all('arm' in name for name in event['mask'])
            assert event['keyframes'][0]['pose']['right_actual']['location'] != event['keyframes'][-1]['pose']['right_actual']['location']
    assert plans[0]['expectations']['contact_ids'] != plans[1]['expectations']['contact_ids']


def test_skin_vertex_binding_and_single_frame_contact_are_supported(compile_plan):
    plan = compile_plan('job', 'actor', 'touch', {'object': 'other_skin', 'vertex_index': 123},
                        start_frame=2, contact_frame=8, release_frame=8, end_frame=16)
    target = plan['events'][0]['targets']['RightHand']
    assert target['binding'] == {'object': 'other_skin', 'vertex_index': 123}
    assert all(sample_target(target, frame)['influence'] < 1 for frame in (2, 7, 9, 16))
    assert plan['expectations']['contact_requirements'][0]['frame_start'] == 8
    assert plan['expectations']['contact_requirements'][0]['frame_end'] == 8


def test_generic_contact_verifies_without_claiming_an_attack(compile_plan):
    plan = compile_plan('job', 'A', 'help', {'object': 'rail', 'local_point': [0, 0, 0]},
                        start_frame=1, contact_frame=8, release_frame=20, end_frame=30, kind='hold')
    cid = plan['expectations']['contact_ids'][0]
    measured = {'ok': True, 'passed': True, 'contacts': [{'id': cid, 'kind': 'hold', 'passed': True,
                 'coverage': 'full', 'tested_frames': 13, 'expected_frames': 13}]}
    with patch('agent_contacts.check_contacts', return_value=measured):
        verified = verify_interaction('job', plan)
        assert verified['passed']
        assert not verified['hit_confirmed']
        measured['contacts'][0]['passed'] = False
        assert not verify_interaction('job', plan)['passed']


def test_foot_overlay_has_only_its_existing_leg_chain(compile_plan):
    plan = compile_plan('job', 'actor', 'brace', {'object': 'step', 'local_point': [0, 0, 0]},
                        start_frame=1, contact_frame=8, release_frame=20, end_frame=30,
                        chain='LeftFoot', kind='brace')
    assert plan['events'][0]['mask'] == ['LeftUpLeg', 'LeftLeg', 'LeftFoot', 'LeftToeBase']
    assert not plan['expectations']['root_translation_generated']


def test_existing_foot_support_must_be_released_before_another_goal(compile_plan):
    stance = {'id': 'old:stance', 'chain': 'LeftFoot', 'frame_start': 1, 'frame_end': 30,
              'event_id': 'walk:planted'}
    with patch('agent_contacts._requirements_for_events', return_value=[stance]):
        with pytest.raises(ValueError, match='release this foot existing contact windows'):
            compile_plan('job', 'actor', 'brace', {'object': 'step', 'local_point': [0, 0, 0]},
                         start_frame=1, contact_frame=8, release_frame=20, end_frame=30,
                         chain='LeftFoot', kind='brace')
