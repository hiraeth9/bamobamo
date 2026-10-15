"""A geometric repair must pass a fresh review of the user's requirements."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from duel_cases import fixture
import duel_plan
import duel_worker
from scene_cast import write_cast
from semantic_review import load_review, write_review


class SemanticRepairTests(unittest.TestCase):
    def test_repair_that_changes_requested_move_stops_before_second_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            jobs = Path(temporary)
            with patch.object(duel_plan, 'JOBS', jobs), patch.object(duel_worker, 'JOBS', jobs):
                job_id = 'semantic_repair'
                folder = duel_plan.job_directory(job_id)
                duel_plan.atomic_json(folder / 'request.json', {
                    'mode': 'auto',
                    'prompt': '两人拳法交锋，A直拳命中B躯干，B上勾拳被A格挡，最后双方拉开距离。6秒，全身入镜。',
                })
                initial = fixture(job_id, duration=6)
                initial['events'].append({
                    'kind': 'move', 'actor': 'both', 'direction': 'apart',
                    'distance': .15, 'start': 4.2, 'end': 5.2,
                })
                calls = []
                reviewed_hashes = []

                def execute(job_folder, command, log_name, timeout):
                    self.assertEqual(job_folder, folder)
                    calls.append(log_name)
                    if log_name == 'hermes.log':
                        self.assertEqual(command[0], 'hermes')
                        write_cast(job_id, {
                            'actors': [{'id': 'A', 'name': '先手人物'}, {'id': 'B', 'name': '反击人物'}],
                            'requires_scene': False, 'requires_interaction': False, 'requires_locomotion': False, 'object_intents': [],
                        })
                        duel_plan.write_plan(job_id, initial)
                    elif log_name == 'semantic_review.log':
                        self.assertEqual(command[0], 'hermes')
                        current = json.loads((folder / 'plan.json').read_text())
                        first_review = not reviewed_hashes
                        self.assertEqual(current['events'][-1]['direction'], 'apart' if first_review else 'closer')
                        accepted = write_review(job_id, {
                            'version': 1,
                            'requirements': [{
                                'description': '交锋后双方拉开距离',
                                'status': 'covered' if first_review else 'missing',
                                'evidence': '最后move事件direction=apart'
                                            if first_review else '修正后的最后move事件direction=closer，遗漏了拉开距离的结果',
                            }],
                            'summary': '原计划满足拉开距离要求' if first_review else '修正改变了用户要求的最后移动结果',
                        })
                        reviewed_hashes.append(accepted['source_sha256']['plan.json'])
                        (folder / log_name).write_text('首次复核通过' if first_review else '修正复核未通过')
                    elif log_name == 'build.log':
                        self.assertEqual(calls.count('build.log'), 1, '第二次复核失败后不应继续构建')
                        self.assertTrue(load_review(job_id)['passed'])
                        duel_plan.atomic_json(folder / 'quality.json', {
                            'passed': False, 'reason': '目标距离需要几何修正',
                        })
                        raise RuntimeError('geometry preflight failed')
                    elif log_name == 'repair_1.log':
                        candidate = copy.deepcopy(json.loads((folder / 'plan.json').read_text()))
                        candidate['events'][-1]['direction'] = 'closer'
                        duel_plan.write_plan(job_id, candidate, revision=1)
                    else:
                        self.fail(f'不应执行额外构建或渲染命令：{log_name}')

                with patch.object(duel_worker, 'run_logged', side_effect=execute), \
                     patch.object(duel_worker, 'render_scene_job') as render, \
                     patch.object(duel_worker, 'verify_video') as verify:
                    result = duel_worker.run_job(job_id)

                self.assertEqual(calls, ['hermes.log', 'semantic_review.log', 'build.log',
                                         'repair_1.log', 'semantic_review.log'])
                render.assert_not_called()
                verify.assert_not_called()
                self.assertFalse(result['ok'])
                self.assertFalse(result['semantic_review_passed'])
                self.assertIn('停止生成', result['error'])
                self.assertIn('拉开距离', result['error'])
                self.assertNotIn('video', result)
                self.assertEqual(duel_worker.status(job_id)['stage'], 'failed')

                archived = json.loads((folder / 'semantic_review_v0.json').read_text())
                self.assertTrue(archived['passed'])
                self.assertEqual(archived['source_sha256']['plan.json'], reviewed_hashes[0])
                self.assertEqual((folder / 'semantic_review_v0.log').read_text(), '首次复核通过')
                self.assertTrue((folder / 'quality_v0.json').is_file())
                self.assertTrue((folder / 'plan_v0.json').is_file())

                latest = load_review(job_id)
                self.assertFalse(latest['passed'])
                self.assertEqual(latest['requirements'][0]['status'], 'missing')
                repaired_hash = hashlib.sha256((folder / 'plan.json').read_bytes()).hexdigest()
                self.assertEqual(latest['source_sha256']['plan.json'], repaired_hash)
                self.assertEqual(reviewed_hashes[1], repaired_hash)
                self.assertNotEqual(reviewed_hashes[0], reviewed_hashes[1])
                saved_result = json.loads((folder / 'result.json').read_text())
                self.assertEqual(saved_result, result)


if __name__ == '__main__':
    unittest.main()
