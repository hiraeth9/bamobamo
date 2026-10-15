"""Meaningful composition rules, independent of a Blender installation."""
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from agent_motion_timeline import (blend_pose,coverage_event,event_weight,
                                   sample_keyframes,upsert_event,validate_event,commit_bake_range,
                                   final_base_contains_layer,preserve_edit_priority)
from agent_motion_quality import rotation_delta_degrees


def event(name,start,end,**kwargs):
    return {'event_id':name,'start_frame':start,'end_frame':end,'source':'procedural',
            'keyframes':[{'frame':start,'pose':{'arm':{'rotation_quaternion':[1,0,0,0]}}}],**kwargs}


class MotionTimelineTests(unittest.TestCase):
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


if __name__=='__main__':unittest.main()
