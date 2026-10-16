"""Finger layer ownership and moving-source fades without Blender."""
import copy
import math
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import agent_hand_pose as hand
from agent_motion_timeline import blend_pose, event_weight


def closure(actor, side, degrees):
    rotation = [math.cos(math.radians(degrees)/2), math.sin(math.radians(degrees)/2), 0, 0]
    return {'rig:'+side+'Hand'+digit+str(joint): rotation
            for digit in ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky') for joint in (1, 2, 3)}


def compile_event(**kwargs):
    with patch.object(hand, '_runtime', return_value=({}, None, 7)), patch.object(hand, '_finger_closure', side_effect=closure):
        return hand.hand_pose_events('job', 'actor-other-23', 'close-hands',
                                     start_frame=10, end_frame=40, **kwargs)


def test_fist_is_only_thirty_finger_rotation_channels_and_no_root_or_ik():
    plan = compile_event()
    event = plan['events'][0]
    assert plan['actor_id'] == 'actor-other-23'
    assert len(event['mask']) == 30
    assert set(event['mask']) == set(event['keyframes'][0]['pose'])
    assert all(name.endswith(('1', '2', '3')) for name in event['mask'])
    assert all(set(channels) == {'rotation_quaternion'} for channels in event['keyframes'][0]['pose'].values())
    assert not {'root', 'root_path', 'targets', 'root_rotation_quaternion'} & set(event)
    assert event['root_strategy'] == 'hold'
    assert event['priority'] == 7


@pytest.mark.parametrize('hands,side', [('Left', 'Left'), ('RightHand', 'Right'), (['LeftHand', 'Left'], 'Left')])
def test_one_hand_selection_keeps_the_other_hand_and_body(hands, side):
    event = compile_event(hands=hands)['events'][0]
    pose = event['keyframes'][0]['pose']
    assert len(pose) == 15
    assert all(name.startswith('rig:'+side+'Hand') for name in pose)
    base = {'rig:'+name: {'rotation_quaternion': [1, 0, 0, 0], 'location': [1, 2, 3], 'scale': [1, 1, 1]}
            for name in ('Hips', 'Spine', 'LeftArm', 'RightArm', 'LeftHand', 'RightHand',
                         'LeftHandIndex1', 'RightHandIndex1')}
    original = copy.deepcopy(base)
    mixed = blend_pose(base, pose, 1, event['mask'])
    for name, channels in original.items():
        if name not in pose:
            assert mixed[name] == channels
        else:
            assert mixed[name]['location'] == channels['location']
            assert mixed[name]['scale'] == channels['scale']
    assert base == original


def test_close_release_blends_with_the_current_moving_source_not_initial_pose():
    event = compile_event()['events'][0]
    pose = event['keyframes'][0]['pose']
    name = next(iter(pose))
    samples = {}
    for frame in (10, 13, 16, 34, 37, 40):
        angle = .01*frame
        base = {name: {'rotation_quaternion': [math.cos(angle/2), 0, math.sin(angle/2), 0]}}
        samples[frame] = blend_pose(base, pose, event_weight(event, frame), event['mask'])[name]['rotation_quaternion']
        if frame in (10, 40):
            assert samples[frame] == pytest.approx(base[name]['rotation_quaternion'])
        if frame in (16, 34):
            assert samples[frame] == pytest.approx(pose[name]['rotation_quaternion'])
    assert samples[10] != samples[40]
    assert event_weight(event, 13) == pytest.approx(.5)
    assert event_weight(event, 37) == pytest.approx(.5)


def test_immediate_fist_and_explicit_open_hand_do_not_insert_fades():
    event = compile_event(blend_in_frames=0, blend_out_frames=0, finger_curl_deg=0)['events'][0]
    assert event_weight(event, 10) == event_weight(event, 40) == 1
    assert all(channels['rotation_quaternion'] == [1, 0, 0, 0]
               for channels in event['keyframes'][0]['pose'].values())


@pytest.mark.parametrize('kwargs', [
    {'hands': []}, {'hands': 'Elbow'}, {'finger_curl_deg': -1},
    {'finger_curl_deg': 100}, {'blend_in_frames': -1},
    {'blend_in_frames': 20, 'blend_out_frames': 20}, {'blend_in_frames': 1.5}])
def test_obvious_bad_hand_or_timing_arguments_explain_the_error(kwargs):
    with pytest.raises(ValueError):
        compile_event(**kwargs)
