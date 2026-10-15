"""Contact coordinates/contracts, interpolation and fail-closed QA without Blender."""
import json
import math
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import agent_contacts as contacts


def event(target, **updates):
    return {'event_id': 'unlisted_action', 'start_frame': 1, 'end_frame': 12,
            'targets': {'RightHand': target}, **updates}


class TargetValidationTests(unittest.TestCase):
    def test_old_world_target_and_open_intent_remain_valid(self):
        target = {'RightHand': {'position': [1, 2, 3], 'intent': '用指节敲未知机关两次'}}
        result = contacts.validate_targets(target)
        self.assertEqual(result['RightHand']['position'], [1, 2, 3])
        self.assertEqual(target['RightHand']['intent'], result['RightHand']['intent'])

    def test_binding_top_level_settings_survive_influence_keyframes(self):
        target = {'binding': {'actor_id': 'B', 'bone': 'Head', 'local_point': [0, 2, 0]},
                  'effector_point': [0, 7, 0], 'influence': .2,
                  'keyframes': [{'frame': 1}, {'frame': 11, 'influence': 1}]}
        normalized = contacts.validate_targets({'RightHand': target})['RightHand']
        middle = contacts.sample_target(normalized, 6)
        self.assertEqual(middle['binding'], normalized['binding'])
        self.assertEqual(middle['effector_point'], [0, 7, 0])
        self.assertAlmostEqual(middle['influence'], .6)

    def test_local_target_point_interpolates_in_same_binding(self):
        target = {'keyframes': [{'frame': 1, 'binding': {'object': 'Door', 'local_point': [0, 0, 0]}},
                                {'frame': 11, 'binding': {'object': 'Door', 'local_point': [2, 0, 0]}}]}
        target = contacts.validate_targets({'RightHand': target})['RightHand']
        self.assertEqual(contacts.sample_target(target, 6)['binding']['local_point'], [1, 0, 0])

    def test_sparse_world_keys_carry_top_level_and_previous_state(self):
        target = {'position': [1, 2, 3], 'pole': [0, 0, 1],
                  'keyframes': [{'frame': 1, 'influence': .4}, {'frame': 3, 'position': [3, 2, 3]},
                                {'frame': 5, 'influence': 0}]}
        normalized = contacts.validate_targets({'RightHand': target})['RightHand']
        state = contacts.sample_target(normalized, 5)
        self.assertEqual(state['position'], [3, 2, 3])
        self.assertEqual(state['pole'], [0, 0, 1])
        self.assertEqual(state['influence'], 0)

    def test_quaternion_sign_does_not_spin_or_collapse(self):
        target = {'position': [0, 0, 0], 'keyframes': [
            {'frame': 1, 'rotation_quaternion': [1, 0, 0, 0]},
            {'frame': 11, 'rotation_quaternion': [-1, 0, 0, 0]}]}
        result = contacts.sample_target(contacts.validate_targets({'RightHand': target})['RightHand'], 6)
        self.assertEqual(result['rotation_quaternion'], [1, 0, 0, 0])

    def test_explicit_contact_window_can_be_single_impact_frame(self):
        result = contacts.validate_targets({'RightHand': {'binding': {'object': 'Box'},
                    'contact': {'start_frame': 6, 'end_frame': 6, 'kind': '任意新接触语义'}}}, 1, 12)
        self.assertEqual(result['RightHand']['contact']['tolerance_m'], .03)
        self.assertEqual(result['RightHand']['binding']['local_point'], [0, 0, 0])

    def test_malformed_bindings_and_indices_rejected(self):
        cases = ({}, {'object': 'Box', 'actor_id': 'B', 'bone': 'Head'},
                 {'actor_id': 'B'}, {'actor_id': 'B', 'bone': 'Head', 'vertex_index': 2},
                 {'object': 'Box', 'vertex_index': -1}, {'object': 'Box', 'vertex_index': True},
                 {'object': 'Box', 'vertex_index': 1.5},
                 {'object': 'Box', 'vertex_index': 1, 'local_point': [0, 0, 0]})
        for binding in cases:
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                contacts.validate_binding(binding)

    def test_invalid_windows_and_coordinates_do_not_enter_scene(self):
        cases = ({'position': [0, 0]}, {'position': [0, math.inf, 1]},
                 {'position': [0, 0, 0], 'influence': 1.1},
                 {'position': [0, 0, 0], 'rotation_quaternion': [0, 0, 0, 0]},
                 {'position': [0, 0, 0], 'keyframes': []},
                 {'binding': {'object': 'Box'}, 'contact': {'start_frame': 8, 'end_frame': 7}},
                 {'binding': {'object': 'Box'}, 'contact': {'start_frame': 0, 'end_frame': 2}},
                 {'binding': {'object': 'Box'}, 'contact': {'start_frame': 1, 'end_frame': 2, 'tolerance_m': 0}})
        for target in cases:
            with self.subTest(target=target), self.assertRaises(ValueError):
                contacts.validate_targets({'RightHand': target}, 1, 12)

    def test_missing_position_in_sparse_keys_is_not_silent_zero(self):
        with self.assertRaisesRegex(ValueError, 'position or binding'):
            contacts.validate_targets({'RightHand': {'keyframes': [{'frame': 1, 'influence': 1}]}})

    def test_target_and_effector_ambiguity_is_explicit(self):
        for target in ({'binding': {'object': 'Box'}, 'position': [0, 0, 0]},
                       {'position': [0, 0, 0], 'effector': {'object': 'Skin'}},
                       {'position': [0, 0, 0], 'effector': {'object': 'Skin', 'vertex_index': 0},
                        'effector_point': [0, 0, 0]}):
            with self.assertRaises(ValueError):
                contacts.validate_targets({'RightHand': target})


class RequirementTests(unittest.TestCase):
    def test_binding_defaults_to_whole_event_and_semantics_are_open(self):
        entries = contacts._requirements_for_events('A', [event({'binding': {'object': 'UnknownProp'},
            'intent': '一个从未登记的新动作'})])
        self.assertEqual((entries[0]['frame_start'], entries[0]['frame_end']), (1, 12))
        self.assertEqual(entries[0]['target']['intent'], '一个从未登记的新动作')

    def test_absolute_legacy_target_does_not_invent_contact_requirement(self):
        self.assertEqual(contacts._requirements_for_events('A', [event({'position': [1, 2, 3]})]), [])

    def test_following_target_can_explicitly_have_no_touch_requirement(self):
        target = {'binding': {'actor_id': 'A', 'bone': 'Head', 'local_point': [0, 12, 8]}, 'contact': False}
        self.assertIs(contacts.validate_targets({'RightHand': target})['RightHand']['contact'], False)
        self.assertEqual(contacts._requirements_for_events('A', [event(target)]), [])

    def test_absolute_target_can_explicitly_require_contact(self):
        result = contacts._requirements_for_events('A', [event({'position': [1, 2, 3],
                         'contact': {'start_frame': 6, 'end_frame': 6, 'id': 'hit', 'kind': 'strike'}})])
        self.assertEqual(result[0]['id'], 'hit')
        self.assertEqual(result[0]['frame_start'], 6)

    def test_corrupt_target_collection_is_not_empty_success(self):
        with self.assertRaises(ValueError):
            contacts._requirements_for_events('A', [event([])])

    def test_corrupt_weight_and_empty_contact_id_are_rejected(self):
        for changes in ({'weight': 2}, {'blend_in_frames': -1}):
            with self.assertRaises(ValueError):
                contacts._requirements_for_events('A', [event({'binding': {'object': 'Box'}}, **changes)])
        with self.assertRaises(ValueError):
            contacts.validate_targets({'RightHand': {'binding': {'object': 'Box'}, 'contact': {'id': ''}}}, 1, 12)

    def test_empty_actor_registry_is_legal_and_corrupt_timeline_is_not(self):
        fake = types.ModuleType('agent_character')
        fake.actor_list = lambda job: {'actors': []}
        fake.resolve_actor = lambda job, aid: {'root': {contacts.TIMELINE_STORE: '{"events":false}'}}
        with patch.dict(sys.modules, {'agent_character': fake}):
            self.assertEqual(contacts.contact_requirements('job')['status'], 'empty')
            fake.actor_list = lambda job: {'actors': [{'actor_id': 'A'}]}
            with self.assertRaises(ValueError):
                contacts.contact_requirements('job')


class ContactCheckerTests(unittest.TestCase):
    def setUp(self):
        self.scene = types.SimpleNamespace(frame_start=1, frame_end=12, frame_current=4, frame_subframe=.25)
        def set_frame(frame, subframe=0):
            self.scene.frame_current = frame; self.scene.frame_subframe = subframe
        self.scene.frame_set = set_frame
        fake_bpy = types.ModuleType('bpy')
        fake_bpy.context = types.SimpleNamespace(scene=self.scene, view_layer=types.SimpleNamespace(update=lambda: None))
        fake_character = types.ModuleType('agent_character')
        fake_character.resolve_actor = lambda job, aid: {'armature': None}
        self.modules = patch.dict(sys.modules, {'bpy': fake_bpy, 'agent_character': fake_character})
        self.modules.start(); self.addCleanup(self.modules.stop)
        target = {'binding': {'object': 'MovingTarget'}, 'contact': {'start_frame': 6, 'end_frame': 8}}
        self.registration = {'version': 1, 'status': 'registered', 'requirements': {'version': 1,
            'contacts': contacts._requirements_for_events('A', [event(target)])}}
        self.patches = [patch.object(contacts, 'contact_requirements', return_value=self.registration),
            patch.object(contacts, 'resolve_binding', side_effect=lambda job, binding, actor_cache=None: {
                'position': [self.scene.frame_current*.01, 0, 0], 'scope': 'evaluated_object_local_point'}),
            patch.object(contacts, 'resolve_effector', side_effect=lambda *args: {
                'position': [self.scene.frame_current*.01, 0, 0], 'scope': 'evaluated_actor_skin_vertex'})]
        for context in self.patches:
            context.start(); self.addCleanup(context.stop)

    def test_all_frames_in_registered_window_checked_and_cursor_restored(self):
        result = contacts.check_contacts('job', detail=True)
        self.assertTrue(result['passed']); self.assertEqual(result['status'], 'passed')
        self.assertEqual([row['frame'] for row in result['frames']], [6, 7, 8])
        self.assertEqual(result['contacts'][0]['effector_scope'], 'evaluated_actor_skin_vertex')
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (4, .25))

    def test_default_report_does_not_echo_dense_target_keyframes(self):
        result = contacts.check_contacts('job')
        self.assertEqual(result['requirement_count'], 1)
        self.assertNotIn('requirements', result)
        self.assertNotIn('frames', result)
        self.assertEqual(len(result['contacts']), 1)

    def test_target_edit_after_bake_fails_geometric_check(self):
        with patch.object(contacts, 'resolve_effector', return_value={'position': [1, 0, 0], 'scope': 'evaluated_bone_origin'}):
            result = contacts.check_contacts('job')
        self.assertFalse(result['passed']); self.assertEqual(result['failure_count'], 3)

    def test_missing_evaluated_target_fails_closed_with_frame_context(self):
        with patch.object(contacts, 'resolve_binding', side_effect=ValueError('target removed')):
            result = contacts.check_contacts('job')
        self.assertFalse(result['ok']); self.assertEqual(result['status'], 'error')
        self.assertEqual(result['failures'][-1]['frame'], 6)
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (4, .25))

    def test_partial_check_reports_actual_coverage(self):
        result = contacts.check_contacts('job', frame_start=7, frame_end=7)
        self.assertEqual(result['contacts'][0]['tested_frames'], 1)
        self.assertEqual(result['contacts'][0]['coverage'], 'partial')

    def test_nonoverlapping_range_cannot_claim_contact_pass(self):
        result = contacts.check_contacts('job', frame_start=1, frame_end=2)
        self.assertFalse(result['passed']); self.assertEqual(result['failure_count'], 1)

    def test_contract_outside_scene_and_bad_query_ranges_fail(self):
        self.registration['requirements']['contacts'][0]['frame_end'] = 13
        self.assertEqual(contacts.check_contacts('job')['status'], 'error')
        self.assertEqual(contacts.check_contacts('job', frame_start=8, frame_end=7)['status'], 'error')


if __name__ == '__main__':
    unittest.main()
