"""Kinematic phase scheduling and measured-hit gating, with no render/GUI."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import agent_interactions as interaction


class MotionCurveTests(unittest.TestCase):
    def test_execution_outcomes_keep_hit_default_and_miss_units_explicit(self):
        self.assertEqual(interaction._outcome_options('hit', None, .08), (None, .08))
        self.assertEqual(interaction._outcome_options('miss', [.2, 0, 0], .08), ([.2, 0, 0], .08))
        for args in (('touch', None, .08), ('block', [.2, 0, 0], .08), ('miss', [.02, 0, 0], .08)):
            with self.assertRaises(ValueError):
                interaction._outcome_options(*args)

    def test_miss_measures_actual_clearance_and_reaching_the_offset_point(self):
        measured = interaction._miss_sample([.2, 0, 1.6], [0, 0, 1.6], [.2, 0, 0], .02, .08)
        self.assertTrue(measured['passed'])
        self.assertAlmostEqual(measured['clearance_m'], .2)
        self.assertFalse(interaction._miss_sample([0, 0, 1.6], [0, 0, 1.6], [.2, 0, 0], .02, .08)['passed'])
        self.assertFalse(interaction._miss_sample([.4, 0, 1.6], [0, 0, 1.6], [.2, 0, 0], .02, .08)['passed'])

    def test_block_and_miss_do_not_compile_a_struck_body_recoil(self):
        for outcome in ('block', 'miss'):
            plan = interaction.recoil_events('job', 'any-actor', 'response', impact_frame=12,
                    end_frame=24, direction_world=[0, 1, 0], outcome=outcome)
            self.assertEqual(plan['events'], [])
            self.assertTrue(plan['expectations']['reaction_skipped'])
            self.assertFalse(plan['hit_confirmed'])

    def test_default_guard_is_ready_on_first_frame_without_t_pose_filler(self):
        self.assertEqual(interaction.guard_weight(1, 1), 1)
        self.assertEqual(interaction.guard_weight(12, 1), 1)
        self.assertEqual(interaction.guard_weight(1, 1, 6), 0)
        self.assertEqual(interaction.guard_weight(7, 1, 6), 1)

    def test_long_guard_keeps_one_key_and_explicit_raise_only_two(self):
        self.assertEqual(interaction._guard_sample_frames(1, 0), [1])
        self.assertEqual(interaction._guard_sample_frames(1, 12), [1, 13])
        self.assertAlmostEqual(interaction.guard_weight(7, 1, 12), .5)

    def test_elbow_point_follows_world_body_facing_with_world_down_offset(self):
        forward = interaction._elbow_pole_point([1, 2, 1.4], [0, -1, 0])
        for actual, expected in zip(forward, [1., 1.9, 1.1]):
            self.assertAlmostEqual(actual, expected)
        turned = interaction._elbow_pole_point([1, 2, 1.4], [1, 0, 0])
        self.assertAlmostEqual(turned[0], 1.1)
        self.assertAlmostEqual(turned[1], 2)
        self.assertAlmostEqual(turned[2], 1.1)

    def test_strike_has_accelerating_approach_contact_and_complete_release(self):
        curve = lambda frame: interaction.strike_progress(frame, 1, 11, 12, 24)
        self.assertEqual(curve(1), 0)
        self.assertEqual(curve(11), 1)
        self.assertEqual(curve(12), 1)
        self.assertEqual(curve(24), 0)
        self.assertLess(curve(5)-curve(4), curve(10)-curve(9))
        self.assertTrue(all(0 <= curve(frame) <= 1 for frame in range(-5, 30)))

    def test_bad_strike_window_is_rejected(self):
        for timing in ((1, 1, 2, 10), (1, 10, 9, 20), (1, 10, 12, 12)):
            with self.assertRaises(ValueError):
                interaction.strike_progress(4, *timing)

    def test_recoil_is_zero_before_impact_and_settles(self):
        values = [interaction.recoil_amount(frame, 10, 40, 30) for frame in range(1, 45)]
        self.assertEqual(values[:10], [0]*10)
        self.assertGreater(values[11], values[10])
        self.assertLess(values[30], values[13])
        self.assertEqual(values[39:], [0]*5)

    def test_recoil_clock_uses_fps_instead_of_assuming_30(self):
        self.assertAlmostEqual(interaction.recoil_amount(13, 10, 40, 30),
                               interaction.recoil_amount(16, 10, 70, 60))


class SupportStepTests(unittest.TestCase):
    def setUp(self):
        self.steps = interaction.normalize_steps([
            {'foot': 'RightFoot', 'lift_frame': 3, 'land_frame': 11, 'position': [.1, .25, 0]},
            {'foot': 'LeftFoot', 'lift_frame': 12, 'land_frame': 20, 'position': [-.1, .35, 0]},
        ], 1, 24)
        self.initial = {'RightFoot': [.1, 0, 0], 'LeftFoot': [-.1, 0, 0]}

    def test_at_least_one_foot_is_planted_in_every_frame(self):
        for frame in range(1, 25):
            phases = [interaction.foot_state(self.steps, foot, frame, position)
                      for foot, position in self.initial.items()]
            self.assertTrue(any(phase['stance'] for phase in phases))

    def test_support_anchor_holds_and_swing_raises_actual_sole_target(self):
        state = lambda frame: interaction.foot_state(self.steps, 'RightFoot', frame, self.initial['RightFoot'])
        self.assertEqual(state(3)['position'], [.1, 0, 0])
        self.assertEqual(state(3)['stance'], True)
        self.assertAlmostEqual(state(7)['position'][2], .08)
        self.assertEqual(state(7)['stance'], False)
        self.assertEqual(state(11)['position'], [.1, .25, 0])
        self.assertEqual(state(24)['position'], [.1, .25, 0])

    def test_step_endpoints_have_continuous_position(self):
        first = interaction.foot_state(self.steps, 'RightFoot', 3, self.initial['RightFoot'])
        close = interaction.foot_state(self.steps, 'RightFoot', 3.000001, self.initial['RightFoot'])
        self.assertLess(math.dist(first['position'], close['position']), 1e-8)

    def test_swing_overlap_cannot_leave_both_feet_unsupported(self):
        changed = copy.deepcopy(self.steps); changed[1]['lift_frame'] = 6
        with self.assertRaisesRegex(ValueError, 'support foot'):
            interaction.normalize_steps(changed, 1, 24)

    def test_empty_inverted_outside_and_same_foot_steps_rejected(self):
        cases = ([], [{'foot': 'RightFoot', 'lift_frame': 5, 'land_frame': 4, 'position': [0, 0, 0]}],
                 [{'foot': 'RightFoot', 'lift_frame': 1, 'land_frame': 25, 'position': [0, 0, 0]}],
                 [self.steps[0], dict(self.steps[1], foot='RightFoot')])
        for steps in cases:
            with self.assertRaises(ValueError):
                interaction.normalize_steps(steps, 1, 24)

    def test_step_inputs_and_actor_identity_are_not_mutated(self):
        original = copy.deepcopy(self.steps)
        interaction.normalize_steps(self.steps, 1, 24)
        self.assertEqual(self.steps, original)

    def test_support_root_motion_lowers_hips_instead_of_stretching_leg(self):
        lowering = interaction.support_lowering([0, .4, .99], [0, 0, .05], 1.)
        self.assertGreater(lowering, 0)
        self.assertLessEqual(math.dist([0, .4, .99-lowering], [0, 0, .05]), .990001)

    def test_step_pole_uses_planned_body_facing_and_follows_root_motion(self):
        hip, ankle = [0, .1, .9], [0, -.1, .05]
        pole = interaction.support_pole(hip, ankle, [0, -1, 0])
        midpoint = [(a+b)*.5 for a, b in zip(hip, ankle)]
        bend = [a-b for a, b in zip(pole, midpoint)]
        axis = [b-a for a, b in zip(hip, ankle)]
        self.assertAlmostEqual(sum(a*b for a, b in zip(bend, axis)), 0)
        self.assertAlmostEqual(math.dist(pole, midpoint), .7)
        self.assertLess(bend[1], 0)
        moved = interaction.support_pole([1, .1, .9], [1, -.1, .05], [0, -1, 0])
        self.assertEqual([a-b for a, b in zip(moved, pole)], [1., 0., 0.])
        rotated = interaction.support_pole([0, 0, .9], [0, 0, .05], [1, 0, 0])
        self.assertAlmostEqual(rotated[0], .7)


class ContractGateTests(unittest.TestCase):
    def setUp(self):
        self.contract = {'id': 'performer-7:invented:impact', 'actor_id': 'performer-7', 'event_id': 'invented:impact',
                         'chain': 'RightHand', 'frame_start': 12, 'frame_end': 12, 'kind': 'strike',
                         'target': {'binding': {'actor_id': 'opponent.foo', 'bone': 'Head', 'local_point': [0, 0, 0]}}}
        self.plan = {'ok': True, 'expectations': {'contact_requirements': [self.contract],
                                                'impact_contact_ids': [self.contract['id']]}}
        self.registration = {'requirements': {'contacts': [self.contract]}}
        self.report = {'ok': True, 'passed': True, 'status': 'passed', 'contacts': [
            {'id': self.contract['id'], 'kind': 'strike', 'passed': True, 'coverage': 'full', 'tested_frames': 1, 'expected_frames': 1}]}
        self.contexts = [patch('agent_contacts.contact_requirements', return_value=self.registration),
                         patch('agent_contacts.check_contacts', return_value=self.report)]
        for context in self.contexts:
            context.start(); self.addCleanup(context.stop)

    def test_hit_is_confirmed_only_from_actual_complete_impact_contract(self):
        result = interaction.verify_interaction('job', self.plan)
        self.assertTrue(result['hit_confirmed'])
        self.assertFalse(result['naturalness_verified'])
        self.assertFalse(result['dynamics_verified'])

    def test_empty_checker_success_cannot_confirm_hit(self):
        self.report.update(status='empty', contacts=[])
        self.assertFalse(interaction.verify_interaction('job', self.plan)['hit_confirmed'])

    def test_missing_expected_impact_cannot_be_marked_hit(self):
        self.report['contacts'] = []
        self.assertFalse(interaction.verify_interaction('job', self.plan)['hit_confirmed'])

    def test_partial_or_failed_contact_cannot_confirm_hit(self):
        self.report['contacts'][0]['coverage'] = 'partial'
        self.assertFalse(interaction.verify_interaction('job', self.plan)['hit_confirmed'])
        self.report['contacts'][0].update(coverage='full', passed=False)
        self.assertFalse(interaction.verify_interaction('job', self.plan)['hit_confirmed'])

    def test_support_failure_blocks_whole_interaction(self):
        support = dict(self.contract, id='retreat:support', kind='support')
        plan = {'ok': True, 'expectations': {'contact_requirements': [support]}}
        self.registration['requirements']['contacts'].append(support)
        self.assertFalse(interaction.verify_interaction('job', [self.plan, plan])['hit_confirmed'])

    def test_reaction_cannot_claim_a_support_contract_is_a_hit(self):
        self.report['contacts'][0]['kind'] = 'support'
        plan = {'ok': True, 'expectations': {'required_impact_contact_ids': [self.contract['id']]}}
        self.assertFalse(interaction.verify_interaction('job', plan)['hit_confirmed'])

    def test_actual_block_is_verified_without_being_reported_as_a_hit(self):
        self.report['contacts'][0]['kind'] = 'block'
        block = dict(self.contract, kind='block')
        plan = {'ok': True, 'expectations': {'contact_requirements': [block],
                                            'block_contact_ids': [block['id']], 'outcome': 'block'}}
        result = interaction.verify_interaction('job', plan)
        self.assertTrue(result['passed'])
        self.assertTrue(result['block_confirmed'])
        self.assertFalse(result['hit_confirmed'])

    def test_miss_plan_has_no_strike_contact_and_uses_actual_pass_measurement(self):
        target = {'event_id': 'new-miss', 'source': 'procedural', 'start_frame': 12, 'end_frame': 12,
                  'mask': ['right_arm'], 'targets': {'RightHand': {'position': [.2, 0, 1.6], 'contact': False}}}
        miss = {'id': 'performer-7:new-miss', 'frame_start': 12, 'frame_end': 12}
        plan = interaction._plan('performer-7', 'new-miss', [target], {'miss_checks': [miss], 'outcome': 'miss'})
        self.assertEqual(plan['expectations']['contact_requirements'], [])
        self.report.update(status='empty', contacts=[])
        measured = {'id': miss['id'], 'passed': True, 'coverage': 'full', 'tested_frames': 1,
                    'expected_frames': 1, 'min_clearance_m': .2, 'max_aim_error_m': .001}
        with patch('agent_interactions._measure_miss', return_value=measured) as checker:
            result = interaction.verify_interaction('job', plan)
            checker.assert_called_once_with('job', miss, False)
            self.assertTrue(result['passed'])
            self.assertTrue(result['miss_confirmed'])
            self.assertFalse(result['hit_confirmed'])
            measured['passed'] = False
            self.assertFalse(interaction.verify_interaction('job', plan)['passed'])
            measured.update(passed=True, coverage='partial', tested_frames=0)
            self.assertFalse(interaction.verify_interaction('job', plan)['miss_confirmed'])

    def test_failed_compilation_or_no_contract_cannot_be_verified(self):
        with self.assertRaises(ValueError):
            interaction.verify_interaction('job', {'ok': False})
        with self.assertRaises(ValueError):
            interaction.verify_interaction('job', {'ok': True, 'expectations': {}})

    def test_event_compiler_uses_actor_parameter_and_open_intent(self):
        target = {'event_id': 'unknown', 'source': 'procedural', 'start_frame': 1, 'end_frame': 2,
                  'mask': ['right_arm'], 'targets': {'RightHand': {'binding': {'object': 'NovelProp'},
                        'contact': {'id': 'custom', 'start_frame': 2, 'end_frame': 2, 'kind': 'unlisted ritual'}}},
                  'intent': '绕开机关后碰一下新目标'}
        plan = interaction._plan('performer-x', 'unknown', [target])
        self.assertEqual(plan['expectations']['contact_requirements'][0]['actor_id'], 'performer-x')
        self.assertFalse(plan['hit_confirmed'])
        self.assertEqual(plan['events'][0]['intent'], target['intent'])

    def test_register_keeps_only_compact_expectations_and_default_verify_reads_them(self):
        root = {}
        plan = {**self.plan, 'actor_id': 'performer-7', 'event_id': 'invented',
                'events': [{'event_id': 'invented:impact', 'keyframes': [{'frame': 12, 'pose': {'large': {}}}]}]}
        plan['expectations'] = {**plan['expectations'], 'foot_phases': [{'frame': 12}]}
        with patch('agent_character.resolve_actor', return_value={'root': root}), \
             patch('agent_motion_timeline.register_event') as register:
            registered = interaction.register_interaction('job', plan)
        self.assertEqual(registered['events_count'], 1)
        self.assertEqual(register.call_args.args[:3], ('job', 'performer-7', 'invented:impact'))
        stored = root[interaction.INTERACTION_STORE]
        self.assertNotIn('foot_phases', stored)
        self.assertNotIn('contact_requirements', stored)
        self.assertNotIn('keyframes', stored)
        restored_root = json.loads(json.dumps(root))
        with patch('agent_character.actor_list', return_value={'actors': [{'actor_id': 'performer-7'}]}), \
             patch('agent_character.resolve_actor', return_value={'root': restored_root}):
            self.assertTrue(interaction.verify_interaction('job')['hit_confirmed'])

    def test_replacing_hit_recoil_with_miss_skip_removes_previous_subevents(self):
        root = {}
        hit = {'ok': True, 'actor_id': 'defender.new', 'event_id': 'reaction',
               'events': [{'event_id': 'reaction:torso'}],
               'expectations': {'required_impact_contact_ids': [self.contract['id']]}}
        skipped = {'ok': True, 'actor_id': 'defender.new', 'event_id': 'reaction',
                   'events': [], 'expectations': {'reaction_skipped': True}}
        with patch('agent_character.resolve_actor', return_value={'root': root}), \
             patch('agent_motion_timeline.register_event') as register, \
             patch('agent_motion_timeline.remove_event') as remove:
            interaction.register_interaction('job', hit)
            interaction.register_interaction('job', skipped)
        register.assert_called_once()
        remove.assert_called_once_with('job', 'defender.new', 'reaction:torso')
        stored = json.loads(root[interaction.INTERACTION_STORE])['reaction']
        self.assertEqual(stored['event_ids'], [])
        self.assertNotIn('required_impact_contact_ids', stored)


if __name__ == '__main__':
    unittest.main()
