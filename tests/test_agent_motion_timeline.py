"""Meaningful composition rules, independent of a Blender installation."""
import math
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from agent_motion_timeline import (blend_pose,blend_root,coverage_event,event_weight,
                                   sample_keyframes,upsert_event,validate_event,commit_bake_range,
                                   final_base_contains_layer,preserve_edit_priority,contact_commit_order,
                                   _bound_object_owners, active_contact_targets, contact_dependency_schedule)
from agent_motion_timeline import _placement_heading_delta
from agent_motion_quality import rotation_delta_degrees


def event(name,start,end,**kwargs):
    return {'event_id':name,'start_frame':start,'end_frame':end,'source':'procedural',
            'keyframes':[{'frame':start,'pose':{'arm':{'rotation_quaternion':[1,0,0,0]}}}],**kwargs}


class MotionTimelineTests(unittest.TestCase):
    def test_heading_alignment_only_accepts_explicit_opt_in_for_full_body_sources(self):
        for source in ('library', 'hybrid'):
            value=event('source',1,20,source=source,motion_id='chosen',align_heading=True)
            self.assertTrue(validate_event(value)['align_heading'])
        for value in (event('masked',1,20,source='library',motion_id='chosen',mask=['left_arm'],align_heading=True),
                      event('procedural',1,20,align_heading=True)):
            with self.assertRaisesRegex(ValueError,'full-body library/hybrid'):
                validate_event(value)
        for value in (1,'true',None):
            with self.assertRaisesRegex(ValueError,'true or false'):
                validate_event(event('source',1,20,source='library',motion_id='chosen',align_heading=value))

    def test_heading_alignment_does_not_silently_override_explicit_direction(self):
        for field in ('keyframes','root_path'):
            value=event('source',1,20,source='library',motion_id='chosen',align_heading=True)
            value[field]=[{'frame':1,'root':[0,0,0],'root_rotation_quaternion':[1,0,0,0]}]
            with self.assertRaisesRegex(ValueError,'align_heading=False'):
                validate_event(value)
            value['align_heading']=False
            self.assertFalse(validate_event(value)['align_heading'])

    def test_heading_alignment_keeps_world_trajectory_coordinates_and_input_pose(self):
        value=event('source',1,20,source='library',motion_id='chosen',align_heading=True,
                    root_strategy='trajectory',root_path=[{'frame':1,'root':[4,5,0]},
                                                          {'frame':20,'root':[7,8,0]}])
        actual=validate_event(value)
        self.assertEqual(actual['root_path'],value['root_path'])
        self.assertEqual(actual['keyframes'],value['keyframes'])

    def test_placement_uses_real_window_pose_and_previous_body_not_root_axis_alone(self):
        armature=object()
        calls=[]
        def heading(arm,pose):
            self.assertIs(arm,armature);calls.append(pose)
            return pose['measured_heading']
        previous={'measured_heading':math.radians(99)}
        incoming={'measured_heading':math.radians(-90)}
        with patch.dict(sys.modules,{'agent_motion_features':SimpleNamespace(pose_heading=heading)}):
            delta=_placement_heading_delta(armature,previous,incoming)
        self.assertAlmostEqual(math.degrees(delta),-171)
        self.assertEqual(calls,[previous,incoming])
        self.assertAlmostEqual(math.sin(incoming['measured_heading']+delta),math.sin(previous['measured_heading']))
        self.assertAlmostEqual(math.cos(incoming['measured_heading']+delta),math.cos(previous['measured_heading']))

    def test_first_clip_aligns_to_actor_forward_and_turn_change_survives_placement(self):
        incoming={'measured_heading':math.radians(75)}
        with patch.dict(sys.modules,{'agent_motion_features':SimpleNamespace(pose_heading=lambda arm,pose:pose['measured_heading'])}):
            delta=_placement_heading_delta(object(),None,incoming)
        self.assertAlmostEqual(math.degrees(delta),-75)
        end_heading=incoming['measured_heading']+math.radians(99)
        self.assertAlmostEqual(math.degrees(end_heading+delta),99)

    def schedule(self, events, first, last):
        return contact_dependency_schedule(list(events), events, first, last,
                                            lambda binding: {binding['actor_id']} if 'actor_id' in binding else set())

    def touch(self, name, start, end, other, **updates):
        return event(name, start, end, mask=['right_arm'], targets={
            'RightHand': {'binding': {'actor_id': other, 'bone': 'Head'}, **updates}})

    def test_nonoverlapping_attack_and_counterattack_use_each_frames_order(self):
        result = self.schedule({'A': [self.touch('attack', 1, 8, 'B')],
                                'B': [self.touch('counter', 9, 16, 'A')]}, 1, 16)
        self.assertEqual(result['frame_orders'][4], ['B', 'A'])
        self.assertEqual(result['frame_orders'][12], ['A', 'B'])
        self.assertTrue(result['dynamic_commit_order'])
        self.assertEqual([(w['frame_start'], w['frame_end']) for w in result['dependency_windows']],
                         [(1, 8), (9, 16)])

    def test_only_actual_same_frame_cycles_are_rejected_with_the_frame(self):
        with self.assertRaisesRegex(ValueError, 'Frame 6: Cyclic actor contact'):
            self.schedule({'A': [self.touch('attack', 1, 12, 'B')],
                           'B': [self.touch('counter', 6, 12, 'A')]}, 1, 12)

    def test_zero_influence_and_release_do_not_leave_a_reverse_dependency(self):
        result = self.schedule({
            'A': [self.touch('release', 1, 12, 'B', keyframes=[
                {'frame': 1, 'influence': 1}, {'frame': 6, 'influence': 0}, {'frame': 12, 'influence': 0}])],
            'B': [self.touch('counter', 1, 12, 'A', keyframes=[
                {'frame': 1, 'influence': 0}, {'frame': 6, 'influence': 0}, {'frame': 7, 'influence': 1}])]}, 1, 12)
        self.assertEqual(result['frame_orders'][1], ['B', 'A'])
        self.assertEqual(result['frame_orders'][7], ['A', 'B'])
        self.assertFalse(active_contact_targets([self.touch('off', 1, 12, 'B', influence=0)], 4))

    def test_event_blending_and_contact_qa_window_are_distinct(self):
        attack = self.touch('attack', 1, 8, 'B', contact={'start_frame': 3, 'end_frame': 4})
        attack['blend_in_frames'] = 2
        result = self.schedule({'A': [attack], 'B': [self.touch('first_frame', 1, 1, 'A')]}, 1, 8)
        self.assertEqual(result['frame_orders'][1], ['A', 'B'])
        self.assertEqual(result['frame_orders'][2], ['B', 'A'])
        self.assertTrue(active_contact_targets([attack], 6), 'Release IK is not limited to its contact QA window')

    def test_final_pose_layers_suppress_only_their_actual_window(self):
        snapshot = event('final', 1, 12, priority=100, final_pose_layers=True)
        hidden = self.touch('old_targets', 1, 24, 'B')
        result = self.schedule({'A': [snapshot, hidden], 'B': [self.touch('counter', 1, 12, 'A')]}, 1, 12)
        self.assertEqual(result['commit_order'], ['A', 'B'])
        with self.assertRaisesRegex(ValueError, 'Frame 13: Cyclic actor contact'):
            self.schedule({'A': [snapshot, hidden], 'B': [self.touch('later', 13, 24, 'A')]}, 1, 24)

    def test_stable_one_way_order_and_compact_dependency_windows(self):
        result = self.schedule({'A': [self.touch('attack', 1, 450, 'B')], 'B': []}, 1, 450)
        self.assertEqual(result['commit_order'], ['B', 'A'])
        self.assertFalse(result['dynamic_commit_order'])
        self.assertEqual(len(result['dependency_windows']), 1)
        self.assertEqual(result['dependency_windows'][0]['frame_end'], 450)

    def test_three_actor_same_frame_chain_is_target_first(self):
        result = self.schedule({'A': [self.touch('a', 1, 5, 'B')],
                                'B': [self.touch('b', 1, 5, 'C')], 'C': []}, 1, 5)
        self.assertEqual(result['commit_order'], ['C', 'B', 'A'])

    def test_pole_binding_obeys_same_frame_target_order_and_cycle_detection(self):
        guard = self.touch('guard', 1, 8, 'A', pole_binding={'actor_id': 'B', 'bone': 'LeftShoulder'},
                           contact=False)
        result = self.schedule({'A': [guard], 'B': []}, 1, 8)
        self.assertEqual(result['commit_order'], ['B', 'A'])
        self.assertEqual(result['dependency_windows'][0]['dependencies'], {'A': ['B']})
        with self.assertRaisesRegex(ValueError, 'Frame 4: Cyclic actor contact'):
            self.schedule({'A': [guard], 'B': [self.touch('return', 4, 8, 'A')]}, 1, 8)

    def test_contact_target_precedes_attacker_even_if_registered_after_it(self):
        self.assertEqual(contact_commit_order(['attacker','reactor'],{'attacker':{'reactor'}}),
                         ['reactor','attacker'])
        self.assertEqual(contact_commit_order(['A','B','C'],{'A':{'B'},'B':{'C'}}),['C','B','A'])

    def test_static_external_target_and_self_reference_do_not_create_false_cycle(self):
        self.assertEqual(contact_commit_order(['A','unrelated'],{'A':{'static','A'}}),['A','unrelated'])

    def test_cyclic_contacts_are_reported_instead_of_using_arbitrary_actor_order(self):
        with self.assertRaisesRegex(ValueError,'Cyclic actor contact'):
            contact_commit_order(['A','B'],{'A':{'B'},'B':{'A'}})
        for actors in ([],['A','A'],['A','']):
            with self.assertRaises(ValueError):contact_commit_order(actors,{})

    def test_held_prop_dependency_follows_constraints_even_before_influence_ramps_up(self):
        target=SimpleNamespace(name='reactor_rig')
        prop=SimpleNamespace(name='held_prop',parent=None,animation_data=None,modifiers=[],
                             constraints=[SimpleNamespace(target=target,influence=0,mute=False)])
        self.assertEqual(_bound_object_owners(prop,{'reactor_rig':'reactor'}),{'reactor'})
        prop.parent=prop
        self.assertEqual(_bound_object_owners(prop,{'reactor_rig':'reactor'}),{'reactor'})

    def test_unbaked_target_driver_is_explicitly_diagnosed(self):
        prop=SimpleNamespace(name='driver_prop',parent=None,constraints=[],modifiers=[],
                             animation_data=SimpleNamespace(drivers=[object()]))
        with self.assertRaisesRegex(ValueError,'Bake contact target drivers'):
            _bound_object_owners(prop,{})

    def test_explicit_masked_edit_takes_over_auto_preservation_without_replacing_base(self):
        old=event('preserved',1,30,priority=100,_auto_preserved_outside_capture=True)
        changed,diagnostic=preserve_edit_priority([old],event('new',21,25,mask=['arm']))
        self.assertEqual(changed['priority'],101)
        self.assertEqual(diagnostic['priority_source'],'automatic_takeover_of_preserved_snapshot')
        self.assertEqual(old['priority'],100)
        explicit,diagnostic=preserve_edit_priority([old],event('new',21,25,mask=['arm'],priority=0))
        self.assertEqual(explicit['priority'],0)
        self.assertTrue(diagnostic['shadowed_by_preserved_base'])

    def test_final_capture_suppresses_only_lower_priority_layers_in_its_window(self):
        base=event('actual',11,20,priority=100,capture='evaluated_final_channels')
        low=event('old',1,30,mask=['arm'],priority=0)
        high=event('new',11,20,mask=['arm'],priority=101)
        self.assertTrue(final_base_contains_layer(base,low,11))
        self.assertTrue(final_base_contains_layer(base,low,20))
        for frame in (10,21):self.assertFalse(final_base_contains_layer(base,low,frame))
        self.assertFalse(final_base_contains_layer(base,high,15))
        self.assertFalse(final_base_contains_layer(dict(base,mask=['arm']),low,15))

    def test_helper_final_pose_marker_is_separate_from_capture_authorization(self):
        base=event('helper',11,20,priority=200,final_pose_layers=True)
        self.assertNotIn('capture',base)
        self.assertTrue(final_base_contains_layer(base,event('old',1,30,priority=100),15))
        self.assertFalse(final_base_contains_layer(event('planned',11,20,priority=200),event('old',1,30),15))

    def test_local_capture_does_not_become_predecessor_after_original_clip_continues(self):
        walk=event('walk',91,150);patch=event('capture',101,110,priority=100)
        base,at=coverage_event([walk,patch],151)
        self.assertEqual((base['event_id'],at),('walk',150))
        base,at=coverage_event([walk,patch],105)
        self.assertEqual((base['event_id'],at),('capture',105))

    def test_preservation_archive_keeps_original_tie_order(self):
        archived=event('z_archive',1,20,_preserved_event_order_id='a')
        sibling=event('a_more',1,20)
        self.assertEqual(coverage_event([archived,sibling],10)[0]['event_id'],'a_more')

    def test_partial_commit_covers_bank_previous_bake_and_active_animation(self):
        result=commit_bake_range((101,110),{'last_commit':{'frame_start':1,'frame_end':180}},
                                 [event('bank',91,240)],[(0.5,250.2)])
        self.assertEqual(result,(0,251))

    def test_execution_field_aliases_are_diagnosed_without_closing_metadata(self):
        for fields,diagnostic in (({'root_trajectory':[]},'root_path'),
                                  ({'source_window_frames':[1,20]},'seconds')):
            with self.assertRaisesRegex(ValueError,diagnostic):validate_event(event('bad',1,20,**fields))
        self.assertEqual(validate_event(event('semantic',1,20,metadata={'root_trajectory':'new idea'}))['metadata']['root_trajectory'],'new idea')

    def test_trajectory_requires_real_root_coordinates(self):
        with self.assertRaisesRegex(ValueError,'actual root'):validate_event(event('missing',1,20,root_strategy='trajectory'))
        value=validate_event(event('path',1,20,root_strategy='trajectory',root_path=[{'frame':1,'root':[2,3,4]}]))
        self.assertEqual(value['root_path'][0]['root'],[2,3,4])

    def test_repair_event_preserves_unrelated_motion(self):
        walk=event('walk',1,50);wave=event('wave',12,30,mask=['arm'])
        result=upsert_event([walk,wave],event('wave',15,35,mask=['arm']))
        self.assertEqual(result[0],walk)
        self.assertEqual(len(result),2)
        self.assertEqual(result[1]['end_frame'],35)

    def test_gaps_and_before_first_hold_motion_pose(self):
        events=[event('walk',10,20),event('gesture',31,45)]
        for frame,expected,sample in ((1,'walk',10),(25,'walk',20),(50,'gesture',45)):
            base,at=coverage_event(events,frame)
            self.assertEqual((base['event_id'],at),(expected,sample))

    def test_overlay_never_steals_root_owner(self):
        base,at=coverage_event([event('walk',1,50),event('wave',1,50,mask=['arm'])],25)
        self.assertEqual(base['event_id'],'walk')
        with self.assertRaises(ValueError):validate_event(event('wave',1,50,mask=['arm'],root_owner=True))

    def test_upper_overlay_preserves_running_legs_and_pelvis(self):
        base={'leg':{'location':[.1,.2,.3],'rotation_quaternion':[1,0,0,0]},
              'arm':{'rotation_quaternion':[1,0,0,0]}}
        overlay={'leg':{'location':[9,9,9]},'arm':{'rotation_quaternion':[0,1,0,0]}}
        actual=blend_pose(base,overlay,.5,{'arm'})
        self.assertEqual(actual['leg'],base['leg'])
        self.assertAlmostEqual(actual['arm']['rotation_quaternion'][0],2**-.5)
        self.assertEqual(base['arm']['rotation_quaternion'],[1,0,0,0])

    def test_quaternion_sign_does_not_spin_at_boundary(self):
        actual=blend_pose({'arm':{'rotation_quaternion':[1,0,0,0]}},
                          {'arm':{'rotation_quaternion':[-1,0,0,0]}},.5)
        self.assertEqual(actual['arm']['rotation_quaternion'],[1,0,0,0])

    def test_measurement_uses_physical_angle_not_quaternion_sign(self):
        self.assertEqual(rotation_delta_degrees([1,0,0,0],[-1,0,0,0]),0)
        self.assertAlmostEqual(rotation_delta_degrees([1,0,0,0],[2**-.5,2**-.5,0,0]),90)
        self.assertAlmostEqual(rotation_delta_degrees([2,0,0,0],[-1,0,0,0]),0)

    def test_partial_pose_preserves_unchanged_channels(self):
        actual=blend_pose({'hand':{'location':[1,2,3],'scale':[1,1,1]}},
                          {'hand':{'scale':[2,3,4]}})
        self.assertEqual(actual['hand']['location'],[1,2,3])
        self.assertEqual(actual['hand']['scale'],[2,3,4])

    def test_sparse_pose_and_root_keys_interpolate_same_clock(self):
        keys=[{'frame':1,'root':[0,0,0],'pose':{'hand':{'location':[0,0,0]}}},
              {'frame':21,'root':[4,0,0],'pose':{'hand':{'location':[0,2,0]}}}]
        actual=sample_keyframes(keys,11)
        self.assertEqual(actual['root'],[2,0,0])
        self.assertEqual(actual['pose']['hand']['location'],[0,1,0])

    def test_new_sparse_channel_inherits_actual_context_not_rest(self):
        keys=[{'frame':1,'pose':{}},
              {'frame':11,'pose':{'hand':{'location':[4,0,0]}}}]
        actual=sample_keyframes(keys,6,baseline={'hand':{'location':[2,0,0]}})
        self.assertEqual(actual['pose']['hand']['location'],[3,0,0])

    def test_missing_end_key_channel_holds_previous_authored_value(self):
        keys=[{'frame':1,'pose':{'hand':{'location':[2,0,0]}}},
              {'frame':11,'pose':{'arm':{'scale':[1.2,1,1]}}}]
        actual=sample_keyframes(keys,11,baseline={'hand':{'location':[9,0,0]}})
        self.assertEqual(actual['pose']['hand']['location'],[2,0,0])

    def test_intent_is_not_an_action_enum(self):
        actual=validate_event(event('unlisted',1,12,intent='同时低头数珠子并向月球敬礼'))
        self.assertIn('月球',actual['intent'])

    def test_invalid_numeric_data_cannot_enter_scene(self):
        for change in ({'end_frame':0},{'start_frame':1.5},{'weight':2},
                       {'keyframes':[{'frame':1,'root':[math.inf,0,0]}]}):
            with self.assertRaises(ValueError):validate_event(event('bad',1,10,**change))

    def test_crossfade_has_no_rest_component(self):
        outgoing={'arm':{'location':[2,0,0]}}
        incoming={'arm':{'location':[4,0,0]}}
        w=event_weight(event('next',20,40,blend_in_frames=10),25)
        self.assertEqual(blend_pose(outgoing,incoming,w)['arm']['location'],[3,0,0])

    def test_root_crossfade_preserves_outgoing_motion_and_blends_heading_and_scale(self):
        outgoing={'root':[2,1,0],'root_rotation_quaternion':[1,0,0,0],'root_scale':[1,1,1]}
        incoming={'root':[4,3,0],'root_rotation_quaternion':[2**-.5,0,0,2**-.5],'root_scale':[1.2,.8,1]}
        actual=blend_root(outgoing,incoming,.5)
        self.assertEqual(actual['root'],[3,2,0])
        self.assertEqual(actual['root_scale'],[1.1,.9,1])
        self.assertAlmostEqual(rotation_delta_degrees(outgoing['root_rotation_quaternion'],actual['root_rotation_quaternion']),45)
        advanced=dict(outgoing,root=[2.4,1,0])
        self.assertAlmostEqual(blend_root(advanced,incoming,.5)['root'][0]-actual['root'][0],.2)
        self.assertEqual(outgoing['root'],[2,1,0])

    def test_root_crossfade_endpoints_and_quaternion_sign_do_not_reset_transform(self):
        outgoing={'root':[2,1,0],'root_rotation_quaternion':[1,0,0,0],'root_scale':[1,1,1]}
        incoming={'root':[4,3,0],'root_rotation_quaternion':[-1,0,0,0],'root_scale':[1.2,.8,1]}
        self.assertEqual(blend_root(outgoing,incoming,0),outgoing)
        end=blend_root(outgoing,incoming,1)
        self.assertEqual(end['root'],incoming['root'])
        self.assertEqual(end['root_scale'],incoming['root_scale'])
        self.assertEqual(end['root_rotation_quaternion'],[1,0,0,0])


if __name__=='__main__':unittest.main()
