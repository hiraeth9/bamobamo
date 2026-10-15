"""Default contact feedback and unfinished coordinated motion checks."""
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import agent_motion_quality


def failed_contact():
    return {'ok': True, 'passed': False, 'version': 1, 'status': 'failed',
            'failure_count': 1, 'contacts': [],
            'failures': [{'check': 'contact_distance', 'frame': 42,
                          'actor_id': 'actor_left', 'event_id': 'reach',
                          'chain': 'RightHand', 'target_object': 'moving_chest',
                          'distance_m': .24, 'tolerance_m': .03}]}


class RegisteredContactFeedbackTests(unittest.TestCase):
    def test_runtime_error_returns_a_readable_contact_failure(self):
        contacts = SimpleNamespace(check_contacts=Mock(side_effect=ValueError('Contact target missing: moving_chest')))
        with patch.dict(sys.modules, {'agent_contacts': contacts}):
            result = agent_motion_quality.registered_contact_report('job')
        self.assertFalse(result['passed'])
        self.assertIn('moving_chest', result['failures'][0]['message'])


class MotionCheckContactIntegrationTests(unittest.TestCase):
    def test_internal_base_pass_cannot_publish_even_with_matching_action_evidence(self):
        from agent_motion_timeline import STORE
        commit = {'status': 'internal_base_only', 'armature_action': 'pose', 'root_action': 'root',
                  'pose_channel_sha256': 'pose_hash', 'root_channel_sha256': 'root_hash'}
        actor = {'root': {STORE: json.dumps({'version': 1, 'events': [], 'last_commit': commit})},
                 'armature': object()}
        characters = SimpleNamespace(scene_actor_facts=lambda job: {'actors': [{'actor_id': 'actor_left'}]},
                                     resolve_actor=lambda job, actor_id: actor)
        pose = {'action': 'pose', 'channel_values_sha256': 'pose_hash',
                'curve_count': 65, 'pose_curve_count': 65}
        root = {'action': 'root', 'channel_values_sha256': 'root_hash',
                'curve_count': 10, 'pose_curve_count': 0}
        with patch.dict(sys.modules, {'agent_character': characters}), \
                patch.object(agent_motion_quality, '_action_facts', side_effect=[pose, root]):
            report = agent_motion_quality.scene_motion_facts('job')
        self.assertEqual(len(report['errors']), 1)
        self.assertIn('internal base pass', report['errors'][0]['error'])
        commit['status'] = 'baked_unreviewed'
        actor['root'][STORE] = json.dumps({'version': 1, 'events': [], 'last_commit': commit})
        with patch.dict(sys.modules, {'agent_character': characters}), \
                patch.object(agent_motion_quality, '_action_facts', side_effect=[pose, root]):
            self.assertFalse(agent_motion_quality.scene_motion_facts('job')['errors'])

    def test_default_check_merges_registered_failures_and_retains_explicit_interface(self):
        class Vector(tuple):
            def __new__(cls, values): return tuple.__new__(cls, values)
            def __sub__(self, other): return Vector(a - b for a, b in zip(self, other))
            @property
            def length(self): return math.sqrt(sum(v * v for v in self))

        class Identity:
            def __matmul__(self, value): return value

        class Scene(SimpleNamespace):
            def frame_set(self, frame): self.frame_current = frame

        scene = Scene(frame_start=1, frame_end=60, frame_current=9)
        arm = SimpleNamespace(matrix_world=Identity(), pose=SimpleNamespace(
            bones={'RightHand': SimpleNamespace(head=Vector((0, 0, 0)))}))
        arm.evaluated_get = lambda graph: arm
        checker = Mock(return_value=failed_contact())
        modules = {
            'bpy': SimpleNamespace(context=SimpleNamespace(
                scene=scene, evaluated_depsgraph_get=lambda: None)),
            'mathutils': SimpleNamespace(Vector=Vector),
            'mathutils.bvhtree': SimpleNamespace(BVHTree=object),
            'bpy_extras': SimpleNamespace(),
            'bpy_extras.object_utils': SimpleNamespace(world_to_camera_view=None),
            'agent_character': SimpleNamespace(
                actor_info=None, scene_actor_facts=lambda job: {'actors': [], 'errors': []},
                resolve_actor=lambda job, actor: {'armature': arm}),
            'agent_contacts': SimpleNamespace(check_contacts=checker),
        }
        with patch.dict(sys.modules, modules):
            report = agent_motion_quality.motion_check('job', contacts=[{
                'actor_id': 'actor_left', 'bone': 'RightHand', 'frame': 42,
                'point': [0, 0, 0], 'tolerance_m': .03}])
        self.assertFalse(report['passed'])
        self.assertTrue(report['contacts'][0]['passed'])
        self.assertEqual(report['failures'][0]['check'], 'registered_contacts')
        self.assertEqual(report['failures'][0]['failures'][0]['frame'], 42)
        checker.assert_called_once_with('job', frame_start=1, frame_end=60, actor_id=None, detail=False)
        self.assertEqual(scene.frame_current, 9)


if __name__ == '__main__':
    unittest.main()
