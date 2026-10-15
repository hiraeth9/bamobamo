"""The independent review must fail closed and preserve original plan evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import duel_plan
from semantic_review import load_review, semantic_review_prompt, validate_review, write_review


def review(status='covered'):
    return {
        'version': 1,
        'requirements': [{'description': '先走路，再跑步', 'status': status,
                          'evidence': 'single_plan.animations依次为Walk和Run，使用单人NLA顺序执行'}],
        'summary': '已逐项核对原文和计划。',
    }


class SemanticReviewTests(unittest.TestCase):
    def test_statuses_derive_passed_instead_of_trusting_model_flag(self):
        for status in ('covered', 'missing', 'unsupported'):
            with self.subTest(status=status):
                raw = review(status)
                raw['passed'] = status != 'covered'
                result = validate_review(raw, 'review_test')
                self.assertEqual(result['passed'], status == 'covered')
                self.assertEqual(result['job_id'], 'review_test')
        raw = review()
        raw['requirements'].append({'description': '助跑穿过窗户', 'status': 'unsupported',
                                    'evidence': '当前执行器只有普通跳跃，没有穿窗轨迹和窗台接触'})
        self.assertFalse(validate_review(raw, 'review_test')['passed'])

    def test_invalid_contract_or_empty_evidence_rejected(self):
        mutations = [
            lambda r: r.update(version=True),
            lambda r: r.update(version=2),
            lambda r: r.update(requirements=[]),
            lambda r: r.update(requirements='全部满足'),
            lambda r: r.update(requirements=[None]),
            lambda r: r['requirements'][0].update(status='probably'),
            lambda r: r['requirements'][0].update(status=[]),
            lambda r: r['requirements'][0].update(description='   '),
            lambda r: r['requirements'][0].update(evidence=''),
            lambda r: r['requirements'][0].pop('evidence'),
            lambda r: r.update(summary=None),
            lambda r: r.update(summary='  '),
        ]
        for mutate in mutations:
            raw = review()
            mutate(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_review(raw, 'review_test')
        with self.assertRaises(ValueError):
            validate_review([], 'review_test')
        with self.assertRaises(ValueError):
            validate_review(review(), '../outside')

    def test_requirement_limit_and_text_normalization(self):
        raw = review()
        raw['requirements'] = [copy.deepcopy(raw['requirements'][0]) for _ in range(32)]
        raw['requirements'][0]['description'] = '  保留自然语言要求  '
        raw['requirements'][0]['evidence'] = '  对应事件证据  '
        raw['summary'] = '  已完成复核  '
        result = validate_review(raw, 'review_test')
        self.assertEqual(result['requirements'][0]['description'], '保留自然语言要求')
        self.assertEqual(result['requirements'][0]['evidence'], '对应事件证据')
        self.assertEqual(result['summary'], '已完成复核')
        raw['requirements'].append(copy.deepcopy(raw['requirements'][0]))
        with self.assertRaises(ValueError):
            validate_review(raw, 'review_test')

    def test_review_requires_original_request_and_motion_plan(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(duel_plan, 'JOBS', Path(temp)):
            folder = duel_plan.job_directory('review_test')
            with self.assertRaisesRegex(ValueError, 'request.json'):
                write_review('review_test', review())
            duel_plan.atomic_json(folder / 'request.json', {'prompt': '先走路，再跑步'})
            with self.assertRaisesRegex(ValueError, '动作计划'):
                write_review('review_test', review())
            duel_plan.atomic_json(folder / 'single_plan.json', {'animations': ['Walk.fbx', 'Run.fbx']})
            self.assertTrue(write_review('review_test', review())['passed'])

    def test_failed_review_persists_and_cannot_be_replaced_by_success(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(duel_plan, 'JOBS', Path(temp)):
            folder = duel_plan.job_directory('review_test')
            duel_plan.atomic_json(folder / 'request.json', {'prompt': '先走路，再跑步翻越窗户'})
            duel_plan.atomic_json(folder / 'plan.json', {'version': 1, 'events': []})
            failed = write_review('review_test', review('unsupported'))
            path = folder / 'semantic_review.json'
            self.assertFalse(failed['passed'])
            self.assertEqual(json.loads(path.read_text()), failed)
            self.assertEqual(write_review('review_test', review('unsupported')), failed)
            self.assertEqual(load_review('review_test'), failed)
            with self.assertRaisesRegex(ValueError, '不允许覆盖'):
                write_review('review_test', review())
            self.assertEqual(json.loads(path.read_text()), failed)

    def test_source_hashes_bind_review_to_every_present_or_missing_file(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(duel_plan, 'JOBS', Path(temp)):
            folder = duel_plan.job_directory('review_test')
            duel_plan.atomic_json(folder / 'request.json', {'prompt': '先走路，再跑步'})
            duel_plan.atomic_json(folder / 'single_plan.json', {'animations': ['Walk.fbx', 'Run.fbx']})
            first = write_review('review_test', review())
            self.assertEqual(write_review('review_test', review()), first)
            self.assertEqual(load_review('review_test'), first)
            hashes = first['source_sha256']
            self.assertEqual(set(hashes), {'request.json', 'cast.json', 'single_plan.json',
                                         'plan.json', 'scene_plan.json'})
            self.assertRegex(hashes['request.json'], '^[0-9a-f]{64}$')
            self.assertRegex(hashes['single_plan.json'], '^[0-9a-f]{64}$')
            self.assertIsNone(hashes['cast.json'])
            self.assertIsNone(hashes['scene_plan.json'])

    def test_changed_request_plan_or_added_scene_cannot_reuse_review(self):
        changes = {
            'request.json': {'prompt': '改为跑步翻窗'},
            'single_plan.json': {'animations': ['Run.fbx', 'Walk.fbx']},
            'scene_plan.json': {'objects': [{'id': 'window_1', 'type': 'window'}]},
            'cast.json': {'actors': [{'id': 'A', 'name': '甲'}, {'id': 'B', 'name': '乙'}]},
        }
        for filename, change in changes.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp, \
                    patch.object(duel_plan, 'JOBS', Path(temp)):
                folder = duel_plan.job_directory('review_test')
                duel_plan.atomic_json(folder / 'request.json', {'prompt': '先走路，再跑步'})
                duel_plan.atomic_json(folder / 'single_plan.json', {'animations': ['Walk.fbx', 'Run.fbx']})
                write_review('review_test', review())
                duel_plan.atomic_json(folder / filename, change)
                with self.assertRaisesRegex(ValueError, '已变化'):
                    load_review('review_test')
                with self.assertRaisesRegex(ValueError, '不允许覆盖'):
                    write_review('review_test', review())

    def test_review_without_source_hashes_or_with_invalid_hashes_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(duel_plan, 'JOBS', Path(temp)):
            folder = duel_plan.job_directory('review_test')
            duel_plan.atomic_json(folder / 'request.json', {'prompt': '走路'})
            duel_plan.atomic_json(folder / 'single_plan.json', {'animations': ['Walk.fbx']})
            legacy = validate_review(review(), 'review_test')
            duel_plan.atomic_json(folder / 'semantic_review.json', legacy)
            with self.assertRaisesRegex(ValueError, '缺少来源哈希'):
                load_review('review_test')
        for hashes in ({}, {'request.json': '0' * 64},
                       {name: [] for name in ('request.json', 'cast.json', 'single_plan.json',
                                             'plan.json', 'scene_plan.json')}):
            raw = review()
            raw['source_sha256'] = hashes
            with self.subTest(hashes=hashes), self.assertRaises(ValueError):
                validate_review(raw, 'review_test')

    def test_write_hashes_are_computed_by_backend_and_preserved_by_validation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(duel_plan, 'JOBS', Path(temp)):
            folder = duel_plan.job_directory('review_test')
            duel_plan.atomic_json(folder / 'request.json', {'prompt': '走路'})
            duel_plan.atomic_json(folder / 'single_plan.json', {'animations': ['Walk.fbx']})
            raw = review()
            raw['source_sha256'] = {name: None for name in ('request.json', 'cast.json',
                                                          'single_plan.json', 'plan.json',
                                                          'scene_plan.json')}
            written = write_review('review_test', raw)
            self.assertIsNotNone(written['source_sha256']['request.json'])
            self.assertEqual(validate_review(written, 'review_test'), written)
            self.assertEqual(load_review('review_test'), written)

    def test_malformed_job_evidence_rejected_before_review(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(duel_plan, 'JOBS', Path(temp)):
            folder = duel_plan.job_directory('review_test')
            duel_plan.atomic_json(folder / 'request.json', {'prompt': '动作'})
            duel_plan.atomic_json(folder / 'single_plan.json', [])
            with self.assertRaisesRegex(ValueError, 'JSON对象'):
                semantic_review_prompt('review_test')
            (folder / 'single_plan.json').write_text('incomplete {', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '有效JSON'):
                semantic_review_prompt('review_test')
            duel_plan.atomic_json(folder / 'single_plan.json', {'animations': []})
            duel_plan.atomic_json(folder / 'request.json', {'prompt': ''})
            with self.assertRaisesRegex(ValueError, '原始用户提示词'):
                semantic_review_prompt('review_test')

    def test_prompt_carries_full_evidence_and_executor_limits(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(duel_plan, 'JOBS', Path(temp)):
            folder = duel_plan.job_directory('review_test')
            documents = {
                'request.json': {'prompt': '一个人先走路，再跑步跳跃窗户，15秒，相机显示全身', 'mode': 'auto'},
                'cast.json': {'actors': [{'id': 'A', 'name': '独特角色名称'}], 'actor_count': 1},
                'single_plan.json': {'animations': ['mixamo_fbx/basic/Walk.fbx',
                                                 'mixamo_fbx/basic/Run.fbx',
                                                 'mixamo_fbx/basic/Jump.fbx'],
                                     'duration_seconds': 15, 'camera_policy': 'fixed'},
                'scene_plan.json': {'objects': [{'id': 'exact_window', 'type': 'window',
                                                'position': [-1.2, 2.5, .9]}], 'interactions': []},
            }
            for filename, document in documents.items():
                duel_plan.atomic_json(folder / filename, document)
            prompt = semantic_review_prompt('review_test')
            evidence = prompt.split('<原始请求和完整已提交计划JSON，null表示未提交>\n')[1].split('\n</原始请求和完整已提交计划JSON>')[0]
            decoded = json.loads(evidence)
            for filename, document in documents.items():
                self.assertEqual(decoded[filename], document)
            self.assertIsNone(decoded['plan.json'])
            self.assertIn('print(bpy.wm_semantic_review("review_test", review))', prompt)
            self.assertIn('普通Walk/Run/Jump轮播加静态窗户不执行助跑穿窗', prompt)
            self.assertIn('先后顺序、空间目标与关系、人数和身份', prompt)
            self.assertIn('用户指定时长、镜头和全身可见要求', prompt)
            self.assertIn('open_window', prompt)
            self.assertIn('touch_table', prompt)
            self.assertIn('unsupported', prompt)
            self.assertIn('不渲染', prompt)
            self.assertIn('single_plan.locomotion', prompt)
            self.assertIn('cast.requires_locomotion', prompt)
            self.assertIn('动画列表中的Walk/Run不能证明真实路线和到达目标', prompt)
            self.assertIn('走到窗前打开窗户', prompt)
            self.assertIn('不按走跑关键词判断', prompt)
            self.assertIn('移动与触摸/开关/翻窗/跳跃/拳脚串联', prompt)


if __name__ == '__main__':
    unittest.main()
