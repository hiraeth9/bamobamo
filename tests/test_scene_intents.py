"""Guard semantic object actions against silent downgrade to scenery or clips."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from scene_intents import validate_object_intents, validate_intent_coverage


def intent(action='open_window', object_type='window', description='握住把手打开窗户'):
    return {'action': action, 'object_type': object_type, 'description': description}


def cast(intents=None):
    items = [intent()] if intents is None else intents
    return {'actor_count': 1, 'requires_scene': True,
            'requires_interaction': bool(items), 'object_intents': items}


def scene(actions=None):
    operations = ['open_window'] if actions is None else actions
    return {'objects': [{'id': 'window_1', 'type': 'window'}],
            'interactions': [{'object': 'window_1', 'action': action} for action in operations]}


class ObjectIntentTests(unittest.TestCase):
    def test_supported_ledger_is_json_safe_and_revalidates(self):
        raw = [intent(), intent('touch_table', 'table', '右手扶住桌面')]
        result = validate_object_intents(raw, '一个人开窗，再扶桌')
        self.assertTrue(all(item['supported'] and not item['reason'] for item in result))
        self.assertEqual(validate_object_intents(json.loads(json.dumps(result))), result)
        self.assertNotIn('supported', raw[0])

    def test_unsupported_actions_are_retained_and_cannot_claim_support(self):
        for action, kind in [('climb_window', 'window'), ('sit', 'chair'),
                             ('carry', 'box'), ('break', 'window'), ('climb_table', 'table')]:
            with self.subTest(action=action):
                result = validate_object_intents([{**intent(action, kind, '用户要求的关键操作'),
                                                  'supported': True, 'reason': ''}])
                self.assertEqual(result[0]['action'], action)
                self.assertFalse(result[0]['supported'])
                self.assertTrue(result[0]['reason'])

    def test_supported_action_requires_matching_object_type(self):
        result = validate_object_intents([intent('open_window', 'door')])
        self.assertFalse(result[0]['supported'])
        with self.assertRaisesRegex(ValueError, '不能用于door'):
            validate_intent_coverage(cast(result), scene())

    def test_malformed_ledger_is_rejected(self):
        invalid = [None, {}, 'open_window', [None], [intent() for _ in range(17)],
                   [{**intent(), 'action': ''}], [{**intent(), 'action': '../open_window'}],
                   [{**intent(), 'action': 'OpenWindow'}], [{**intent(), 'action': []}],
                   [{**intent(), 'object_type': 'sofa'}], [{**intent(), 'object_type': []}],
                   [{**intent(), 'description': '  '}], [{**intent(), 'description': None}]]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_object_intents(raw)
        for prompt in ('', 3):
            with self.assertRaises(ValueError): validate_object_intents([], prompt)

    def test_static_scenery_and_plain_jump_are_allowed(self):
        self.assertEqual(validate_intent_coverage(cast([]), scene([]),
                          {'animations': ['mixamo_fbx/basic/Jump.fbx']}), [])
        self.assertEqual(validate_intent_coverage(cast([]), None), [])

    def test_vault_cannot_be_replaced_with_jump_static_window_or_opening(self):
        requested = cast([intent('vault_window', 'window', '先走路，再跑步翻越窗户')])
        for environment in (None, scene([]), scene(['open_window'])):
            with self.subTest(environment=environment), self.assertRaisesRegex(ValueError, '场景计划|匹配动作|数量不足'):
                validate_intent_coverage(requested, environment,
                                        {'animations': ['mixamo_fbx/basic/Walk.fbx',
                                                        'mixamo_fbx/basic/Run.fbx',
                                                        'mixamo_fbx/basic/Jump.fbx']})
        with self.assertRaisesRegex(ValueError, '普通Jump不能代替翻窗'):
            validate_intent_coverage(requested, scene(['vault_window']),
                                    {'animations': ['mixamo_fbx/basic/Jump.fbx']})

    def test_vault_has_a_dedicated_event_and_accepts_walk_run_body_sources(self):
        requested = cast([intent('vault_window', 'window', '先走后跑，双手撑窗并翻至另一侧')])
        result = validate_intent_coverage(requested, scene(['vault_window']), {
            'animations': ['mixamo_fbx/basic/Idle.fbx', 'mixamo_fbx/basic/Walk.fbx',
                           'mixamo_fbx/basic/Run.fbx'],
        })
        self.assertTrue(result[0]['supported'])

    def test_vault_cannot_be_mixed_with_explicit_open_operation(self):
        requested = cast([intent(), intent('vault_window', 'window', '之后翻窗')])
        with self.assertRaisesRegex(ValueError, '唯一物品交互'):
            validate_intent_coverage(requested, scene(['open_window', 'vault_window']))

    def test_missing_objects_actions_and_scene_fail(self):
        cases = [(None, '场景计划'), ({'objects': [], 'interactions': []}, 'window对象'),
                 (scene([]), '数量不足'), (scene(['close_window']), '匹配动作')]
        for environment, message in cases:
            with self.subTest(environment=environment), self.assertRaisesRegex(ValueError, message):
                validate_intent_coverage(cast(), environment)

    def test_repeated_intents_require_matching_operation_count(self):
        requested = cast([intent('touch_table', 'table', '先扶桌'),
                          intent('touch_table', 'table', '随后再次扶桌')])
        environment = {'objects': [{'id': 'table_1', 'type': 'table'}],
                       'interactions': [{'object': 'table_1', 'action': 'touch_table'}]}
        with self.assertRaisesRegex(ValueError, '数量不足'):
            validate_intent_coverage(requested, environment)
        environment['interactions'].append(copy.deepcopy(environment['interactions'][0]))
        self.assertEqual(len(validate_intent_coverage(requested, environment)), 2)

    def test_undeclared_operations_and_extra_count_fail(self):
        for requested, environment in [(cast([]), scene()), (cast(), scene(['open_window', 'open_window']))]:
            with self.subTest(requested=requested), self.assertRaisesRegex(ValueError, '没有声明'):
                validate_intent_coverage(requested, environment)

    def test_order_must_match_and_unreferenced_static_objects_are_allowed(self):
        requested = cast([intent(), intent('close_window', 'window', '随后关窗')])
        with self.assertRaisesRegex(ValueError, '顺序'):
            validate_intent_coverage(requested, scene(['close_window', 'open_window']))
        environment = scene(['open_window', 'close_window'])
        environment['objects'].append({'id': 'chair_1', 'type': 'chair'})
        self.assertEqual(len(validate_intent_coverage(requested, environment)), 2)

    def test_flag_cannot_hide_declared_intent_or_invent_missing_ledger(self):
        for requested in (cast(), cast([])):
            requested['requires_interaction'] = not requested['requires_interaction']
            with self.assertRaisesRegex(ValueError, 'object_intents'):
                validate_intent_coverage(requested, scene([]))
        requested = cast(); requested['requires_scene'] = False
        with self.assertRaisesRegex(ValueError, 'requires_scene'):
            validate_intent_coverage(requested, scene())

    def test_run_jump_mix_is_not_supported_by_existing_prop_executor(self):
        for asset in ('mixamo_fbx/basic/Run.fbx', 'mixamo_fbx/basic/Jump.fbx'):
            with self.subTest(asset=asset), self.assertRaisesRegex(ValueError, '混编尚未接入'):
                validate_intent_coverage(cast(), scene(), {'animations': [asset]})
        self.assertTrue(validate_intent_coverage(cast(), scene(),
                        {'animations': ['mixamo_fbx/basic/Idle.fbx', 'mixamo_fbx/basic/Walk.fbx']}))

    def test_dual_operations_and_invalid_targets_fail(self):
        requested = cast(); requested['actor_count'] = 2
        with self.assertRaisesRegex(ValueError, '仅支持单人'):
            validate_intent_coverage(requested, scene())
        environment = scene(); environment['interactions'][0]['object'] = 'missing'
        with self.assertRaisesRegex(ValueError, '不存在'):
            validate_intent_coverage(cast(), environment)


if __name__ == '__main__':
    unittest.main()
