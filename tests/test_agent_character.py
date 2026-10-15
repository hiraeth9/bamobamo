"""Pure validation/catalog arithmetic; does not mock a successful Beta rig."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import agent_character as character


class CharacterValidation(unittest.TestCase):
    def test_actor_identifier_does_not_accept_paths(self):
        for value in ('A', 'person_2', 'B.left'):
            character._validate_actor(value)
        for value in ('', '../A', 'A/B', None, 'A' * 65):
            with self.assertRaises(ValueError):
                character._validate_actor(value)

    def test_scene_coordinates_are_finite(self):
        self.assertEqual(character._finite_sequence([1, 2, 3], 3, 'position'), (1., 2., 3.))
        for value in ([1, 2], [1, 2, float('nan')], [1, True, 3]):
            with self.assertRaises(ValueError):
                character._finite_sequence(value, 3, 'position')

    def test_rig_compatibility_checks_hierarchy_and_rest_space(self):
        bone = {'name': 'Hips', 'parent': None, 'use_deform': True,
                'inherit_scale': 'FULL', 'use_inherit_rotation': True,
                'matrix_local': [[1., 0], [0, 1.]]}
        expected = {'bones': [bone]}
        actual = copy.deepcopy(expected)
        actual['bones'][0]['matrix_local'][0][0] += .0001
        self.assertTrue(character.compatible_rig(actual, expected))
        actual['bones'][0]['matrix_local'][0][0] += .01
        self.assertFalse(character.compatible_rig(actual, expected))
        actual = copy.deepcopy(expected)
        actual['bones'][0]['parent'] = 'Different'
        self.assertFalse(character.compatible_rig(actual, expected))


if __name__ == '__main__':
    unittest.main()
