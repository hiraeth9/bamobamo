"""The free director receives the request and execution feedback without gates."""
import copy
import ast
import json
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from agent_director import director_prompt
from camera_plan import camera_capabilities, validate_camera


class AgentDirectorTests(unittest.TestCase):
    def test_public_camera_check_forwards_contract_and_query_options(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/preload_helpers.py'
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'wm_agent_camera_check')
        calls = []
        namespace = {'_agent_component': lambda *args, **kwargs: calls.append((args, kwargs)) or {'passed': True}}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        requirements = {'version': 1, 'shots': [{'camera': 'real_camera'}]}
        namespace['wm_agent_camera_check']('job', requirements, 20, 40, True)
        self.assertEqual(calls, [(('agent_camera_quality', 'check_camera_visibility', 'job'),
                                 {'requirements': requirements, 'frame_start': 20,
                                  'frame_end': 40, 'detail': True})])
        registered = [node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Attribute) and target.attr == 'wm_agent_camera_check'
                              for target in node.targets)]
        self.assertEqual(len(registered), 1)

    def test_camera_failures_preserve_actions_and_prompt_specific_camera_scope(self):
        request = '机器人搬椅堵门20秒，固定斜侧略微俯视，全身、整把椅子和门可见。'
        prompt = director_prompt(request, 'chair_camera')
        self.assertIn(request, prompt)
        self.assertIn('wm_agent_camera_check', prompt)
        self.assertIn('focus_objects', prompt)
        self.assertIn('focus_groups', prompt)
        self.assertIn('组内正常互挡', prompt)
        self.assertIn('最多12代表帧', prompt)
        self.assertIn('backend_full_frame_visibility_required=True', prompt)
        self.assertIn('framing=\'regions\'', prompt)
        self.assertIn('expected_position', prompt)
        self.assertIn('expected_elevation_deg', prompt)
        self.assertIn('不盲强制固定全景', prompt)
        self.assertIn('不得为取景修改已正确的动作/物品轨迹', prompt)
        self.assertIn('隐藏/删除墙体或擅自透明化', prompt)
        self.assertIn('camera_visibility_version=1', prompt)

    def test_runtime_reference_info_does_not_depend_on_test_import_paths(self):
        script = Path(__file__).resolve().parents[1] / 'scripts'
        code = ('import sys,json;sys.path.insert(0,' + repr(str(script)) + ');'
                'from agent_reference import reference_info;'
                'from duel_plan import validate_plan;'
                'info=reference_info();'
                'print(json.dumps({"assets":len(info["animation_assets"]),'
                '"frames":validate_plan(info["duel_example"],"runtime_reference")["frame_count"]}))')
        import os
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True,
                                text=True, env=environment, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertGreater(output['assets'], 0)
        self.assertEqual(output['frames'], 120)

    def test_complete_long_natural_request_is_preserved_without_legacy_routing(self):
        request = ('三个不同的人一起走向椅子，依次坐下再起身，生成45秒。\n'
                   + '人物和椅子都必须完整可见；' * 450
                   + '相机右前方60度、俯视20度，最后第三个人挥手。')
        prompt = director_prompt(request, 'free_three_people')
        self.assertIn('<原始用户提示词>\n' + request + '\n</原始用户提示词>', prompt)
        self.assertIn('generation_contract_version=3', prompt)
        self.assertIn('execution_strategy=hermes_mcp', prompt)
        self.assertIn('bpy.wm_agent_scene("free_three_people", analysis=analysis)', prompt)
        self.assertNotIn('bpy.wm_scene_cast(', prompt)
        self.assertNotIn('bpy.wm_single_plan(', prompt)

    def test_prompt_is_pure_and_job_identifier_is_a_python_literal(self):
        job = 'job"quoted\\identifier'
        with patch('builtins.open', side_effect=AssertionError('prompt must not read files')):
            first = director_prompt('人物“甲”先转身\n再挥手。', job)
            second = director_prompt('人物“甲”先转身\n再挥手。', job)
        self.assertEqual(first, second)
        encoded = json.dumps(job, ensure_ascii=False)
        self.assertIn('bpy.wm_agent_analyze(' + encoded + ', analysis)', first)
        self.assertIn('bpy.wm_agent_scene(' + encoded + ', analysis=analysis)', first)

    def test_actual_error_feedback_is_retained_without_mutating_it(self):
        feedback = {'stage': 'scene_probe', 'error': '相机位于地面下',
                    'requested': {'elevation_deg': 20}, 'actual': {'elevation_deg': -20},
                    'warnings': ['第三人还没有坐下'], 'passed': False}
        previous = copy.deepcopy(feedback)
        prompt = director_prompt('三个人先坐下，再站起，俯视20度。', 'repair_job', feedback)
        serialized = prompt.split('<实际执行反馈>\n', 1)[1].split('\n</实际执行反馈>', 1)[0]
        self.assertEqual(json.loads(serialized), feedback)
        self.assertEqual(feedback, previous)
        self.assertIn('bpy.wm_agent_scene("repair_job", analysis=analysis)', prompt)
        self.assertNotIn('<实际执行反馈>', director_prompt('站稳。', 'new_job'))

    def test_repair_keeps_the_actual_scene_instead_of_default_clear(self):
        request = '三个人站在桌旁，轮流坐下起身，视频5秒。'
        first = director_prompt(request, 'three_people')
        repair = director_prompt(request, 'three_people', {'scene_path': '/existing/scene.blend',
                                                          'error': '只有骨架，没有可渲染人物'})
        empty_feedback = director_prompt(request, 'three_people', {})
        self.assertIn('<首次构建入口>', first)
        self.assertIn('bpy.wm_clear()', first)
        for prompt in [repair, empty_feedback]:
            self.assertIn('<反馈修复入口>', prompt)
            self.assertNotIn('<首次构建入口>', prompt)
            self.assertNotIn('bpy.wm_clear()', prompt)
            self.assertIn('bpy.wm_agent_load("three_people")', prompt)
            self.assertIn(request, prompt)
            self.assertIn('bpy.wm_agent_scene("three_people", analysis=analysis)', prompt)

    def test_render_grounding_requires_visible_geometry_and_actual_render(self):
        prompt = director_prompt('三个人围着桌子动作，生成5秒白模视频。', 'visible_people')
        self.assertIn('ARMATURE骨架、骨骼显示和custom_shape仅用于视口', prompt)
        self.assertIn('skin mesh', prompt)
        self.assertIn('evaluated/deformed可见mesh', prompt)
        self.assertIn('实际灯光（如AREA/SUN）及World', prompt)
        self.assertIn('实际渲染低分辨率代表帧', prompt)
        self.assertIn('MCP视口/骨架overlay截图不能代替最终render', prompt)
        self.assertIn('无法观看图像要明确视觉尚未确认', prompt)
        self.assertIn('每段代码必须self-contained', prompt)
        self.assertIn('从bpy.data重新查对象', prompt)

    def test_elevation_signs_remain_distinct_and_capability_explains_high_camera(self):
        high = validate_camera({'elevation_deg': 20})
        low = validate_camera({'elevation_deg': -20})
        self.assertEqual(high['elevation_deg'], 20)
        self.assertEqual(low['elevation_deg'], -20)
        self.assertEqual(validate_camera(high), high)
        self.assertEqual(validate_camera(low), low)
        convention = camera_capabilities()['elevation_convention']
        self.assertIn('+20=高机位向下俯视20度', convention)
        self.assertIn('-20=低机位向上仰视20度', convention)


if __name__ == '__main__':
    unittest.main()
