"""Door passage is a stateful spatial relation, never an animation playlist."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import duel_plan
from scene_cast import validate_cast, write_cast
from scene_intents import validate_object_intents, validate_intent_coverage
from scene_plan import allocate_interaction_times, object_catalog, validate_scene, write_scene
from semantic_review import semantic_review_prompt
from single_plan import single_planning_prompt, validate_single, write_single


def chain(direction='outward'):
    return {'version': 1, 'duration_seconds': 15, 'room': {'enabled': True},
            'objects': [{'id': 'door_1', 'type': 'door'}], 'interactions': [
                {'object': 'door_1', 'action': 'open_door', 'start': 0, 'end': 5},
                {'object': 'door_1', 'action': 'pass_door', 'start': 5, 'end': 10, 'direction': direction},
                {'object': 'door_1', 'action': 'close_door', 'start': 10, 'end': 15}]}


def cast():
    return {'actors': [{'id': 'A', 'name': '人物'}], 'requires_scene': True,
            'requires_interaction': True, 'requires_locomotion': False, 'object_intents': [
                {'action': action, 'object_type': 'door', 'description': description}
                for action, description in [('open_door', '从室内打开门'),
                                             ('pass_door', '身体穿过门洞走到门外'),
                                             ('close_door', '在门外转身关门')]]}


def body():
    return {'version': 1, 'duration_seconds': 15,
            'animations': ['mixamo_fbx/basic/Idle.fbx', 'mixamo_fbx/basic/Walk.fbx']}


def extended_chain(seconds=20):
    raw=chain();raw['duration_seconds']=seconds
    raw['interactions'][0]['end']=6
    raw['interactions'][1].update(start=6,end=seconds-6)
    raw['interactions'][2].update(start=seconds-6,end=seconds)
    return raw


class DoorPassagePlanTests(unittest.TestCase):
    def test_outward_and_inward_infer_fixed_frame_operation_sides(self):
        for direction, before, after in [('outward', 'inside', 'outside'), ('inward', 'outside', 'inside')]:
            raw=chain(direction); normalized=validate_scene(raw,'door_plan')
            events=normalized['interactions']
            self.assertEqual(events[0]['door_side'], before)
            self.assertEqual(events[2]['door_side'], after)
            self.assertNotIn('door_side', events[1])
            self.assertEqual(events[1]['direction'], direction)
            self.assertEqual(normalized['duration_seconds'],15)
            self.assertEqual(validate_scene(json.loads(json.dumps(normalized)),'door_plan'),normalized)
            self.assertNotIn('door_side',raw['interactions'][0], 'validator修改原输入')

    def test_explicit_sides_must_match_actor_side_without_teleportation(self):
        for index, wrong_side in [(0,'outside'),(2,'inside')]:
            raw=chain();raw['interactions'][index]['door_side']=wrong_side
            with self.assertRaisesRegex(ValueError,'不能跨侧瞬移'):validate_scene(raw,'door_plan')
        raw=chain();raw['interactions'][0]['door_side']='inside';raw['interactions'][2]['door_side']='outside'
        self.assertEqual(validate_scene(raw,'door_plan')['interactions'][2]['door_side'],'outside')

    def test_pass_only_and_pass_then_close_require_initial_open_door(self):
        for count in (1,2):
            raw=chain();raw['objects'][0]['state']='open';raw['interactions']=raw['interactions'][1:1+count]
            self.assertEqual(len(validate_scene(raw,'door_plan')['interactions']),count)
            raw['objects'][0]['state']='closed'
            with self.assertRaisesRegex(ValueError,'要求门已打开'):validate_scene(raw,'door_plan')
        raw=chain();raw['interactions'][1].pop('direction')
        self.assertEqual(validate_scene(raw,'door_plan')['interactions'][1]['direction'],'outward')

    def test_close_before_pass_does_not_count_as_open_clearance(self):
        raw=chain()
        raw['interactions']=[{'object':'door_1','action':'open_door','start':0,'end':4},
                             {'object':'door_1','action':'close_door','start':4,'end':8},
                             {'object':'door_1','action':'pass_door','start':8,'end':15}]
        with self.assertRaisesRegex(ValueError,'要求门已打开'):validate_scene(raw,'door_plan')
        raw=chain('inward');raw['interactions'][2]['door_side']='outside'
        with self.assertRaisesRegex(ValueError,'不能跨侧瞬移'):validate_scene(raw,'door_plan')

    def test_passage_recommendation_preserves_explicit_geometry_and_legacy_default(self):
        catalog=object_catalog();door=catalog['objects']['door']
        self.assertEqual(door['size'],[.9,.14,2.1])
        self.assertIn('pull_bar',door['parts'])
        self.assertTrue({'handle','handle_back','entry','exit','contact_front','contact_back'}<=set(door['anchors']))
        raw=chain();raw['objects'][0].update(size=[.9,.18,2.2],open_angle_deg=65)
        spec=validate_scene(raw,'door_plan')['objects'][0]
        self.assertEqual(spec['size'],[.9,.18,2.2]);self.assertEqual(spec['open_angle_deg'],65)
        raw['objects'][0].update(size=[1.3,.14,2.1],open_angle_deg=80)
        self.assertEqual(validate_scene(raw,'door_plan')['objects'][0]['size'],[1.3,.14,2.1])

    def test_passage_helper_applies_special_defaults_only_to_referenced_door(self):
        raw=chain();raw['objects'].append({'id':'door_2','type':'door','position':[-1.4,2.5,0]})
        before=copy.deepcopy(raw)
        scene=validate_scene(raw,'door_plan');by_id={obj['id']:obj for obj in scene['objects']}
        self.assertEqual(by_id['door_1']['size'],[1.3,.14,2.1])
        self.assertEqual(by_id['door_1']['open_angle_deg'],80)
        self.assertEqual(by_id['door_2']['size'],[.9,.14,2.1])
        self.assertEqual(by_id['door_2']['open_angle_deg'],70)
        self.assertEqual(raw,before)
        self.assertEqual(validate_scene(scene,'door_plan'),scene)

    def test_passage_explicit_size_and_angle_override_defaults_independently(self):
        for explicit,size,angle in [({'size':[.9,.14,2.1]},[.9,.14,2.1],80),
                                    ({'open_angle_deg':70},[1.3,.14,2.1],70),
                                    ({'size':[.9,.14,2.1],'open_angle_deg':70},[.9,.14,2.1],70)]:
            raw=chain();raw['objects'][0].update(explicit)
            with self.subTest(explicit=explicit):
                door=validate_scene(raw,'door_plan')['objects'][0]
                self.assertEqual(door['size'],size);self.assertEqual(door['open_angle_deg'],angle)
        for explicit in ({'size':None},{'open_angle_deg':None}):
            raw=chain();raw['objects'][0].update(explicit)
            with self.subTest(explicit=explicit),self.assertRaises(ValueError):validate_scene(raw,'door_plan')

    def test_legacy_door_and_catalog_defaults_remain_narrow_and_seventy_degrees(self):
        raw=chain();raw['interactions'].pop(1)
        door=validate_scene(raw,'door_plan')['objects'][0]
        self.assertEqual(door['size'],[.9,.14,2.1]);self.assertEqual(door['open_angle_deg'],70)
        self.assertEqual(object_catalog()['objects']['door']['size'],[.9,.14,2.1])

    def test_fifteen_second_auto_allocation_keeps_state_order_and_fixed_sides(self):
        events=validate_scene(chain(),'door_plan')['interactions']
        result=allocate_interaction_times(events,[4,6,4],'auto')
        self.assertEqual([event['action'] for event in result],['open_door','pass_door','close_door'])
        self.assertEqual(result[0]['start'],0);self.assertEqual(result[-1]['end'],15)
        self.assertEqual(result[0]['door_side'],'inside');self.assertEqual(result[2]['door_side'],'outside')
        self.assertEqual(result[1]['direction'],'outward')
        for first,second in zip(result,result[1:]):self.assertAlmostEqual(first['end'],second['start'])
        with self.assertRaisesRegex(ValueError,'指定交互时刻不足'):allocate_interaction_times(events,[4,6,4],'exact')

    def test_old_open_close_shape_is_preserved_and_side_is_chain_only(self):
        raw=chain();raw['interactions']=[raw['interactions'][0],raw['interactions'][2]]
        normalized=validate_scene(raw,'door_plan')
        self.assertTrue(all('door_side' not in event and 'direction' not in event
                            for event in normalized['interactions']))
        raw['interactions'][0]['door_side']='inside'
        with self.assertRaisesRegex(ValueError,'仅用于包含pass_door'):validate_scene(raw,'door_plan')

    def test_passage_hand_defaults_left_and_explicit_right_is_not_rewritten(self):
        normalized=validate_scene(chain(),'door_plan')
        self.assertTrue(all(event['side']=='left' for event in normalized['interactions']))
        raw=chain()
        for event in raw['interactions']:event['side']='right'
        self.assertTrue(all(event['side']=='right' for event in validate_scene(raw,'door_plan')['interactions']))
        raw=chain();raw['interactions']=[raw['interactions'][0],raw['interactions'][2]]
        self.assertTrue(all(event['side']=='right' for event in validate_scene(raw,'door_plan')['interactions']))

    def test_exclusive_one_door_one_pass_and_single_actor(self):
        mutations=[lambda raw:raw['interactions'].insert(2,copy.deepcopy(raw['interactions'][1])),
                   lambda raw:raw['interactions'][2].update(object='door_2'),
                   lambda raw:raw['interactions'].append({'object':'table_1','action':'touch_table','start':12,'end':15})]
        for mutate in mutations:
            raw=chain();raw['objects'].extend([{'id':'door_2','type':'door','position':[-1.4,2.5,0]},
                                             {'id':'table_1','type':'table'}]);mutate(raw)
            with self.subTest(raw=raw),self.assertRaises(ValueError):validate_scene(raw,'door_plan')
        with self.assertRaisesRegex(ValueError,'单人'):validate_scene(chain(),'door_plan',2)
        raw=cast();raw['actors'].append({'id':'B','name':'另一人物'})
        self.assertFalse(validate_cast(raw,'door_plan')['supported'])

    def test_minimum_time_direction_and_unknown_fields_fail_closed(self):
        mutations=[lambda raw:raw['interactions'][1].update(end=7.9),
                   lambda raw:raw['interactions'][1].update(direction='sideways'),
                   lambda raw:raw['interactions'][1].update(direction=[]),
                   lambda raw:raw['interactions'][1].update(door_side='inside'),
                   lambda raw:raw['interactions'][0].update(direction='outward'),
                   lambda raw:raw['interactions'][0].update(door_side='front'),
                   lambda raw:raw['interactions'][1].update(speed=4),
                   lambda raw:raw['interactions'][1].update(gait='run')]
        for mutate in mutations:
            raw=chain();mutate(raw)
            with self.subTest(raw=raw),self.assertRaises(ValueError):validate_scene(raw,'door_plan')
        raw=chain();raw['interactions'][1]['end']=8;raw['interactions'][2]['start']=8
        self.assertEqual(validate_scene(raw,'door_plan')['interactions'][1]['end'],8)

    def test_body_sources_and_independent_route_cannot_replace_passage(self):
        person=validate_cast(cast(),'door_plan');environment=validate_scene(chain(),'door_plan')
        self.assertTrue(person['supported'])
        validate_intent_coverage(person,environment,validate_single(body(),'door_plan'))
        raw=body();raw['animations']=['mixamo_fbx/basic/Run.fbx']
        with self.assertRaisesRegex(ValueError,'Idle/Walk'):validate_intent_coverage(person,environment,raw)
        raw=body();raw['locomotion']={'version':1}
        with self.assertRaisesRegex(ValueError,'不同时提交通用locomotion'):validate_intent_coverage(person,environment,raw)
        person['requires_locomotion']=True
        self.assertFalse(validate_cast(person,'door_plan')['supported'])

    def test_intents_cannot_omit_or_substitute_walk_for_actual_passage(self):
        self.assertTrue(validate_object_intents([cast()['object_intents'][1]])[0]['supported'])
        wrong=copy.deepcopy(cast()['object_intents'][1]);wrong['object_type']='window'
        self.assertFalse(validate_object_intents([wrong])[0]['supported'])
        scene=validate_scene(chain(),'door_plan');scene['interactions'].pop(1)
        with self.assertRaisesRegex(ValueError,'缺少匹配动作'):validate_intent_coverage(validate_cast(cast(),'door_plan'),scene,body())

    def test_submission_keeps_original_duration_and_requires_saved_door_chain(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(duel_plan,'JOBS',Path(temporary)):
            folder=duel_plan.job_directory('door_plan')
            duel_plan.atomic_json(folder/'request.json',{'mode':'auto','prompt':'打开门走出去转身关门，15秒全身'})
            write_cast('door_plan',cast());write_scene('door_plan',chain());saved=write_single('door_plan',body())
            self.assertEqual(saved['duration_seconds'],15)
            self.assertNotIn('locomotion',saved)
            prompt=semantic_review_prompt('door_plan')
            for text in ['pass_door','door_side','固定门框','不能像翻窗一样','closed门没有开门事件则missing']:
                self.assertIn(text,prompt)
            self.assertIn('真实近铰链横拉杆',prompt)
            self.assertIn('不是几何认证',prompt)

    def test_saved_passage_scene_enables_twenty_and_thirty_second_person_plans(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(duel_plan,'JOBS',Path(temporary)):
            for seconds in (20,30):
                job=f'door_plan_{seconds}';folder=duel_plan.job_directory(job)
                write_cast(job,cast());write_scene(job,extended_chain(seconds))
                original=(folder/'scene_plan.json').read_bytes()
                saved=write_single(job,{**body(),'duration_seconds':seconds})
                self.assertEqual(saved['duration_seconds'],seconds)
                self.assertEqual(saved['frame_count'],seconds*30)
                # The worker and door CLI use this same two-argument validator.
                self.assertEqual(validate_single(saved,job),saved)
                self.assertEqual((folder/'scene_plan.json').read_bytes(),original)
                with self.assertRaises(ValueError):
                    validate_single({**body(),'duration_seconds':30.01},job)

    def test_larger_duration_cannot_be_self_authorized_or_used_by_other_single_scenes(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(duel_plan,'JOBS',Path(temporary)):
            raw={**body(),'duration_seconds':20,'pass_door':True,'requires_scene':True}
            with self.assertRaisesRegex(ValueError,'未执行字段'):validate_single(raw,'missing_scene')
            raw={**body(),'duration_seconds':20}
            with self.assertRaisesRegex(ValueError,'超过15秒仅限'):validate_single(raw,'missing_scene')
            variants=[]
            static=extended_chain();static['interactions']=[];variants.append(static)
            ordinary=extended_chain();ordinary['interactions'].pop(1);variants.append(ordinary)
            vault=extended_chain();vault['objects']=[{'id':'window_1','type':'window'}]
            vault['interactions']=[{'object':'window_1','action':'vault_window','start':0,'end':20}]
            variants.append(vault)
            for index,environment in enumerate(variants):
                job=f'other_single_{index}'
                duel_plan.atomic_json(duel_plan.job_directory(job)/'scene_plan.json',environment)
                with self.subTest(environment=environment),self.assertRaisesRegex(ValueError,'超过15秒仅限'):
                    validate_single(raw,job)
            self.assertEqual(validate_single(body(),'old_single')['duration_seconds'],15)

    def test_extended_duration_revalidates_saved_scene_and_rejects_locomotion(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(duel_plan,'JOBS',Path(temporary)):
            folder=duel_plan.job_directory('door_plan')
            invalid=[]
            mismatch=chain();invalid.append(mismatch)
            closed=extended_chain();closed['interactions']=closed['interactions'][1:];invalid.append(closed)
            wrong_side=extended_chain();wrong_side['interactions'][2]['door_side']='inside';invalid.append(wrong_side)
            wrong_object=extended_chain();wrong_object['interactions'][1]['object']='missing';invalid.append(wrong_object)
            for environment in invalid:
                duel_plan.atomic_json(folder/'scene_plan.json',environment)
                with self.subTest(environment=environment),self.assertRaises(ValueError):
                    write_single('door_plan',{**body(),'duration_seconds':20})
                self.assertFalse((folder/'single_plan.json').exists())
            duel_plan.atomic_json(folder/'scene_plan.json',extended_chain())
            movement={'version':1,'start_position':[0,0],'start_yaw_deg':0,
                      'segments':[{'start':0,'end':20,'gait':'walk','waypoints':[[0,0],[0,-1]]}]}
            with self.assertRaisesRegex(ValueError,'通用路线移动时长仍为1至15秒'):
                validate_single({**body(),'duration_seconds':20,'locomotion':movement},'door_plan')
            self.assertEqual(validate_single({**body(),'duration_seconds':20},'door_plan')['frame_count'],600)

    def test_unspecified_chain_recommendation_is_twenty_seconds_without_extending_explicit_time(self):
        recommendation=object_catalog()['interactions']['pass_door']['planning_recommendation']
        self.assertEqual(recommendation['full_chain_seconds'],20)
        self.assertEqual(recommendation['suggested_event_intervals_seconds'],
                         {'open_door':[0,6],'pass_door':[6,14],'close_door':[14,20]})
        self.assertIn('完整链未指定建议20秒',single_planning_prompt('打开门走出去关门','door_plan'))
        events=validate_scene(chain(),'door_plan')['interactions']
        with self.assertRaisesRegex(ValueError,'交互至少需要'):
            allocate_interaction_times(events,[5,9,3],'auto')
        self.assertEqual(events[-1]['end'],15)
        explicit=chain();explicit['timing_policy']='exact'
        self.assertEqual(validate_scene(explicit,'door_plan')['duration_seconds'],15)

    def test_review_uses_computed_fixed_sides_states_exact_times_and_saved_dimensions(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(duel_plan,'JOBS',Path(temporary)):
            for direction,origin,destination in [('outward','inside','outside'),('inward','outside','inside')]:
                job='review_'+direction;folder=duel_plan.job_directory(job)
                environment=extended_chain();environment['interactions'][1]['direction']=direction
                environment['timing_policy']='exact'
                environment['objects'][0].update(size=[1.1,.16,2.2],open_angle_deg=65)
                duel_plan.atomic_json(folder/'request.json',{'prompt':'指定尺寸的门，6秒开，14秒穿过，20秒关上','mode':'auto'})
                write_cast(job,cast());write_scene(job,environment)
                write_single(job,{**body(),'duration_seconds':20})
                original=(folder/'scene_plan.json').read_bytes()
                prompt=semantic_review_prompt(job)
                tag='后端计算的门侧与状态事实JSON，null表示没有pass_door'
                facts=json.loads(prompt.split('<'+tag+'>\n')[1].split('\n</后端计算的门侧与状态事实JSON>')[0])
                self.assertTrue(facts['protocol_valid'])
                self.assertEqual(facts['initial_actor_side'],origin)
                self.assertEqual(facts['final_actor_side'],destination)
                self.assertEqual([event['action'] for event in facts['events']],['open_door','pass_door','close_door'])
                opened,passed,closed=facts['events']
                self.assertEqual((opened['door_state_before'],opened['door_state_after']),('closed','open'))
                self.assertEqual((passed['actor_side_before'],passed['actor_side_after']),(origin,destination))
                self.assertEqual(passed['door_state_before'],'open')
                self.assertEqual(closed['door_side'],destination)
                self.assertEqual(facts['final_door_state'],'closed')
                self.assertEqual(facts['door_size_m'],[1.1,.16,2.2]);self.assertEqual(facts['door_open_angle_deg'],65)
                self.assertEqual(facts['timing_policy'],'exact')
                self.assertEqual([(event['start'],event['end']) for event in facts['events']],[(0,6),(6,14),(14,20)])
                self.assertIn('不是实际皮肤',facts['geometry_note'])
                self.assertEqual((folder/'scene_plan.json').read_bytes(),original)
                environment['interactions'][2]['door_side']=origin
                duel_plan.atomic_json(folder/'scene_plan.json',environment)
                invalid_prompt=semantic_review_prompt(job)
                invalid=json.loads(invalid_prompt.split('<'+tag+'>\n')[1].split('\n</后端计算的门侧与状态事实JSON>')[0])
                self.assertFalse(invalid['protocol_valid'])
                self.assertIn('不能跨侧瞬移',invalid['error'])


if __name__=='__main__':unittest.main()
