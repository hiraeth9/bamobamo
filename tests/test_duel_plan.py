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
from duel_cases import fixture


class PlanTests(unittest.TestCase):
    def test_events_remain_dynamic(self):
        plan = duel_plan.validate_plan(fixture(), 'test')
        changed = fixture()
        changed['events'][0].update(actor='B', motion='hook', region='head', outcome='dodge', side='right')
        alternative = duel_plan.validate_plan(changed, 'test')
        self.assertNotEqual(plan['events'], alternative['events'])
        self.assertEqual(plan['frame_count'], 120)

    def test_invalid_resources_timing_and_roles_are_rejected(self):
        for field, value in [('motion', '../../secret'), ('actor', 'C'), ('outcome', 'unknown'), ('start', float('nan')), ('peak', .51), ('end', .7)]:
            with self.subTest(field=field):
                plan = fixture()
                plan['events'][0][field] = value
                with self.assertRaises((ValueError, TypeError)):
                    duel_plan.validate_plan(plan, 'test')
        plan = fixture()
        plan['events'][1]['start'] = 1
        with self.assertRaises(ValueError):
            duel_plan.validate_plan(plan, 'test')

    def test_unsupported_fps_and_camera_rejected(self):
        for extra in ({'fps': 24}, {'camera': {'policy': 'closeup'}}, {'duration_seconds': 90}):
            with self.assertRaises(ValueError):
                duel_plan.validate_plan({**fixture(), **extra}, 'test')

    def test_job_paths_cannot_escape(self):
        for value in ('../other', '/tmp/path', 'A', '', 'a/b'):
            with self.assertRaises(ValueError):
                duel_plan.job_directory(value)

    def test_cc0_clip_cross_is_executable_and_assets_remain_restricted(self):
        raw = fixture()
        raw['events'][0].update(motion='cross',side='right')
        self.assertEqual(duel_plan.validate_plan(raw,'cross_test')['events'][0]['motion'],'cross')
        definitions = duel_plan.catalog()
        definitions['motions']['cross']['asset'] = 'README.md'
        with patch.object(duel_plan,'catalog',return_value=definitions), self.assertRaises(ValueError):
            duel_plan.validate_plan(raw,'cross_test')

    def test_cc0_asset_integrity_is_checked_before_execution(self):
        definitions = duel_plan.catalog()
        definitions['motions']['jab']['sha256'] = '0'*64
        with patch.object(duel_plan,'catalog',return_value=definitions), self.assertRaises(ValueError):
            duel_plan.validate_plan(fixture(),'integrity_test')

    def test_job_plan_is_immutable_and_repairs_are_bounded(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(duel_plan, 'JOBS', Path(folder)):
            first = duel_plan.write_plan('test', fixture())
            self.assertEqual(duel_plan.write_plan('test', fixture()), first)
            modified = fixture()
            modified['events'][0]['outcome'] = 'block'
            with self.assertRaises(ValueError):
                duel_plan.write_plan('test', modified)
            duel_plan.write_plan('test', modified, revision=1)
            with self.assertRaises(ValueError):
                duel_plan.write_plan('test', modified, revision=3)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.patches = [patch.object(duel_plan, 'JOBS', self.folder), patch.object(duel_worker, 'JOBS', self.folder)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temporary.cleanup()

    def test_idempotency_never_creates_two_workers(self):
        with patch.object(duel_worker, 'spawn') as spawn:
            job = duel_worker.submit('双人直拳', 'request-key')
            self.assertEqual(job, duel_worker.submit('双人直拳', 'request-key'))
            self.assertEqual(spawn.call_count, 1)
            with self.assertRaises(ValueError):
                duel_worker.submit('不同动作', 'request-key')

    def test_renderer_failure_never_returns_video(self):
        duel_plan.write_plan('failed', fixture())
        with patch.object(duel_worker, 'run_logged', side_effect=RuntimeError('render process died')):
            result = duel_worker.run_job('failed', already_planned=True)
        self.assertFalse(result['ok'])
        self.assertNotIn('video', result)
        self.assertEqual(duel_worker.status('failed')['stage'], 'failed')

    def test_encoder_or_media_failure_never_publishes(self):
        duel_plan.write_plan('encodefail', fixture())
        def command(folder, *_):
            duel_plan.atomic_json(folder / 'quality.json', {'passed': True})
        with patch.object(duel_worker, 'run_logged', side_effect=command), patch.object(duel_worker, 'verify_video', side_effect=RuntimeError('decode failed')):
            result = duel_worker.run_job('encodefail', already_planned=True)
        self.assertFalse(result['ok'])
        self.assertIn('decode failed', result['error'])

    def test_scene_check_failure_stops_before_render(self):
        duel_plan.write_plan('checkfail', fixture())
        def command(folder, *_):
            duel_plan.atomic_json(folder / 'quality.json', {'passed': False})
        with patch.object(duel_worker, 'run_logged', side_effect=command) as execute:
            result = duel_worker.run_job('checkfail', already_planned=True)
        self.assertFalse(result['ok'])
        self.assertEqual(execute.call_count, 1)


if __name__ == '__main__':
    unittest.main()
