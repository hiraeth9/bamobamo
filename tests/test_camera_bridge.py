"""Exercise placement preservation at duel validation and worker dispatch."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from duel_cases import fixture
import duel_plan
import duel_worker
from semantic_review import write_review
from single_plan import validate_single


class CameraBridgeTests(unittest.TestCase):
    def test_duel_preserves_explicit_placement_and_existing_pov(self):
        raw = fixture('camera_bridge')
        raw['camera'] = {
            'policy': 'cinematic',
            'placement': {'position': [5, -5, 3], 'look_at': [0, 0, 1]},
            'shots': [{'kind': 'full', 'start': 0, 'end': 1},
                      {'kind': 'pov', 'actor': 'B', 'start': 1, 'end': 2},
                      {'kind': 'full', 'start': 2, 'end': 4}],
        }
        plan = duel_plan.validate_plan(raw, 'camera_bridge')
        self.assertEqual(plan['camera']['placement']['position'], [5, -5, 3])
        self.assertEqual(plan['camera']['shots'][1]['actor'], 'B')
        self.assertEqual(duel_plan.validate_plan(plan, 'camera_bridge'), plan)

    def test_camera_fields_cannot_be_discarded_or_conflict(self):
        for camera in ({'position': [5, -5, 3]},
                       {'policy': 'fixed_full', 'shots': []},
                       {'placement': {}, 'azimuth_deg': -50},
                       {'margin': .1}):
            with self.subTest(camera=camera), self.assertRaises(ValueError):
                duel_plan.validate_plan({**fixture(), 'camera': camera}, 'camera_bridge')

    def test_plain_single_camera_builds_a_saved_scene_for_quality_and_preview(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(duel_plan, 'JOBS', Path(temp)), \
                patch.object(duel_worker, 'JOBS', Path(temp)):
            job = 'camera_plain_bridge'
            folder = duel_plan.job_directory(job)
            duel_plan.atomic_json(folder/'request.json', {'mode': 'auto', 'prompt': '人物练习出拳，固定左前方镜头'})
            duel_plan.atomic_json(folder/'cast.json', {'actors': [{'id': 'A', 'name': '练习者'}]})
            plan = validate_single({'version': 1, 'animations': ['mixamo_fbx/basic/Idle.fbx'],
                                    'duration_seconds': 4,
                                    'camera': {'azimuth_deg': -45}}, job)
            duel_plan.atomic_json(folder/'single_plan.json', plan)
            write_review(job, {'version': 1, 'requirements': [{'description': '固定左前方镜头',
                          'status': 'covered', 'evidence': 'camera.azimuth_deg=-45'}], 'summary': '已绑定'})
            commands = []
            def build_only(folder, command, log_name, timeout):
                commands.append(command)
                raise RuntimeError('stop after checking dispatch')
            with patch.object(duel_worker, 'run_logged', side_effect=build_only):
                result = duel_worker.run_job(job, already_planned=True)
            self.assertFalse(result['ok'])
            self.assertEqual(len(commands), 1)
            self.assertIn('--camera-plan', commands[0])
            self.assertIn('--save-scene', commands[0])
            self.assertNotIn('--scene-plan', commands[0])


if __name__ == '__main__':
    unittest.main()
