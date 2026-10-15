"""Behavioral route/dynamics checks without Blender or external services."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from locomotion_path import compile_timeline, plan_route


def movement(points, end, gait='walk', start=0):
    return {'start': start, 'end': end, 'gait': gait, 'waypoints': points}


def plan(segments, position=(0, 0), yaw=180):
    return {'version': 1, 'start_position': list(position),
            'start_yaw_deg': yaw, 'segments': segments}


def yaw_delta(a, b):
    return math.atan2(math.sin(b - a), math.cos(b - a))


class LocomotionPathTests(unittest.TestCase):
    def assert_dynamics(self, timeline):
        frames = timeline['frames']
        for previous, current in zip(frames, frames[1:]):
            dt = current['seconds'] - previous['seconds']
            self.assertLessEqual(abs(current['speed'] - previous['speed']) / dt, 3.8 + 1e-5)
            self.assertLessEqual(abs(yaw_delta(previous['yaw'], current['yaw'])) / dt,
                                 math.radians(120) + 1e-5)
            displacement = math.dist(previous['position'][:2], current['position'][:2])
            if displacement > .005 and previous['gait'] != 'turn' and current['gait'] != 'turn':
                direction = math.atan2(current['position'][0] - previous['position'][0],
                                       -(current['position'][1] - previous['position'][1]))
                self.assertLess(abs(yaw_delta(previous['yaw'], direction)), math.radians(12))
        self.assertTrue(all(b['distance'] + 1e-9 >= a['distance'] for a, b in zip(frames, frames[1:])))

    def test_clear_route_is_exact_straight_line(self):
        self.assertEqual(plan_route((.11, -.16), (1.23, 2.34)), [(.11, -.16), (1.23, 2.34)])

    def test_endpoint_blockage_is_not_rounded_away(self):
        blocked = lambda point: abs(point[0] - .11) < .01 and abs(point[1] - .11) < .01
        with self.assertRaisesRegex(ValueError, '终点'):
            plan_route((0, 0), (.11, .11), blocked)
        with self.assertRaisesRegex(ValueError, '起点'):
            plan_route((.11, .11), (1, 1), blocked)

    def test_detour_edges_have_continuous_clearance(self):
        blocked = lambda point: -.35 < point[0] < .35 and -.4 < point[1] < .4
        route = plan_route((-1, 0), (1, 0), blocked, (-2, 2, -2, 2))
        self.assertGreater(len(route), 2)
        self.assertEqual(route[0], (-1., 0.))
        self.assertEqual(route[-1], (1., 0.))
        for a, b in zip(route, route[1:]):
            count = max(1, math.ceil(math.dist(a, b) / .005))
            for i in range(count + 1):
                point = tuple(a[j] + (b[j] - a[j]) * i / count for j in (0, 1))
                self.assertFalse(blocked(point))

    def test_sealed_corridor_rejects(self):
        blocked = lambda point: abs(point[0]) < .16
        with self.assertRaisesRegex(ValueError, '没有可通行'):
            plan_route((-1, 0), (1, 0), blocked, (-1.5, 1.5, -.5, .5))

    def test_room_boundary_rejects_exact_target(self):
        with self.assertRaisesRegex(ValueError, '终点'):
            plan_route((0, 0), (1.01, 0), bounds=(-1, 1, -1, 1))

    def test_walk_starts_stops_and_reaches_last_visible_frame(self):
        result = compile_timeline(plan([movement([[0, 0], [0, 4]], 5)]), 5)
        self.assertEqual(len(result['frames']), 150)
        self.assertEqual(result['frames'][0]['position'], [0., 0., 0.])
        self.assertEqual(result['frames'][-1]['position'], [0., 4., 0.])
        self.assertEqual(result['frames'][0]['speed'], 0.)
        self.assertEqual(result['frames'][-1]['speed'], 0.)
        self.assertLessEqual(max(f['speed'] for f in result['frames']), 1.65)
        self.assert_dynamics(result)

    def test_aligned_walk_to_run_keeps_nonzero_speed(self):
        result = compile_timeline(plan([
            movement([[0, 0], [0, 3.5]], 3),
            movement([[0, 3.5], [0, 7.5]], 5.6, 'run', 3)]), 5.6)
        before, boundary, after = result['frames'][89:92]
        self.assertEqual(boundary['seconds'], 3.)
        self.assertEqual(boundary['gait'], 'run')
        self.assertGreater(boundary['speed'], .8)
        self.assertEqual(boundary['position'], [0., 3.5, 0.])
        self.assertGreater(before['speed'], .8)
        self.assertGreater(after['speed'], .8)
        self.assert_dynamics(result)

    def test_round_corner_turns_toward_motion_without_sideways_sliding(self):
        result = compile_timeline(plan([movement([[0, 0], [0, 2], [2, 2]], 5)]), 5)
        self.assertAlmostEqual(result['frames'][-1]['yaw'], math.pi / 2)
        self.assert_dynamics(result)
        changing = [f for f in result['frames'] if math.pi / 2 + .1 < f['yaw'] < math.pi - .1]
        self.assertTrue(changing)
        self.assertLess(max(f['speed'] for f in changing), 1.65)

    def test_small_cross_segment_heading_change_is_safely_braked(self):
        # A 0.286-degree mismatch used to request a shared speed and an
        # incompatible mandatory turn at the same segment boundary.
        result = compile_timeline(plan([
            movement([[0, 0], [0, 3]], 3),
            movement([[0, 3], [.015, 6]], 6, start=3)]), 6)
        boundary = result['frames'][90]
        self.assertEqual(boundary['position'], [0., 3., 0.])
        self.assertEqual(boundary['speed'], 0.)
        self.assertTrue(result['segments'][1]['turns'])
        self.assertAlmostEqual(result['frames'][-1]['position'][0], .015)
        self.assertAlmostEqual(result['frames'][-1]['position'][1], 6.)
        self.assert_dynamics(result)

    def test_tiny_heading_residual_does_not_accumulate_into_rejection(self):
        segments, position = [], [0., 0.]
        for i in range(4):
            angle = math.radians(i * .008)
            target = [position[0] + math.sin(angle) * .7,
                      position[1] + math.cos(angle) * .7]
            segments.append(movement([position, target], i + 1, start=i))
            position = target
        segments.append({'start': 4, 'end': 5, 'gait': 'idle'})
        result = compile_timeline(plan(segments), 5)
        self.assertTrue(result['segments'][2]['turns'])
        self.assert_dynamics(result)

    def test_reversal_stops_before_turning_and_continues(self):
        result = compile_timeline(plan([movement([[0, 0], [0, 2], [0, 0]], 9)]), 9)
        turning = [f for f in result['frames'] if f['gait'] == 'turn']
        self.assertTrue(turning)
        self.assertTrue(all(f['speed'] == 0 for f in turning))
        self.assertTrue(all(math.dist(f['position'][:2], [0, 2]) < 1e-7 for f in turning))
        self.assertGreater(result['turn_distance_m'], .5)
        self.assert_dynamics(result)

    def test_initial_facing_consumes_time_and_uses_turn_steps(self):
        result = compile_timeline(plan([movement([[0, 0], [0, 3]], 7)], yaw=0), 7)
        self.assertEqual(result['frames'][0]['gait'], 'turn')
        turning = [f for f in result['frames'] if f['gait'] == 'turn']
        self.assertTrue(all(f['position'] == [0., 0., 0.] for f in turning))
        self.assertGreater(turning[-1]['turn_distance'], .5)
        self.assert_dynamics(result)

    def test_requested_time_too_short_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '至少需要'):
            compile_timeline(plan([movement([[0, 0], [0, 5]], 1)]), 1)

    def test_explicit_turn_enforces_yaw_rate(self):
        result = compile_timeline(plan([{'start': 0, 'end': 2, 'gait': 'turn',
                                         'end_yaw_deg': 90}], yaw=0), 2)
        self.assertEqual(result['frames'][-1]['position'], [0., 0., 0.])
        self.assertAlmostEqual(result['frames'][-1]['yaw'], math.pi / 2)
        self.assert_dynamics(result)
        with self.assertRaisesRegex(ValueError, '转身时段太短'):
            compile_timeline(plan([{'start': 0, 'end': .5, 'gait': 'turn',
                                     'end_yaw_deg': 90}], yaw=0), 1)

    def test_gap_is_real_idle_and_position_does_not_jump(self):
        result = compile_timeline(plan([
            movement([[0, 0], [0, 2]], 3),
            movement([[0, 2], [0, 4]], 7, start=4)]), 7)
        gap = [f for f in result['frames'] if 3 <= f['seconds'] < 4]
        self.assertTrue(all(f['gait'] == 'idle' and f['position'] == [0., 2., 0.] and
                            f['speed'] == 0 for f in gap))
        self.assert_dynamics(result)

    def test_inconsistent_waypoint_start_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '当前人物位置'):
            compile_timeline(plan([movement([[1, 0], [0, 3]], 5)]), 5)

    def test_long_run_does_not_become_a_slow_walk(self):
        result = compile_timeline(plan([movement([[0, 0], [0, 3]], 8, 'run')]), 8)
        self.assertTrue(result['warnings'])
        running = [f for f in result['frames'] if f['gait'] == 'run']
        self.assertGreater(max(f['speed'] for f in running), 1.09)
        self.assertEqual(result['frames'][-1]['gait'], 'idle')
        self.assertEqual(result['frames'][-1]['position'], [0., 3., 0.])
        self.assert_dynamics(result)

    def test_terminal_move_has_visible_time_to_lower_swinging_foot(self):
        result = compile_timeline(plan([movement([[0, 0], [0, 4]], 5)]), 5)
        last = result['frames'][-1]
        settled = [frame for frame in result['frames']
                   if frame['seconds'] >= last['seconds'] - .4 - 1e-9]
        self.assertTrue(all(frame['gait'] == 'idle' and frame['position'] == [0., 4., 0.]
                            and frame['speed'] == 0 for frame in settled))
        executed = result['segments'][0]
        self.assertEqual(executed['requested_end'], 5.)
        self.assertAlmostEqual(executed['settle_seconds'], .4)
        self.assertAlmostEqual(executed['settle_reserved_in_segment_seconds'], .4 + 1 / 30)
        self.assertTrue(result['warnings'])
        self.assert_dynamics(result)

    def test_existing_trailing_idle_is_not_reserved_twice(self):
        result = compile_timeline(plan([
            movement([[0, 0], [0, 3]], 4),
            {'start': 4, 'end': 5, 'gait': 'idle'}]), 5)
        executed = result['segments'][0]
        self.assertEqual(executed['executed_end'], 4.)
        self.assertEqual(executed['settle_reserved_in_segment_seconds'], 0.)
        self.assertAlmostEqual(executed['settle_seconds'], 1 - 1 / 30)

    def test_short_existing_idle_reserves_only_missing_settle_time(self):
        result = compile_timeline(plan([
            movement([[0, 0], [0, 3]], 4.8),
            {'start': 4.8, 'end': 5, 'gait': 'idle'}]), 5)
        executed = result['segments'][0]
        self.assertAlmostEqual(executed['settle_reserved_in_segment_seconds'], .4 - (.2 - 1 / 30))
        self.assertAlmostEqual(executed['settle_seconds'], .4)

    def test_terminal_turn_also_has_visible_recovery(self):
        result = compile_timeline(plan([{'start': 0, 'end': 2, 'gait': 'turn',
                                         'end_yaw_deg': 90}], yaw=0), 2)
        cutoff = result['frames'][-1]['seconds'] - .4
        settled = [frame for frame in result['frames'] if frame['seconds'] >= cutoff - 1e-9]
        self.assertTrue(all(frame['gait'] == 'idle' and abs(frame['yaw'] - math.pi / 2) < 1e-8
                            for frame in settled))
        self.assert_dynamics(result)

    def test_settle_cannot_be_hidden_by_over_speeding_short_move(self):
        # Two metres fit in the raw two-second span; reserving the required
        # recovery makes that span physically too short and must be rejected.
        with self.assertRaisesRegex(ValueError, '至少需要'):
            compile_timeline(plan([movement([[0, 0], [0, 2]], 2)]), 2)

    def test_obstacle_detour_checks_rendered_frame_positions(self):
        blocked = lambda point: -.4 < point[0] < .4 and .8 < point[1] < 1.6
        result = compile_timeline(plan([movement([[0, 0], [0, 3]], 8)]),
                                  8, blocked, (-2, 2, -1, 4))
        self.assertTrue(all(not blocked(f['position'][:2]) for f in result['frames']))
        self.assert_dynamics(result)


if __name__ == '__main__':
    unittest.main()
