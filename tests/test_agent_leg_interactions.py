"""Kick phase and contact-window invariants, without Blender or rendering."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from agent_leg_interactions import kick_position, kick_timing, kick_weight, kick_pole, foot_contact_conflicts


class KickTrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.timing = kick_timing(4, 24, 52)
        self.initial, self.chamber, self.impact = [0, 0, .02], [.1, 0, .47], [.8, 0, .9]

    def test_real_phase_endpoints_and_grounded_recovery(self):
        timing = self.timing
        for key, expected in (('start', self.initial), ('chamber', self.chamber),
                              ('impact', self.impact), ('release', self.impact),
                              ('recover', self.chamber), ('land', self.initial), ('end', self.initial)):
            actual = kick_position(timing[key], timing, self.initial, self.chamber, self.impact)
            self.assertLess(math.dist(actual, expected), 1e-12)
        self.assertEqual(kick_weight(timing['start'], timing), 0)
        self.assertEqual(kick_weight(timing['land'], timing), 0)
        self.assertEqual(kick_weight(timing['impact'], timing), 1)

    def test_two_contact_frames_hold_the_impact_and_do_not_begin_recovery_early(self):
        timing = kick_timing(4, 24, 52, contact_frames=2)
        for frame in (24, 25):
            self.assertEqual(kick_position(frame, timing, self.initial, self.chamber, self.impact), self.impact)
        self.assertGreater(timing['recover'], 25)

    def test_phase_boundaries_are_continuous_including_landing(self):
        for frame in self.timing.values():
            a = kick_position(frame-.000001, self.timing, self.initial, self.chamber, self.impact)
            b = kick_position(frame+.000001, self.timing, self.initial, self.chamber, self.impact)
            self.assertLess(math.dist(a, b), .000001)

    def test_mirrored_or_turned_trajectories_keep_world_direction(self):
        right = kick_position(20, self.timing, self.initial, self.chamber, self.impact)
        turned = kick_position(20, self.timing, self.initial, [0, -.1, .47], [0, -.8, .9])
        self.assertAlmostEqual(right[0], -turned[1])
        self.assertAlmostEqual(right[2], turned[2])

    def test_timing_keeps_room_to_chamber_retract_land_and_settle(self):
        for args in ((4, 5, 6), (4, 24, 25), (24, 4, 52)):
            with self.assertRaises(ValueError):
                kick_timing(*args)

    def test_horizontal_extended_kick_bends_upward_and_stance_bends_forward(self):
        pole = kick_pole([0, 0, .9], [.8, 0, .9], [1, 0, 0])
        self.assertGreater(pole[2], .9)
        stance = kick_pole([0, 0, .9], [0, 0, .08], [1, 0, 0])
        self.assertGreater(stance[0], 0)

    def test_existing_attack_foot_contact_must_be_explicitly_released(self):
        contracts = [{'id': 'walk:stance', 'event_id': 'walk:planted', 'chain': 'RightFoot', 'frame_start': 1, 'frame_end': 30},
                     {'id': 'other', 'event_id': 'walk:support', 'chain': 'LeftFoot', 'frame_start': 1, 'frame_end': 60},
                     {'id': 'self', 'event_id': 'kick:land', 'chain': 'RightFoot', 'frame_start': 40, 'frame_end': 52}]
        self.assertEqual(foot_contact_conflicts(contracts, 'RightFoot', 5, 45, 'kick'), ['walk:stance'])
        self.assertEqual(foot_contact_conflicts(contracts, 'RightFoot', 31, 45, 'kick'), [])


if __name__ == '__main__':
    unittest.main()
