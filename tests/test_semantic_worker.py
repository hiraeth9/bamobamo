"""Reject incomplete semantics before building, including legacy saved plans."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import duel_plan
import duel_worker
from scene_cast import write_cast
from scene_plan import validate_scene
from single_plan import validate_single
from semantic_review import write_review,load_review


def review(status='covered'):
    return {'version':1,'requirements':[{'description':'按原文完成动作和对象关系',
            'status':status,'evidence':'普通动画轮播没有vault_window穿窗事件' if status!='covered' else '计划字段与已实现能力相符'}],
            'summary':'独立复核结果'}


class SemanticWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.jobs=Path(self.temp.name)
        self.patches=[patch.object(duel_plan,'JOBS',self.jobs),patch.object(duel_worker,'JOBS',self.jobs)]
        for p in self.patches: p.start()
        self.job_id='semantic_worker'
        self.folder=duel_plan.job_directory(self.job_id)

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def save_job(self,window=True,intents=None):
        duel_plan.atomic_json(self.folder/'request.json',{'prompt':'先走路，再跑步跳跃窗户' if window else '一个人先走路，再跑步','mode':'auto'})
        items=intents or []
        write_cast(self.job_id,{'actors':[{'id':'A','name':'人物'}],'requires_scene':window,
                               'requires_interaction':bool(items),'requires_locomotion':False,'object_intents':items})
        duel_plan.atomic_json(self.folder/'single_plan.json',validate_single({
            'version':1,'animations':['mixamo_fbx/basic/Walk.fbx','mixamo_fbx/basic/Run.fbx','mixamo_fbx/basic/Jump.fbx'],
            'duration_seconds':6},self.job_id))
        if window:
            duel_plan.atomic_json(self.folder/'scene_plan.json',validate_scene({
                'version':1,'duration_seconds':6,'objects':[{'id':'window_1','type':'window'}]},self.job_id))

    def test_declared_vault_without_executor_event_stops_before_audit_or_build(self):
        self.save_job(intents=[{'action':'vault_window','object_type':'window','description':'跑步穿过窗洞到另一侧'}])
        with patch.object(duel_worker,'run_logged') as execute:
            result=duel_worker.run_job(self.job_id,already_planned=True)
        self.assertFalse(result['ok']);execute.assert_not_called()
        self.assertIn('匹配动作',result['error'])
        self.assertNotIn('video',result)

    def test_empty_ledger_and_static_window_still_receive_independent_audit(self):
        self.save_job()
        def audit(folder,command,log_name,timeout):
            self.assertEqual(command[0],'hermes');self.assertEqual(log_name,'semantic_review.log')
            write_review(self.job_id,review('missing'))
        with patch.object(duel_worker,'run_logged',side_effect=audit) as execute:
            result=duel_worker.run_job(self.job_id,already_planned=True)
        self.assertFalse(result['ok']);self.assertEqual(execute.call_count,1)
        self.assertFalse(result['semantic_review_passed'])
        self.assertIn('穿窗',result['error']);self.assertNotIn('video',result)

    def test_no_report_malformed_report_and_timeout_never_build(self):
        for failure in ('absent','malformed','timeout'):
            with self.subTest(failure=failure):
                self.save_job()
                (self.folder/'semantic_review.json').unlink(missing_ok=True)
                def audit(*args):
                    if failure=='malformed': (self.folder/'semantic_review.json').write_text('{broken')
                    if failure=='timeout': raise RuntimeError('semantic review timeout')
                with patch.object(duel_worker,'run_logged',side_effect=audit) as execute:
                    result=duel_worker.run_job(self.job_id,already_planned=True)
                self.assertFalse(result['ok']);self.assertEqual(execute.call_count,1)
                self.assertNotIn('video',result)
                self.assertEqual(duel_worker.status(self.job_id)['stage'],'failed')

    def test_changed_request_cannot_use_cached_successful_review(self):
        self.save_job();write_review(self.job_id,review())
        duel_plan.atomic_json(self.folder/'request.json',{'prompt':'搬起箱子再翻窗','mode':'auto'})
        with patch.object(duel_worker,'run_logged') as execute:
            result=duel_worker.run_job(self.job_id,already_planned=True)
        self.assertFalse(result['ok']);execute.assert_not_called()
        self.assertIn('已变化',result['error'])
        self.assertFalse(result['semantic_review_passed'])

    def test_legacy_plan_is_normalized_before_review_and_build(self):
        self.save_job(window=False)
        duel_plan.atomic_json(self.folder/'single_plan.json',{'version':1,
            'animations':['mixamo_fbx/basic/Walk.fbx','mixamo_fbx/basic/Run.fbx'],'duration_seconds':6})
        def execute(folder,command,log_name,timeout):
            if log_name=='semantic_review.log':
                saved=json.loads((folder/'single_plan.json').read_text())
                self.assertEqual(saved['frame_count'],180)
                write_review(self.job_id,review())
            else:
                self.assertTrue(load_review(self.job_id)['passed'])
                raise RuntimeError('stop after confirming build received valid audit')
        with patch.object(duel_worker,'run_logged',side_effect=execute) as commands:
            result=duel_worker.run_job(self.job_id,already_planned=True)
        self.assertEqual(commands.call_count,2)
        self.assertIn('stop after confirming',result['error'])
        self.assertTrue(result['semantic_review_passed'])

    def test_new_auto_submissions_require_operation_ledger(self):
        duel_plan.atomic_json(self.folder/'request.json',{'prompt':'翻窗','mode':'auto'})
        with self.assertRaisesRegex(ValueError,'object_intents'):
            write_cast(self.job_id,{'actors':[{'id':'A','name':'人物'}],'requires_scene':True,'requires_interaction':False})


if __name__=='__main__': unittest.main()
