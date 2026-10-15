"""The general director delivers its scene without old semantic gates."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import duel_plan
import duel_worker


class HermesWorkerTests(unittest.TestCase):
    def test_director_has_separate_longer_configurable_budget(self):
        with patch.dict(duel_worker.os.environ, {}, clear=True):
            self.assertEqual(duel_worker.director_timeout_seconds(), 3600)
        with patch.dict(duel_worker.os.environ,
                        {'BAMO_DIRECTOR_TIMEOUT_SECONDS': '2400.5'}, clear=True):
            self.assertEqual(duel_worker.director_timeout_seconds(), 2400.5)

    def test_invalid_director_deadlines_cannot_disable_process_timeout(self):
        for value in ('0', '-1', 'nan', 'inf', '', 'invalid'):
            with self.subTest(value=value), patch.dict(duel_worker.os.environ,
                    {'BAMO_DIRECTOR_TIMEOUT_SECONDS': value}, clear=True):
                with self.assertRaisesRegex(ValueError, '有限正秒数'):
                    duel_worker.director_timeout_seconds()

    def test_director_budget_does_not_extend_probe_or_render(self):
        budgets = []
        def execute(folder, command, log_name, timeout):
            budgets.append((log_name, timeout))
            return self.execute(folder, command, log_name, timeout)
        with patch.dict(duel_worker.os.environ,
                        {'BAMO_DIRECTOR_TIMEOUT_SECONDS': '2400'}, clear=True):
            self.assertTrue(self.run_with(execute)['ok'])
        self.assertIn(('hermes.log', 2400), budgets)
        self.assertIn(('agent_probe_0.log', 180), budgets)
        self.assertTrue(all(value <= 900 for name, value in budgets
                            if name != 'hermes.log'))

    def test_software_renderer_threads_follow_cpu_quota_and_keep_explicit_override(self):
        with patch.object(Path, 'read_text', return_value='100000 100000'), \
                patch.dict(duel_worker.os.environ, {}, clear=True):
            self.assertEqual(duel_worker.child_environment()['LP_NUM_THREADS'], '1')
        with patch.object(Path, 'read_text', return_value='100000 100000'), \
                patch.dict(duel_worker.os.environ, {'LP_NUM_THREADS': '3'}, clear=True):
            self.assertEqual(duel_worker.child_environment()['LP_NUM_THREADS'], '3')
        with patch.object(Path, 'read_text', return_value='max 100000'), \
                patch.dict(duel_worker.os.environ, {}, clear=True):
            self.assertNotIn('LP_NUM_THREADS', duel_worker.child_environment())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.jobs = self.project / 'output/jobs'
        self.jobs.mkdir(parents=True)
        self.patches = [patch.object(duel_plan, 'JOBS', self.jobs),
                        patch.object(duel_worker, 'JOBS', self.jobs),
                        patch.object(duel_worker, 'PROJECT', self.project)]
        for context in self.patches:
            context.start()
        self.job = 'free_director'
        self.folder = duel_plan.job_directory(self.job)
        self.request = {'prompt': '三个人分别坐下、举手、转身，30秒，全身入镜。', 'mode': 'auto',
                        'generation_contract_version': 3, 'execution_strategy': 'hermes_mcp'}
        duel_plan.atomic_json(self.folder / 'request.json', self.request)
        # Deliberately conflicting old files cannot veto the general director.
        duel_plan.atomic_json(self.folder / 'cast.json', {'supported': False, 'actor_count': 3})
        duel_plan.atomic_json(self.folder / 'semantic_review.json', {'passed': False})
        self.commands = []
        self.submission_number = 0

    def tearDown(self):
        for context in reversed(self.patches):
            context.stop()
        self.temp.cleanup()

    def save_scene(self):
        self.submission_number += 1
        (self.folder / 'scene.blend').write_bytes(b'captured MCP scene')
        duel_plan.atomic_json(self.folder / 'agent_analysis.json', {'analysis': {
            'actors': ['A', 'B', 'C'], 'warnings': ['坐下是程序化近似'],
            'unfulfilled': [{'detail': '尚未加入音效'}]}})
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        duel_plan.atomic_json(self.folder / 'agent_scene.json', {
            'submission_id': str(self.submission_number),
            'frame_start': 11, 'frame_end': 58, 'frame_count': 48,
            'fps': 24 / 1.001, 'fps_nominal': 24, 'fps_base': 1.001,
            'actor_count': 3, 'warnings': [],
            'scene_sha256': sha(self.folder / 'scene.blend'),
            'request_sha256': sha(self.folder / 'request.json'),
            'analysis_sha256': sha(self.folder / 'agent_analysis.json')})

    def execute(self, folder, command, log_name, timeout):
        self.commands.append((command, log_name))
        if '-z' in command:
            self.assertIn(self.request['prompt'], command[-1])
            self.save_scene()
        elif log_name.startswith('agent_probe'):
            path = Path(command[command.index('--report') + 1])
            duel_plan.atomic_json(path, {'passed': True, 'errors': []})
        else:
            self.assertIn('--preserve-scene-fps', command)
            path = Path(command[command.index('--output') + 1])
            path.write_bytes(b'verified by a separate media verifier')

    def run_with(self, execute):
        with patch.object(duel_worker, 'run_logged', side_effect=execute), \
                patch.object(duel_worker, 'verify_video', return_value={'nb_read_frames': '48', 'duration': '2.002'}), \
                patch.object(duel_worker, 'validate_cast', side_effect=AssertionError('old cast gate called')), \
                patch.object(duel_worker, 'validate_single', side_effect=AssertionError('old single gate called')), \
                patch.object(duel_worker, 'validate_plan', side_effect=AssertionError('old duel gate called')), \
                patch.object(duel_worker, 'review_planned_request', side_effect=AssertionError('old review gate called')):
            return duel_worker.run_job(self.job)

    def test_three_people_and_old_unsupported_flags_do_not_veto_actual_scene(self):
        result = self.run_with(self.execute)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['actor_count'], 3)
        self.assertTrue(result['media_verified'])
        self.assertNotIn('quality_passed', result)
        self.assertNotIn('semantic_review_passed', result)
        self.assertIn('坐下是程序化近似', result['warnings'])
        self.assertIn('尚未加入音效', ' '.join(result['warnings']))
        self.assertEqual([name for _, name in self.commands],
                         ['hermes.log', 'agent_probe_0.log', 'preview.log', 'rendering.log'])

    def test_scene_reload_error_goes_back_to_hermes_before_rendering(self):
        def execute(folder, command, log_name, timeout):
            if log_name == 'agent_probe_0.log':
                self.commands.append((command, log_name))
                raise RuntimeError('invalid temporary Python driver: bake keyframes')
            if log_name == 'agent_repair_1.log':
                self.assertIn('invalid temporary Python driver', command[-1])
            return self.execute(folder, command, log_name, timeout)
        result = self.run_with(execute)
        self.assertTrue(result['ok'], result)
        names = [name for _, name in self.commands]
        self.assertLess(names.index('agent_repair_1.log'), names.index('preview.log'))
        self.assertTrue((self.folder / 'agent_feedback_0.json').is_file())

    def save_camera_scene(self, motion='original'):
        self.save_scene()
        path = self.folder / 'agent_scene.json'
        manifest = json.loads(path.read_text())
        manifest['camera_visibility_version'] = 1
        if motion is not None:
            manifest['motion_execution'] = {'actors': [
                {'actor_id': actor, 'pose_animation': {'channel_values_sha256': motion + actor},
                 'root_animation': {'channel_values_sha256': 'root' + actor}}
                for actor in ('A', 'B', 'C')]}
        duel_plan.atomic_json(path, manifest)

    def camera_probe(self, command, failed=False, mixed=False):
        visibility = {'passed': not failed, 'scope': 'sampled opaque occlusion and actual projection',
                      'failures': [{'frame': 40, 'target': 'A', 'opaque_objects': ['Wall']}]
                      if failed else []}
        errors = [{'code': 'camera_visibility', 'failures': visibility['failures']}] if failed else []
        if mixed:
            errors.append({'code': 'nonfinite_pose', 'frame': 40})
        duel_plan.atomic_json(Path(command[command.index('--report') + 1]),
                             {'passed': not errors, 'errors': errors, 'camera_visibility': visibility})
        if errors:
            raise RuntimeError('agent_scene_probe exited with status 1')

    def test_actual_camera_failure_repairs_before_any_video_and_preserves_motion(self):
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            if '-z' in command:
                self.assertIn(self.request['prompt'], command[-1])
                if log_name == 'agent_repair_1.log':
                    feedback = json.loads(command[-1].split('<实际执行反馈>\n', 1)[1].split('\n</实际执行反馈>', 1)[0])
                    self.assertEqual(feedback['repair_scope'], 'camera_only')
                    self.assertEqual(feedback['preserve_motion_signature']['status'], 'available')
                    self.assertEqual(feedback['camera_visibility']['failures'][0]['opaque_objects'], ['Wall'])
                    self.assertIn('不得删动作、删墙或擅自透明化', feedback['camera_instruction'])
                self.save_camera_scene()
            elif log_name.startswith('agent_probe'):
                self.camera_probe(command, failed=log_name == 'agent_probe_0.log')
            else:
                Path(command[command.index('--output') + 1]).write_bytes(b'verified video')
        result = self.run_with(execute)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['camera_visibility_verified'])
        self.assertEqual(result['camera_only_actor_motion_preservation']['status'], 'actor_channels_verified')
        names = [name for _, name in self.commands]
        self.assertLess(names.index('agent_repair_1.log'), names.index('preview.log'))
        self.assertEqual(json.loads((self.folder / 'request.json').read_text()), self.request)

    def test_camera_only_repair_that_drops_motion_cannot_publish_video(self):
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            if '-z' in command:
                self.save_camera_scene('original' if log_name == 'hermes.log' else 'changed')
            elif log_name == 'agent_probe_0.log':
                self.camera_probe(command, failed=True)
            else:
                self.fail('Changed actor channels must be stopped before another probe or any rendering')
        result = self.run_with(execute)
        self.assertFalse(result['ok'])
        self.assertIn('仅相机修复改变了原人物', result['error'])
        self.assertNotIn('video', result)
        self.assertFalse((self.project / 'output' / f'agent_{self.job}.mp4').exists())

    def test_camera_only_repair_that_changes_duration_cannot_publish_video(self):
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            if '-z' in command:
                self.save_camera_scene()
                if log_name != 'hermes.log':
                    path = self.folder / 'agent_scene.json'
                    manifest = json.loads(path.read_text())
                    manifest.update(frame_end=34, frame_count=24)
                    duel_plan.atomic_json(path, manifest)
            elif log_name == 'agent_probe_0.log':
                self.camera_probe(command, failed=True)
            else:
                self.fail('Changed duration must stop before probe or rendering')
        result = self.run_with(execute)
        self.assertFalse(result['ok'])
        self.assertIn('仅相机修复改变了原人物', result['error'])
        self.assertFalse((self.project / 'output' / f'agent_{self.job}.mp4').exists())

    def test_seeded_camera_only_repair_protects_existing_motion_before_first_probe(self):
        self.save_camera_scene()
        duel_plan.atomic_json(self.folder / 'agent_seed_feedback.json', {
            'repair_scope': 'camera_only', 'actual_errors': ['Wall blocks actor at frame 40']})
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            if '-z' in command:
                self.save_camera_scene('changed')
            else:
                self.fail('Seeded camera-only repair must preserve the original animation before probing')
        result = self.run_with(execute)
        self.assertFalse(result['ok'])
        self.assertIn('仅相机修复改变了原人物', result['error'])
        self.assertFalse((self.project / 'output' / f'agent_{self.job}.mp4').exists())

    def test_repair_cannot_change_the_original_prompt_even_after_resigning_manifest(self):
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            if '-z' in command:
                if log_name != 'hermes.log':
                    duel_plan.atomic_json(self.folder / 'request.json',
                                          dict(self.request, prompt='站着不动，删掉搬椅子'))
                self.save_camera_scene()
            elif log_name == 'agent_probe_0.log':
                self.camera_probe(command, failed=True)
            else:
                self.fail('Changed original request must stop before probe or rendering')
        result = self.run_with(execute)
        self.assertFalse(result['ok'])
        self.assertIn('改变了原始request/prompt', result['error'])
        self.assertFalse((self.project / 'output' / f'agent_{self.job}.mp4').exists())

    def test_probe_process_error_does_not_reuse_a_stale_camera_report(self):
        duel_plan.atomic_json(self.folder / 'agent_probe_0.json', {
            'passed': False, 'errors': [{'code': 'camera_visibility'}],
            'camera_visibility': {'passed': False, 'failures': [{'frame': -999}]}})
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            if '-z' in command:
                if log_name == 'agent_repair_1.log':
                    self.assertNotIn('"repair_scope": "camera_only"', command[-1])
                    self.assertNotIn('-999', command[-1])
                self.save_camera_scene()
            elif log_name == 'agent_probe_0.log':
                self.assertFalse((self.folder / 'agent_probe_0.json').exists())
                archived = list((self.folder / 'attempts/probes').glob('*_agent_probe_0.json'))
                self.assertEqual(len(archived), 1)
                self.assertEqual(json.loads(archived[0].read_text())['camera_visibility']['failures'],
                                 [{'frame': -999}])
                raise RuntimeError('probe failed before writing a report')
            elif log_name.startswith('agent_probe'):
                self.camera_probe(command)
            else:
                Path(command[command.index('--output') + 1]).write_bytes(b'verified video')
        self.assertTrue(self.run_with(execute)['ok'])

    def test_old_missing_channel_hashes_are_explicit_unknown_after_camera_repair(self):
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            if '-z' in command:
                if log_name == 'agent_repair_1.log':
                    self.assertIn('"status": "unknown"', command[-1])
                self.save_camera_scene(None)
            elif log_name.startswith('agent_probe'):
                self.camera_probe(command, failed=log_name == 'agent_probe_0.log')
            else:
                Path(command[command.index('--output') + 1]).write_bytes(b'verified video')
        result = self.run_with(execute)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['camera_only_actor_motion_preservation']['status'], 'unknown')

    def test_mixed_camera_and_motion_errors_do_not_freeze_required_motion_repairs(self):
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            if '-z' in command:
                if log_name == 'agent_repair_1.log':
                    self.assertNotIn('"repair_scope": "camera_only"', command[-1])
                self.save_camera_scene('original' if log_name == 'hermes.log' else 'fixed_motion')
            elif log_name.startswith('agent_probe'):
                self.camera_probe(command, failed=log_name == 'agent_probe_0.log',
                                  mixed=log_name == 'agent_probe_0.log')
            else:
                Path(command[command.index('--output') + 1]).write_bytes(b'verified video')
        result = self.run_with(execute)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('camera_only_actor_motion_preservation', result)

    def test_new_camera_protocol_cannot_publish_without_independent_visibility_report(self):
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            if '-z' in command:
                self.save_camera_scene()
            elif log_name.startswith('agent_probe'):
                duel_plan.atomic_json(Path(command[command.index('--report') + 1]), {'passed': True, 'errors': []})
            else:
                self.fail('Missing new-protocol camera report must not reach render')
        result = self.run_with(execute)
        self.assertFalse(result['ok'])
        self.assertIn('缺少通过的实际镜头检查', result['error'])
        self.assertFalse((self.project / 'output' / f'agent_{self.job}.mp4').exists())

    def test_unsubmitted_scene_cannot_publish_an_unrelated_movie(self):
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            self.assertIn('-z', command)
            self.assertEqual(Path(command[1]).name, 'hermes_director_cli.py')
        result = self.run_with(execute)
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.commands), 3)
        self.assertNotIn('video', result)

    def test_repair_without_resubmission_cannot_publish_the_previous_scene(self):
        self.save_scene()
        duel_plan.atomic_json(self.folder / 'agent_seed_feedback.json', {'actual_errors': ['旧视频人物重复']})
        previous_manifest = (self.folder / 'agent_scene.json').read_bytes()
        def execute(folder, command, log_name, timeout):
            self.commands.append((command, log_name))
            self.assertIn('-z', command)
        result = self.run_with(execute)
        self.assertFalse(result['ok'])
        self.assertIn('没有重新提交场景', result['error'])
        self.assertEqual(len(self.commands), 3)
        self.assertEqual((self.folder / 'agent_scene.json').read_bytes(), previous_manifest)
        self.assertFalse((self.project / 'output' / f'agent_{self.job}.mp4').exists())
        self.assertNotIn('video', result)

    def test_render_runtime_error_is_returned_to_hermes_and_scene_is_rebuilt(self):
        def execute(folder, command, log_name, timeout):
            if log_name == 'preview.log' and not (folder / 'agent_feedback_0.json').exists():
                self.commands.append((command, log_name))
                raise RuntimeError('render dependency missing: repair material')
            if log_name == 'agent_repair_1.log':
                self.assertIn('render dependency missing', command[-1])
            return self.execute(folder, command, log_name, timeout)
        result = self.run_with(execute)
        self.assertTrue(result['ok'], result)
        names = [name for _, name in self.commands]
        self.assertEqual(names.count('preview.log'), 2)
        self.assertLess(names.index('agent_repair_1.log'), names.index('rendering.log'))

    def test_hermes_execution_failure_is_returned_for_retry(self):
        def execute(folder, command, log_name, timeout):
            if log_name == 'hermes.log':
                self.commands.append((command, log_name))
                raise RuntimeError('MCP connection interrupted')
            if log_name == 'agent_repair_1.log':
                self.assertIn('MCP connection interrupted', command[-1])
            return self.execute(folder, command, log_name, timeout)
        result = self.run_with(execute)
        self.assertTrue(result['ok'], result)
        self.assertTrue((self.folder / 'agent_feedback_0.json').is_file())

    def test_independent_render_feedback_reaches_director_with_original_prompt(self):
        feedback = {'error': '人物只有骨架，没有可渲染网格，最终帧为黑色',
                    'source_scene': '/previous/scene.blend'}
        duel_plan.atomic_json(self.folder / 'agent_seed_feedback.json', feedback)
        def execute(folder, command, log_name, timeout):
            if '-z' in command:
                self.assertIn(feedback['error'], command[-1])
                self.assertIn(feedback['source_scene'], command[-1])
            return self.execute(folder, command, log_name, timeout)
        result = self.run_with(execute)
        self.assertTrue(result['ok'], result)
        self.assertEqual(json.loads((self.folder / 'request.json').read_text()), self.request)

    def test_repeated_runtime_repairs_keep_initial_intent_feedback_and_prior_errors(self):
        seed = {'actual_errors': ['减速时必须保留脚支撑'],
                'source_scene': '/previous/correct_character.blend'}
        duel_plan.atomic_json(self.folder / 'agent_seed_feedback.json', seed)
        repairs = []

        def execute(folder, command, log_name, timeout):
            if log_name == 'agent_probe_0.log':
                raise RuntimeError('hidden duplicate matrix changed after reload')
            if log_name == 'preview.log' and not (folder / 'agent_feedback_1.json').exists():
                raise RuntimeError('software render dependency failed')
            if log_name.startswith('agent_repair_'):
                serialized = command[-1].split('<实际执行反馈>\n', 1)[1].split('\n</实际执行反馈>', 1)[0]
                repairs.append(json.loads(serialized))
            return self.execute(folder, command, log_name, timeout)

        result = self.run_with(execute)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(repairs), 2)
        for feedback in repairs:
            self.assertEqual(feedback['initial_feedback'], seed)
            self.assertEqual(feedback['scene_path'], str(self.folder / 'scene.blend'))
            self.assertEqual(feedback['analysis_path'], str(self.folder / 'agent_analysis.json'))
        self.assertIn('hidden duplicate matrix', repairs[1]['previous_execution_errors'][0]['error'])
        self.assertIn('software render dependency', repairs[1]['error'])
        self.assertEqual(json.loads((self.folder / 'request.json').read_text()), self.request)

    def test_changed_analysis_during_render_cannot_publish(self):
        def execute(folder, command, log_name, timeout):
            self.execute(folder, command, log_name, timeout)
            if log_name == 'rendering.log':
                (folder / 'agent_analysis.json').write_text('{}')
        result = self.run_with(execute)
        self.assertFalse(result['ok'])
        self.assertIn('渲染期间', result['error'])
        self.assertFalse((self.project / 'output' / f'agent_{self.job}.mp4').exists())

    def test_saved_portrait_resolution_is_used_for_final_and_preview_aspect(self):
        def execute(folder, command, log_name, timeout):
            self.execute(folder, command, log_name, timeout)
            if '-z' in command:
                path = folder / 'agent_scene.json'
                manifest = json.loads(path.read_text())
                manifest['resolution'] = [600, 1000]
                duel_plan.atomic_json(path, manifest)
        result = self.run_with(execute)
        self.assertTrue(result['ok'], result)
        resolutions = {name: command[command.index('--resolution') + 1] for command, name in self.commands
                       if name in ('preview.log', 'rendering.log')}
        self.assertEqual(resolutions, {'preview.log': '216x360', 'rendering.log': '600x1000'})

    def test_auto_submission_preserves_raw_prompt_and_selects_general_director(self):
        prompt = '三个人复杂编排；' * 1000
        with patch.object(duel_worker, 'spawn'):
            job = duel_worker.submit(prompt, mode='auto')
        saved = json.loads((self.jobs / job / 'request.json').read_text())
        self.assertEqual(saved['prompt'], prompt)
        self.assertEqual(saved['generation_contract_version'], 3)
        self.assertEqual(saved['execution_strategy'], 'hermes_mcp')
        self.assertEqual(saved['character_policy'], 'library_beta')

    def test_invalid_model_credentials_report_actual_cause_without_scene_retries(self):
        calls = []
        def execute(folder, command, log_name, timeout):
            calls.append(log_name)
            (folder / log_name).write_text('HTTP 401: Invalid API-key provided.\n', encoding='utf-8')
        result = self.run_with(execute)
        self.assertFalse(result['ok'])
        self.assertIn('HTTP 401', result['error'])
        self.assertIn('API Key', result['error'])
        self.assertEqual(calls, ['hermes.log'])


if __name__ == '__main__':
    unittest.main()
