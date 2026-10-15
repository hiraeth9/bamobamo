"""New camera intent and requirement review must survive without lost fields."""
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
from scene_plan import validate_scene, write_scene
from semantic_review import _camera_facts, load_review, semantic_review_prompt, write_review
from single_plan import validate_single, write_single
from duel_cases import fixture


PROMPT = '一个人先走路，再跑步翻越窗户，相机从窗户左前方斜看，显示全身；不要配音。'


def ledger():
    return [
        {'id': 'r01', 'category': 'camera', 'source_text': '相机从窗户左前方斜看', 'description': '物品相对左前斜视'},
        {'id': 'r02', 'category': 'audio', 'source_text': '不要配音', 'description': '不要配音'},
    ]


def people():
    return {'actors': [{'id': 'A', 'name': '一个人'}], 'requires_scene': True,
            'requires_interaction': False, 'requires_locomotion': False,
            'object_intents': [], 'requirements': ledger()}


def body():
    return {'version': 1, 'animations': ['mixamo_fbx/basic/Idle.fbx'], 'duration_seconds': 6}


def scene():
    return {'version': 1, 'duration_seconds': 6, 'objects': [{'id': 'window_1', 'type': 'window'}], 'interactions': []}


def placement():
    return {'version': 1, 'reference': 'object', 'target': 'window_1', 'azimuth_deg': -45,
            'elevation_deg': 20, 'projection': 'perspective', 'framing': 'full_body',
            'fov_deg': 60, 'occlusion_policy': 'strict'}


def review():
    return {'version': 1, 'requirements': [
        {'description': '窗户左前斜视', 'status': 'covered', 'evidence': '物品参考左前45度固定相机',
         'requirement_ids': ['r01'], 'plan_evidence': [
             {'document': 'single_plan.json', 'path': 'camera.azimuth_deg', 'value': -45}]},
        {'description': '不配音', 'status': 'unsupported', 'evidence': '当前文本计划没有音频执行字段',
         'requirement_ids': ['r02'], 'plan_evidence': []},
    ], 'summary': '镜头计划已保留，音频能力未认证。'}


class GenerationContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.jobs = patch.object(duel_plan, 'JOBS', Path(self.temporary.name))
        self.jobs.start()
        self.folder = duel_plan.job_directory('contract_test')
        duel_plan.atomic_json(self.folder / 'request.json', {'prompt': PROMPT, 'mode': 'auto',
                                                           'generation_contract_version': 2})

    def tearDown(self):
        self.jobs.stop()
        self.temporary.cleanup()

    def saved_plans(self):
        write_cast('contract_test', people())
        write_scene('contract_test', scene())
        return write_single('contract_test', {**body(), 'camera': placement()})

    def test_camera_survives_normalization_save_and_reload(self):
        plan = self.saved_plans()
        self.assertEqual(plan['camera'], placement())
        self.assertEqual(validate_single(plan, 'contract_test'), plan)
        self.assertEqual(json.loads((self.folder / 'single_plan.json').read_text())['camera'], placement())
        self.assertEqual(plan['camera_policy'], 'fixed')
        self.assertEqual(plan['pitch_deg'], 0)

    def test_existing_single_white_model_style_is_canonical_and_roundtrips(self):
        legacy = body()
        plan = validate_single(legacy, 'contract_test')
        self.assertEqual(plan['render_style'], 'white_model')
        self.assertNotIn('render_style', legacy)
        self.assertEqual(validate_single(plan, 'contract_test'), plan)
        self.assertEqual(validate_single({**legacy, 'render_style': 'white_model'}, 'contract_test'), plan)

    def test_existing_duel_white_model_style_is_canonical_and_roundtrips(self):
        legacy = fixture()
        plan = duel_plan.validate_plan(legacy, 'contract_test')
        self.assertEqual(plan['render_style'], 'white_model')
        self.assertNotIn('render_style', legacy)
        self.assertEqual(duel_plan.validate_plan(plan, 'contract_test'), plan)

    def test_other_render_styles_are_rejected_rather_than_silently_rendered_white(self):
        for style in (None, True, '', 'photorealistic', 'gray_rgb_0.8', {'rgb': [.8, .8, .8]}):
            with self.subTest(style=style), self.assertRaisesRegex(ValueError, '只支持white_model'):
                validate_single({**body(), 'render_style': style}, 'contract_test')
            with self.subTest(style=style), self.assertRaisesRegex(ValueError, '只支持white_model'):
                duel_plan.validate_plan({**fixture(), 'render_style': style}, 'contract_test')

    def test_v2_white_model_requirement_is_grounded_to_both_real_saved_plans(self):
        for kind in ('single', 'duel'):
            job = 'style_' + kind
            folder = duel_plan.job_directory(job)
            duel_plan.atomic_json(folder / 'request.json', {'prompt': '生成白模视频', 'mode': 'auto',
                                                           'generation_contract_version': 2})
            cast = {'actors': [{'id': ident, 'name': '人物' + ident} for ident in ('A', 'B')[:1 if kind == 'single' else 2]],
                    'requires_scene': False, 'requires_interaction': False, 'requires_locomotion': False,
                    'object_intents': [], 'requirements': [{'id': 'r01', 'category': 'style',
                                                           'source_text': '白模视频', 'description': '现有白模渲染方式'}]}
            write_cast(job, cast)
            if kind == 'single':
                write_single(job, body())
            else:
                duel_plan.write_plan(job, fixture())
            document = 'single_plan.json' if kind == 'single' else 'plan.json'
            raw = {'version': 1, 'requirements': [{'description': '白模视频', 'status': 'covered',
                                                  'evidence': '真实保存render_style标明现有clay渲染',
                                                  'requirement_ids': ['r01'], 'plan_evidence': [
                                                      {'document': document, 'path': 'render_style', 'value': 'white_model'}]}],
                   'summary': '仅白模方式有执行证据，不认证RGB/衣服/背景或音频。'}
            self.assertTrue(write_review(job, raw)['passed'])
            self.assertEqual(load_review(job)['requirements'][0]['plan_evidence'][0]['value'], 'white_model')

    def test_new_metadata_cannot_be_invented_as_evidence_for_an_immutable_old_plan(self):
        duel_plan.atomic_json(self.folder / 'request.json', {'prompt': '生成白模视频', 'mode': 'auto',
                                                           'generation_contract_version': 2})
        cast = people()
        cast.update(requires_scene=False, requirements=[{'id': 'r01', 'category': 'style',
                                                        'source_text': '白模视频', 'description': '白模渲染'}])
        write_cast('contract_test', cast)
        duel_plan.atomic_json(self.folder / 'single_plan.json', body())
        before = (self.folder / 'single_plan.json').read_bytes()
        raw = {'version': 1, 'requirements': [{'description': '白模视频', 'status': 'covered',
                                              'evidence': '错误地把新默认当旧保存字段', 'requirement_ids': ['r01'],
                                              'plan_evidence': [{'document': 'single_plan.json', 'path': 'render_style', 'value': 'white_model'}]}],
               'summary': '待核对旧字段'}
        with self.assertRaisesRegex(ValueError, '不存在.*render_style'):
            write_review('contract_test', raw)
        self.assertEqual((self.folder / 'single_plan.json').read_bytes(), before)
        raw['requirements'][0].update(status='missing', evidence='现有白模能力已支持，但旧保存计划缺render_style证据', plan_evidence=[])
        self.assertFalse(write_review('contract_test', raw)['passed'])

    def test_position_mode_does_not_gain_conflicting_direction_defaults(self):
        camera = {'version': 1, 'position': [4, -4, 3], 'look_at': [0, 0, 1], 'projection': 'perspective'}
        plan = validate_single({**body(), 'camera': camera}, 'contract_test')
        self.assertNotIn('reference', plan['camera'])
        self.assertNotIn('azimuth_deg', plan['camera'])
        self.assertEqual(validate_single(plan, 'contract_test'), plan)

    def test_ordinary_legacy_orbit_remains_available_without_new_placement(self):
        plan = validate_single({**body(), 'camera_policy': 'orbit', 'pitch_deg': 20, 'fov': 60}, 'contract_test')
        self.assertEqual(plan['camera_policy'], 'orbit')
        self.assertNotIn('camera', plan)

    def test_new_camera_rejects_nondefault_second_legacy_placement(self):
        for key, value in [('camera_policy', 'orbit'), ('pitch_deg', 20), ('distance_ratio', 2), ('fov', 60)]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '不能.*混用'):
                validate_single({**body(), 'camera': {'azimuth_deg': -45}, key: value}, 'contract_test')
        plan = validate_single({**body(), 'camera': {'azimuth_deg': -45},
                                'camera_policy': 'fixed', 'pitch_deg': 0, 'distance_ratio': 3.5, 'fov': 50}, 'contract_test')
        self.assertEqual(plan['camera']['azimuth_deg'], -45)

    def test_unsupported_or_misplaced_camera_fields_are_rejected(self):
        for extra in [{'azimuth_deg': -45}, {'camera': {'orbit_seconds': 6}}, {'camera': {'framing': 'closeup'}},
                      {'camera': {'position': [4, -4, 3], 'azimuth_deg': -45}}, {'fps': 24}, {'frame_count': 999}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_single({**body(), **extra}, 'contract_test')
        with self.assertRaisesRegex(ValueError, '不能写入scene'):
            validate_scene({**scene(), 'camera': {'azimuth_deg': -45}}, 'contract_test')

    def test_object_and_actor_reference_require_real_target(self):
        for camera in [placement(), {'reference': 'actor', 'target': 'B'}, {'reference': 'world', 'target': 'A'}]:
            with self.subTest(camera=camera), self.assertRaises(ValueError):
                validate_single({**body(), 'camera': camera}, 'contract_test')
        self.saved_plans()
        with self.assertRaisesRegex(ValueError, '真实物品'):
            validate_single({**body(), 'camera': {**placement(), 'target': 'ghost'}}, 'contract_test')

    def test_locomotion_accepts_new_camera_without_legacy_fixed_lens_assumption(self):
        raw = {**body(), 'duration_seconds': 6, 'animations': ['mixamo_fbx/basic/Walk.fbx'],
               'camera': {'reference': 'actor', 'target': 'A', 'azimuth_deg': -35, 'elevation_deg': 25},
               'locomotion': {'version': 1, 'start_position': [0, 1], 'start_yaw_deg': 0,
                              'segments': [{'start': 0, 'end': 5, 'gait': 'walk', 'waypoints': [[0, 1], [0, -1]]},
                                           {'start': 5, 'end': 6, 'gait': 'idle'}]}}
        self.assertEqual(validate_single(raw, 'contract_test')['camera']['elevation_deg'], 25)

    def test_v2_cast_requires_ledger_before_saving(self):
        raw = people(); raw.pop('requirements')
        with self.assertRaisesRegex(ValueError, '完整requirements'):
            write_cast('contract_test', raw)
        self.assertFalse((self.folder / 'cast.json').exists())

    def test_ledger_preserves_unsupported_requests_and_verbatim_source(self):
        cast = write_cast('contract_test', people())
        self.assertEqual(cast['requirements'], ledger())
        self.assertIn('audio', [item['category'] for item in cast['requirements']])
        self.assertEqual(validate_cast(cast, 'contract_test'), cast)

    def test_invalid_duplicate_rewritten_or_extra_ledger_fields_are_rejected(self):
        for mutation in [lambda rows: rows[0].update(source_text='相机自动旋转'),
                         lambda rows: rows[1].update(id='r01'),
                         lambda rows: rows[0].update(category='unknown'),
                         lambda rows: rows[0].update(supported=True)]:
            raw = people(); mutation(raw['requirements'])
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                write_cast('contract_test', raw)

    def test_legacy_cast_without_ledger_is_still_readable(self):
        duel_plan.atomic_json(self.folder / 'request.json', {'prompt': PROMPT, 'mode': 'auto'})
        raw = people(); raw.pop('requirements')
        self.assertNotIn('requirements', write_cast('contract_test', raw))

    def test_v2_exact_evidence_and_unsupported_requirement_are_retained(self):
        self.saved_plans()
        result = write_review('contract_test', review())
        self.assertFalse(result['passed'])
        self.assertEqual(load_review('contract_test'), result)
        self.assertEqual(result['requirements'][0]['plan_evidence'][0]['value'], -45)

    def test_review_cannot_drop_unknown_or_unbound_ledger_ids(self):
        self.saved_plans()
        for mutation in [lambda raw: raw['requirements'][0].update(requirement_ids=['ghost']),
                         lambda raw: raw['requirements'].pop(),
                         lambda raw: raw['requirements'][0].pop('requirement_ids')]:
            raw = review(); mutation(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                write_review('contract_test', raw)
        self.assertFalse((self.folder / 'semantic_review.json').exists())

    def test_review_cannot_use_prose_or_wrong_actual_values_as_covered_evidence(self):
        self.saved_plans()
        invalid = [[], [{'document': 'single_plan.json', 'path': 'camera.azimuth_deg', 'value': 0}],
                   [{'document': 'single_plan.json', 'path': 'camera.missing', 'value': -45}],
                   [{'document': 'request.json', 'path': 'prompt', 'value': PROMPT}],
                   [{'document': 'cast.json', 'path': 'requirements[0].description', 'value': '物品相对左前斜视'}],
                   [{'document': 'cast.json', 'path': 'actors[0].name', 'value': '一个人'}],
                   [{'document': 'plan.json', 'path': 'camera.azimuth_deg', 'value': -45}]]
        for evidence in invalid:
            raw = review(); raw['requirements'][0]['plan_evidence'] = evidence
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, '第1项'):
                write_review('contract_test', raw)

    def test_boolean_evidence_cannot_impersonate_numeric_json_field(self):
        self.saved_plans()
        raw = review()
        raw['requirements'][0]['plan_evidence'] = [{'document': 'cast.json', 'path': 'actor_count', 'value': True}]
        with self.assertRaisesRegex(ValueError, '第1项.*不一致.*cast.json:actor_count'):
            write_review('contract_test', raw)

    def test_actual_actor_count_can_ground_character_count_requirement(self):
        cast = people()
        cast['requirements'][0] = {'id': 'r01', 'category': 'character', 'source_text': '一个人',
                                   'description': '画面一名人物'}
        write_cast('contract_test', cast)
        write_scene('contract_test', scene())
        write_single('contract_test', {**body(), 'camera': placement()})
        raw = review()
        raw['requirements'][0].update(description='画面一名人物', evidence='规范人物列表共一名A',
                                      plan_evidence=[{'document': 'cast.json', 'path': 'actor_count', 'value': 1}])
        self.assertFalse(write_review('contract_test', raw)['passed'])

    def test_legacy_window_camera_facts_do_not_invent_angled_placement(self):
        facts = _camera_facts({'single_plan.json': {**body(), 'camera_policy': 'fixed'},
                               'scene_plan.json': {'objects': [{'id': 'window_1', 'yaw_deg': 0}],
                                                   'interactions': [{'action': 'vault_window', 'object': 'window_1'}]}})
        self.assertFalse(facts['new_placement_submitted'])
        self.assertEqual(facts['legacy_azimuth_deg'], 0)
        self.assertIn('沿窗面固定侧视', facts['legacy_description'])
        self.assertIn('指定方位', facts['explicit_placement_note'])

    def test_nested_scene_field_and_list_value_are_checked(self):
        self.saved_plans()
        raw = review()
        raw['requirements'][0]['plan_evidence'].append(
            {'document': 'scene_plan.json', 'path': 'objects[0].size', 'value': [1.1, .12, 1.2]})
        self.assertFalse(write_review('contract_test', raw)['passed'])

    def test_v2_review_supports_detailed_requirements_beyond_old_32_limit(self):
        self.saved_plans()
        raw = review()
        raw['requirements'].extend([{'description': '独立补充未实现要求', 'status': 'unsupported',
                                     'evidence': '没有对应执行能力', 'requirement_ids': [], 'plan_evidence': []}
                                    for _ in range(40)])
        self.assertEqual(len(write_review('contract_test', raw)['requirements']), 42)

    def test_prompt_exposes_true_legacy_window_side_and_new_grounding_rules(self):
        self.saved_plans()
        prompt = semantic_review_prompt('contract_test')
        self.assertIn('requirement_ids', prompt)
        self.assertIn('plan_evidence', prompt)
        self.assertIn('翻窗为沿窗面固定侧视', prompt)
        self.assertIn('旧fixed/0/3.5/50不承载指定水平角', prompt)
        self.assertIn('transparent_architecture', prompt)
        self.assertIn('"world_azimuth_deg": -45', prompt)
        self.assertIn('只进行一次独立语义判断', prompt)
        self.assertIn('允许最多两次仅纠正提交结构和真实字段引用后重交', prompt)
        self.assertIn('禁止覆盖失败复核', prompt)
        self.assertIn('明确人物数量与身份必须独立列出', prompt)
        self.assertIn('白模风格特别核对', prompt)


if __name__ == '__main__':
    unittest.main()
