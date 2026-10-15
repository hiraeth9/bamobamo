"""Validate configurable window-vault plans without substituting ordinary Jump."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import duel_plan
import duel_worker
from scene_cast import validate_cast, write_cast
from scene_intents import validate_intent_coverage, validate_object_intents
from scene_plan import validate_scene, write_scene
from semantic_review import write_review
from single_plan import write_single


def vault_scene():
    return {'version': 1, 'duration_seconds': 9, 'room': {'enabled': True},
            'objects': [{'id': 'window_1', 'type': 'window'}],
            'interactions': [{'actor': 'A', 'object': 'window_1', 'action': 'vault_window',
                              'start': 0, 'end': 9}]}


def vault_cast():
    return {'actors': [{'id': 'A', 'name': '人物'}], 'requires_scene': True,
            'requires_interaction': True, 'requires_locomotion': False, 'object_intents': [{
                'action': 'vault_window', 'object_type': 'window',
                'description': '先走后跑，双手撑窗，翻越窗洞后落地站稳',
            }]}


class WindowVaultPlanTests(unittest.TestCase):
    def test_vault_defaults_are_persistent_and_closed_windows_are_allowed(self):
        plan = validate_scene(vault_scene(), 'vault_plan')
        event = plan['interactions'][0]
        self.assertEqual(event['approach'], 'walk_run')
        self.assertEqual(event['walk_fraction'], .45)
        self.assertEqual(plan['objects'][0]['state'], 'closed')
        self.assertEqual(len(plan['interactions']), 1)
        self.assertEqual(plan, validate_scene(json.loads(json.dumps(plan)), 'vault_plan', 1, 9))

    def test_approach_modes_and_walk_fraction_constraints(self):
        for approach in ('walk_run', 'run', 'walk'):
            raw = vault_scene(); raw['interactions'][0]['approach'] = approach
            event = validate_scene(raw, 'vault_plan')['interactions'][0]
            self.assertEqual(event['approach'], approach)
            self.assertEqual('walk_fraction' in event, approach == 'walk_run')
            if approach != 'walk_run':
                raw['interactions'][0]['walk_fraction'] = .45
                with self.assertRaisesRegex(ValueError, '仅在approach=walk_run'):
                    validate_scene(raw, 'vault_plan')
        for fraction in (.2, .7):
            raw = vault_scene(); raw['interactions'][0]['walk_fraction'] = fraction
            self.assertEqual(validate_scene(raw, 'vault_plan')['interactions'][0]['walk_fraction'], fraction)
        for key, value in [('approach', 'jump'), ('approach', []), ('walk_fraction', .19),
                           ('walk_fraction', .71), ('walk_fraction', True), ('walk_fraction', float('nan'))]:
            raw = vault_scene(); raw['interactions'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_scene(raw, 'vault_plan')

    def test_vault_minimum_time_and_exclusive_interaction(self):
        raw = vault_scene(); raw['duration_seconds'] = 6; raw['interactions'][0]['end'] = 6
        self.assertEqual(validate_scene(raw, 'vault_plan')['duration_seconds'], 6)
        raw['interactions'][0]['start'] = .1
        with self.assertRaisesRegex(ValueError, '至少6秒'):
            validate_scene(raw, 'vault_plan')
        raw = vault_scene(); raw['interactions'].insert(0, {
            'object': 'window_1', 'action': 'open_window', 'start': 0, 'end': 2,
        })
        with self.assertRaisesRegex(ValueError, '唯一物品交互'):
            validate_scene(raw, 'vault_plan')
        raw = vault_scene(); raw['interactions'].append(copy.deepcopy(raw['interactions'][0]))
        with self.assertRaisesRegex(ValueError, '唯一物品交互'):
            validate_scene(raw, 'vault_plan')

    def test_window_geometry_options_preserve_existing_bounds(self):
        for x, width, height in ((-1.5, 1.0, 1.0), (.8, 1.5, 1.6)):
            raw = vault_scene()
            raw['objects'][0].update(position=[x, 2.5, .85], size=[width, .12, height])
            obj = validate_scene(raw, 'vault_plan')['objects'][0]
            self.assertEqual(obj['position'][0], x)
            self.assertEqual(obj['size'], [width, .12, height])
        raw = vault_scene(); raw['objects'][0]['size'] = [1.61, .12, 1.2]
        with self.assertRaises(ValueError): validate_scene(raw, 'vault_plan')
        raw = vault_scene(); raw['objects'][0]['position'] = [0, 0, .85]
        with self.assertRaisesRegex(ValueError, '后墙'): validate_scene(raw, 'vault_plan')
        with self.assertRaisesRegex(ValueError, '单人'): validate_scene(vault_scene(), 'vault_plan', 2)

    def test_breaking_glass_and_multiple_actor_vault_remain_unsupported(self):
        raw = vault_cast()
        raw['object_intents'].insert(0, {
            'action': 'break', 'object_type': 'window', 'description': '撞碎玻璃后跳出去',
        })
        result = validate_cast(raw, 'vault_plan')
        self.assertFalse(result['supported'])
        self.assertIn('破坏', result['message'])
        with self.assertRaisesRegex(ValueError, '尚不支持'):
            validate_intent_coverage(result, vault_scene())
        raw = vault_cast(); raw['actors'].append({'id': 'B', 'name': '另一人物'})
        self.assertFalse(validate_cast(raw, 'vault_plan')['supported'])
        self.assertFalse(validate_object_intents([{
            'action': 'jump_table', 'object_type': 'table', 'description': '跳过桌子',
        }])[0]['supported'])

    def test_vault_routes_to_its_own_executor_and_keeps_user_duration(self):
        with tempfile.TemporaryDirectory() as temporary:
            jobs = Path(temporary)
            with patch.object(duel_plan, 'JOBS', jobs), patch.object(duel_worker, 'JOBS', jobs):
                job_id = 'vault_route'
                folder = duel_plan.job_directory(job_id)
                duel_plan.atomic_json(folder / 'request.json', {
                    'mode': 'auto', 'prompt': '一个人先走路再跑步翻过窗户，9秒，全身入镜',
                })
                write_cast(job_id, vault_cast())
                write_scene(job_id, vault_scene())
                write_single(job_id, {'version': 1, 'duration_seconds': 9,
                                     'animations': ['mixamo_fbx/basic/Walk.fbx', 'mixamo_fbx/basic/Run.fbx']})
                commands = []
                def execute(job_folder, command, log_name, timeout):
                    commands.append(log_name)
                    if log_name == 'semantic_review.log':
                        write_review(job_id, {'version': 1, 'requirements': [{
                            'description': '先走路，再跑步翻至窗外，9秒', 'status': 'covered',
                            'evidence': '唯一vault_window事件approach=walk_run，start=0、end=9',
                        }], 'summary': '计划具备专用翻窗事件'})
                    elif log_name == 'build.log':
                        self.assertIn(str(duel_plan.PROJECT / 'scripts/window_vault_scene.py'), command)
                        self.assertEqual(command[-2:], ['--plan', str(folder / 'single_plan.json')])
                        self.assertEqual(json.loads((folder / 'single_plan.json').read_text())['duration_seconds'], 9)
                        raise RuntimeError('stop before real Blender execution')
                    else:
                        self.fail(f'不应执行其它流程：{log_name}')
                with patch.object(duel_worker, 'run_logged', side_effect=execute), \
                     patch.object(duel_worker, 'render_scene_job') as render:
                    result = duel_worker.run_job(job_id, already_planned=True)
                self.assertEqual(commands, ['semantic_review.log', 'build.log'])
                self.assertIn('stop before real Blender execution', result['error'])
                render.assert_not_called()


if __name__ == '__main__':
    unittest.main()
