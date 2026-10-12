from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import duel_plan
from single_plan import validate_single, write_single


class SingleTests(unittest.TestCase):
    def plan(self):
        return {'version': 1, 'animations': ['mixamo_fbx/combat_melee/Punching.fbx', 'mixamo_fbx/combat_melee/Mma Kick.fbx'], 'duration_seconds': 4}

    def test_multiple_intents_and_fixed_default_are_preserved(self):
        plan = validate_single(self.plan(), 'single_test')
        self.assertEqual(len(plan['animations']), 2)
        self.assertEqual(plan['camera_policy'], 'fixed')
        self.assertEqual(plan['frame_count'], 120)

    def test_missing_and_escaping_resources_and_bad_camera_rejected(self):
        for assets in (['../outside.fbx'], ['mixamo_fbx/missing.fbx'], [123], []):
            with self.assertRaises(ValueError):
                validate_single({**self.plan(), 'animations': assets}, 'single_test')
        with self.assertRaises(ValueError):
            validate_single({**self.plan(), 'camera_policy': 'unknown'}, 'single_test')

    def test_saved_plan_cannot_change(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(duel_plan, 'JOBS', Path(folder)):
            first = write_single('single_test', self.plan())
            self.assertEqual(write_single('single_test', self.plan()), first)
            with self.assertRaises(ValueError):
                write_single('single_test', {**self.plan(), 'duration_seconds': 6})


if __name__ == '__main__':
    unittest.main()
