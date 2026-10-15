"""Validate the object contract and refusal to discard required scenery/actions."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import duel_plan
import duel_worker
from scene_cast import write_cast,validate_cast
from scene_plan import validate_scene,write_scene,allocate_interaction_times


def scene():
    return {'version':1,'duration_seconds':10,'room':{'enabled':True},
            'objects':[{'id':k+'_1','type':k} for k in ('table','chair','window','door','box')]}


class ScenePlanTests(unittest.TestCase):
    def test_five_defaults_and_grounding(self):
        plan=validate_scene(scene(),'props_test')
        self.assertEqual(len(plan['objects']),5)
        self.assertEqual(plan['objects'][2]['position'][1],2.5)
        self.assertEqual(plan['objects'][2]['position'][2],.85)
        self.assertTrue(all(o['position'][2]==0 for o in plan['objects'] if o['type']!='window'))

    def test_multiple_instances_preserve_unique_ids(self):
        raw={'version':1,'duration_seconds':2,'objects':[{'id':'box_a','type':'box'},{'id':'box_b','type':'box'}]}
        plan=validate_scene(raw,'props_test')
        self.assertNotEqual(plan['objects'][0]['position'],plan['objects'][1]['position'])
        raw['objects'][1]['id']='box_a'
        with self.assertRaises(ValueError): validate_scene(raw,'props_test')

    def test_large_room_plan_survives_persistence_and_revalidation(self):
        raw=scene();raw['room']['size']=[12,12,5]
        first=validate_scene(raw,'props_test')
        self.assertEqual(first,validate_scene(json.loads(json.dumps(first)),'props_test',1,10))
        self.assertEqual(first['objects'][2]['position'][1],6)

    def test_open_then_close_and_already_open(self):
        raw=scene();raw['interactions']=[{'object':'window_1','action':'open_window','start':0,'end':5},
                                      {'object':'window_1','action':'close_window','start':5,'end':10}]
        plan=validate_scene(raw,'props_test')
        self.assertEqual([e['action'] for e in plan['interactions']],['open_window','close_window'])
        raw['interactions']=raw['interactions'][1:];raw['objects'][2]['state']='open'
        self.assertEqual(len(validate_scene(raw,'props_test')['interactions']),1)

    def test_invalid_hinge_state_order(self):
        for actions in (['close_window'],['open_window','open_window']):
            raw=scene();raw['interactions']=[{'object':'window_1','action':a,'start':i*5,'end':(i+1)*5} for i,a in enumerate(actions)]
            with self.subTest(actions=actions),self.assertRaises(ValueError): validate_scene(raw,'props_test')

    def test_unknown_assets_actions_and_targets(self):
        for mutation in ('asset','action','target'):
            raw=scene();raw['interactions']=[{'object':'table_1','action':'touch_table','start':0,'end':5}]
            if mutation=='asset': raw['objects'][0]['type']='sofa'
            elif mutation=='action': raw['interactions'][0]['action']='lift_table'
            else: raw['interactions'][0]['object']='missing'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError): validate_scene(raw,'props_test')

    def test_static_duel_allowed_interaction_duel_rejected(self):
        raw=scene();self.assertEqual(len(validate_scene(raw,'props_test',2)['objects']),5)
        raw['interactions']=[{'object':'table_1','action':'touch_table','start':0,'end':5}]
        with self.assertRaisesRegex(ValueError,'单人'): validate_scene(raw,'props_test',2)

    def test_invalid_dimensions_position_duration_and_overlap(self):
        mutations=[lambda r:r['objects'][0].update(size=[1,.7,float('nan')]),
                   lambda r:r['objects'][0].update(position=[0,0,1]),
                   lambda r:r['objects'][2].update(position=[0,0,.85]),
                   lambda r:r['objects'][3].update(position=[-1.35,2.5,0]),
                   lambda r:r['objects'][0].update(position=[2.8,0,0]),
                   lambda r:r['objects'][0].update(position=[0,2.2,0],size=[2.4,.4,.75],yaw_deg=90)]
        for change in mutations:
            raw=scene();change(raw)
            with self.assertRaises(ValueError): validate_scene(raw,'props_test')
        with self.assertRaisesRegex(ValueError,'时长'): validate_scene(scene(),'props_test',1,5)

    def test_interactions_cannot_overlap_or_be_too_short(self):
        for end,start in ((1,5),(5,4)):
            raw=scene();raw['interactions']=[{'object':'table_1','action':'touch_table','start':0,'end':end},
                                           {'object':'window_1','action':'open_window','start':start,'end':10}]
            with self.assertRaises(ValueError): validate_scene(raw,'props_test')

    def test_scene_requires_cast_and_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(duel_plan,'JOBS',Path(tmp)):
            with self.assertRaises(ValueError): write_scene('props_test',scene())
            write_cast('props_test',{'actors':[{'id':'A','name':'甲'}],'requires_scene':True})
            first=write_scene('props_test',scene());self.assertEqual(write_scene('props_test',scene()),first)
            raw=scene();raw['objects'].pop()
            with self.assertRaises(ValueError): write_scene('props_test',raw)

    def test_cast_flags_have_consistent_types(self):
        for flags in ({'requires_scene':'true'},{'requires_interaction':True}):
            with self.assertRaises(ValueError): validate_cast({'actors':[{'id':'A','name':'甲'}],**flags},'props_test')

    def test_required_flags_and_scene_operations_checked_at_submission(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(duel_plan,'JOBS',Path(tmp)):
            folder=duel_plan.job_directory('props_test')
            duel_plan.atomic_json(folder/'request.json',{'mode':'auto'})
            with self.assertRaisesRegex(ValueError,'requires_scene'):
                write_cast('props_test',{'actors':[{'id':'A','name':'甲','requires_scene':True}]})
            write_cast('props_test',{'actors':[{'id':'A','name':'甲'}],'requires_scene':True,'requires_interaction':True,'requires_locomotion':False,
                                     'object_intents':[{'action':'open_window','object_type':'window','description':'开窗'}]})
            with self.assertRaisesRegex(ValueError,'interactions'): write_scene('props_test',scene())
        from single_plan import validate_single
        with self.assertRaisesRegex(ValueError,'wm_scene_plan'):
            validate_single({'version':1,'animations':['mixamo_fbx/basic/Idle.fbx'],'interactions':[]},'props_test')

    def test_auto_timing_keeps_actions_overall_duration_and_gaps(self):
        events=[{'id':'p01','action':'touch_table','start':0,'end':3},
                {'id':'p02','action':'open_window','start':4,'end':9},
                {'id':'p03','action':'close_window','start':9,'end':15}]
        result=allocate_interaction_times(events,[2.5,6.5,2.5])
        self.assertEqual([e['action'] for e in result],[e['action'] for e in events])
        self.assertEqual(result[0]['start'],0);self.assertEqual(result[-1]['end'],15)
        self.assertAlmostEqual(result[1]['start']-result[0]['end'],1)
        self.assertGreaterEqual(result[1]['end']-result[1]['start'],6.5)
        with self.assertRaises(ValueError): allocate_interaction_times(events,[2.5,6.5,2.5],'exact')
        with self.assertRaises(ValueError): allocate_interaction_times(events,[5,10,5])

    def test_required_scene_and_interaction_are_not_silently_discarded(self):
        for missing in ('scene','interaction'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as tmp:
                jobs=Path(tmp)
                with patch.object(duel_plan,'JOBS',jobs),patch.object(duel_worker,'JOBS',jobs):
                    folder=duel_plan.job_directory('props_test')
                    duel_plan.atomic_json(folder/'request.json',{'prompt':'扶桌然后开窗','mode':'auto'})
                    def planning(*args):
                        write_cast('props_test',{'actors':[{'id':'A','name':'甲'}],'requires_scene':True,'requires_interaction':True,'requires_locomotion':False,
                                                 'object_intents':[{'action':'touch_table','object_type':'table','description':'扶桌'},
                                                                   {'action':'open_window','object_type':'window','description':'开窗'}]})
                        duel_plan.atomic_json(folder/'single_plan.json',{'duration_seconds':10})
                        if missing=='interaction': duel_plan.atomic_json(folder/'scene_plan.json',validate_scene(scene(),'props_test'))
                    with patch.object(duel_worker,'run_logged',side_effect=planning) as commands:
                        result=duel_worker.run_job('props_test')
                    self.assertFalse(result['ok']);self.assertEqual(commands.call_count,1)
                    self.assertIn('场景计划' if missing=='scene' else '没有交互动作',result['error'])


if __name__=='__main__': unittest.main()
