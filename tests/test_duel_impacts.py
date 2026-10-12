from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from duel_impacts import strike_reach, step_distance, response_amount, recoil_step_progress
from duel_plan import validate_plan


def impact_plan(job='impact_demo'):
    return {'version': 1, 'duration_seconds': 8, 'fps': 30, 'seed': 22,
            'camera': {'policy': 'cinematic', 'shots': [{'kind': 'full', 'start': 0, 'end': 4.7},
                {'kind': 'pov', 'actor': 'B', 'start': 4.7, 'end': 5.45}, {'kind': 'full', 'start': 5.45, 'end': 8}]},
            'events': [{'kind': 'strike', 'actor': 'A', 'motion': 'jab', 'side': 'left', 'region': 'body', 'outcome': 'hit', 'intensity': .4,
                        'reaction': 'recoil', 'start': .3, 'peak': .75, 'end': 1.3},
                       {'kind': 'strike', 'actor': 'B', 'motion': 'jab', 'side': 'right', 'region': 'head', 'outcome': 'hit', 'intensity': .95,
                        'reaction': 'stagger', 'start': 1.7, 'peak': 2.3, 'end': 3.0},
                       {'kind': 'strike', 'actor': 'B', 'motion': 'hook', 'side': 'right', 'region': 'head', 'outcome': 'hit', 'intensity': 1.0,
                        'reaction': 'knockdown', 'start': 4.4, 'peak': 5.1, 'end': 6.0}]}


class ImpactTests(unittest.TestCase):
    def test_accelerates_into_contact_and_then_retracts(self):
        event = impact_plan()['events'][0]
        contact = round(event['peak'] * 30) / 30
        near = [strike_reach(event, contact - i / 30, 'hand') for i in range(4)]
        self.assertGreater(near[0] - near[1], near[2] - near[3])
        self.assertEqual(strike_reach(event, event['peak'], 'hand'), 1)
        self.assertEqual(strike_reach(event, event['end'], 'hand'), 0)

    def test_fractional_contact_times_reach_the_surface_on_the_sampled_frame(self):
        for peak in (.75, 6.75, 13.35, .76):
            event = {'start': peak - .55, 'peak': peak, 'end': peak + .6}
            for limb in ('hand', 'foot'):
                self.assertEqual(strike_reach(event, round(peak * 30) / 30, limb), 1)

    def test_light_and_heavy_impacts_have_different_displacement(self):
        light, heavy = impact_plan()['events'][:2]
        self.assertEqual(step_distance(light), 0)
        self.assertGreater(step_distance(heavy), .4)
        self.assertGreater(response_amount(1/30), .4)
        self.assertEqual(response_amount(-.01), 0)
        self.assertEqual(recoil_step_progress(heavy, heavy['end']), 1)

    def test_shots_and_reactions_survive_validation(self):
        plan = validate_plan(impact_plan(), 'impact_test')
        self.assertEqual(plan['camera']['shots'][1]['actor'], 'B')
        self.assertEqual(plan['events'][-1]['reaction'], 'knockdown')

    def test_knockdown_cannot_silently_stand_and_attack(self):
        plan = impact_plan()
        plan['events'].append({'kind': 'strike', 'actor': 'A', 'motion': 'jab', 'region': 'head', 'outcome': 'hit', 'start': 6.2, 'peak': 6.8, 'end': 7.4})
        with self.assertRaises(ValueError):
            validate_plan(plan, 'impact_test')

    def test_shot_gaps_and_invalid_people_rejected(self):
        for change in ({'start': 4.8}, {'actor': 'C'}, {'end': 9}):
            plan = impact_plan()
            plan['camera']['shots'][1].update(change)
            with self.assertRaises(ValueError):
                validate_plan(plan, 'impact_test')

    def test_intense_fight_cannot_hide_long_idle_gaps(self):
        raw = impact_plan()
        raw['pace'] = 'intense'
        with self.assertRaises(ValueError):
            validate_plan(raw, 'impact_test')
        raw['events'][-1].update(start=3.4, peak=4.1, end=4.9)
        with self.assertRaises(ValueError):
            validate_plan(raw, 'impact_test')
        raw['events'][-1].update(start=3.4, peak=4.0, end=4.7)
        self.assertEqual(validate_plan(raw, 'impact_test')['pace'], 'intense')


if __name__ == '__main__':
    unittest.main()
