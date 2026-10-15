"""The ground route must be executable and preserve the requested relations."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import duel_plan
from locomotion_plan import validate_locomotion, validate_locomotion_coverage
from scene_cast import validate_cast, write_cast
from scene_plan import write_scene
from single_plan import validate_single, write_single


def route():
    return {'version': 1, 'start_position': [0, -2], 'start_yaw_deg': 180,
            'segments': [
                {'start': 0, 'end': 2.5, 'gait': 'walk', 'waypoints': [[0, -2], [0, -.8]]},
                {'start': 2.5, 'end': 5, 'gait': 'run', 'waypoints': [[0, -.8], [.4, .5], [1, 1.5]]},
                {'start': 5, 'end': 5.5, 'gait': 'idle'},
                {'start': 6, 'end': 7, 'gait': 'turn', 'end_yaw_deg': 90},
            ]}


def body():
    return {'version': 1, 'duration_seconds': 8,
            'animations': ['mixamo_fbx/basic/Idle.fbx', 'mixamo_fbx/basic/Walk.fbx',
                           'mixamo_fbx/basic/Run.fbx'], 'locomotion': route()}


def cast():
    return {'actors': [{'id': 'A', 'name': '人物'}], 'requires_scene': False,
            'requires_interaction': False, 'requires_locomotion': True, 'object_intents': []}


class LocomotionPlanTests(unittest.TestCase):
    def test_impossible_timing_is_rejected_before_saving_a_plan(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(duel_plan, 'JOBS', Path(tmp)):
            write_cast('locomotion_test', cast())
            raw = body()
            raw['locomotion']['segments'] = [
                {'start': 0, 'end': .5, 'gait': 'turn', 'end_yaw_deg': 0},
            ]
            with self.assertRaisesRegex(ValueError, '转身时段太短'):
                write_single('locomotion_test', raw)
            self.assertFalse((duel_plan.job_directory('locomotion_test') / 'single_plan.json').exists())
            self.assertEqual(write_single('locomotion_test', body())['locomotion'], route())

    def test_route_roundtrip_preserves_waypoints_times_and_gap(self):
        raw = route()
        normalized = validate_locomotion(raw, 8)
        self.assertEqual(normalized, raw)
        self.assertEqual(validate_locomotion(json.loads(json.dumps(normalized)), 8), normalized)
        single = validate_single(body(), 'locomotion_test')
        self.assertEqual(single['locomotion'], raw)
        self.assertEqual(single['duration_seconds'], 8)
        self.assertEqual(single['frame_count'], 240)

    def test_discontinuous_route_overlap_and_short_segment_rejected(self):
        mutations = [
            lambda r: r['segments'][1]['waypoints'][0].__setitem__(0, .25),
            lambda r: r['segments'][1].update(start=2.4),
            lambda r: r['segments'][2].update(end=5.4),
            lambda r: r['segments'][3].update(end=8.1),
            lambda r: r['segments'][0].update(waypoints=[[0, -2], [0, -2]]),
        ]
        for mutate in mutations:
            raw = route(); mutate(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_locomotion(raw, 8)

    def test_unknown_actions_and_unexecuted_fields_rejected(self):
        mutations = [
            lambda r: r.update(speed=3),
            lambda r: r['segments'][0].update(gait='jump'),
            lambda r: r['segments'][0].update(end_yaw_deg=90),
            lambda r: r['segments'][0].update(target='table_1'),
            lambda r: r['segments'][2].update(waypoints=[[1, 1.5], [1, 1.5]]),
            lambda r: r['segments'][2].update(end_yaw_deg=90),
            lambda r: r['segments'][3].update(waypoints=[[1, 1.5], [2, 2]]),
            lambda r: r['segments'][3].pop('end_yaw_deg'),
        ]
        for mutate in mutations:
            raw = route(); mutate(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_locomotion(raw, 8)

    def test_shape_number_and_capacity_limits_rejected(self):
        bad_routes = [None, [], {'version': True, **{k: v for k, v in route().items() if k != 'version'}}]
        mutations = [
            lambda r: r.update(start_position=[0, 0, 0]),
            lambda r: r.update(start_position=[7, 0]),
            lambda r: r.update(start_yaw_deg=True),
            lambda r: r.update(start_yaw_deg=float('nan')),
            lambda r: r.update(segments=[]),
            lambda r: r.update(segments=r['segments'] * 5),
            lambda r: r['segments'][0].update(waypoints=[[0, -2]]),
            lambda r: r['segments'][0].update(waypoints=[[0, -2]] * 9),
            lambda r: r['segments'][0]['waypoints'][1].__setitem__(1, float('inf')),
            lambda r: r['segments'][0].update(start=False),
        ]
        for mutate in mutations:
            raw = route(); mutate(raw); bad_routes.append(raw)
        for raw in bad_routes:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_locomotion(raw, 8)

    def test_sources_and_camera_are_validated_only_for_new_route(self):
        for key, value in [('animations', ['mixamo_fbx/basic/Jump.fbx']), ('camera_policy', 'orbit'),
                           ('pitch_deg', 10), ('distance_ratio', 2), ('fov', 40)]:
            raw = body(); raw[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_single(raw, 'locomotion_test')
        old = {'version': 1, 'duration_seconds': 6, 'animations': ['mixamo_fbx/basic/Jump.fbx'],
               'camera_policy': 'orbit', 'pitch_deg': 20, 'distance_ratio': 2, 'fov': 40}
        self.assertNotIn('locomotion', validate_single(old, 'locomotion_test'))

    def test_route_ledger_and_body_must_match_in_both_directions(self):
        person = validate_cast(cast(), 'locomotion_test')
        single = validate_single(body(), 'locomotion_test')
        self.assertEqual(validate_locomotion_coverage(person, None, single), single['locomotion'])
        old = copy.deepcopy(single); old.pop('locomotion')
        with self.assertRaisesRegex(ValueError, '缺少locomotion'):
            validate_locomotion_coverage(person, None, old)
        person['requires_locomotion'] = False
        with self.assertRaisesRegex(ValueError, '没有声明'):
            validate_locomotion_coverage(person, None, single)

    def test_static_props_are_allowed_without_inventing_contact(self):
        person = validate_cast({**cast(), 'requires_scene': True}, 'locomotion_test')
        scene = {'objects': [{'id': 'table_1', 'type': 'table'}], 'interactions': []}
        validate_locomotion_coverage(person, scene, validate_single(body(), 'locomotion_test'))
        scene['interactions'] = [{'object': 'table_1', 'action': 'touch_table'}]
        with self.assertRaisesRegex(ValueError, '不与物品操作'):
            validate_locomotion_coverage(person, scene, validate_single(body(), 'locomotion_test'))

    def test_multiple_people_and_explicit_object_operations_remain_unsupported(self):
        raw = cast(); raw['actors'].append({'id': 'B', 'name': '另一个人物'})
        result = validate_cast(raw, 'locomotion_test')
        self.assertFalse(result['supported']); self.assertIn('仅支持单人', result['message'])
        raw = cast(); raw.update(requires_scene=True, requires_interaction=True,
                                 object_intents=[{'action': 'open_door', 'object_type': 'door',
                                                  'description': '走过去后开门'}])
        result = validate_cast(raw, 'locomotion_test')
        self.assertFalse(result['supported']); self.assertIn('串联尚未接入', result['message'])

    def test_old_casts_keep_their_saved_shape_and_new_auto_requires_route_flag(self):
        old = validate_cast({'actors': [{'id': 'A', 'name': '人物'}]}, 'locomotion_test')
        self.assertNotIn('requires_locomotion', old)
        with tempfile.TemporaryDirectory() as tmp, patch.object(duel_plan, 'JOBS', Path(tmp)):
            folder = duel_plan.job_directory('locomotion_test')
            duel_plan.atomic_json(folder / 'request.json', {'mode': 'auto', 'prompt': '走过去后停下来'})
            raw = cast(); raw.pop('requires_locomotion')
            with self.assertRaisesRegex(ValueError, 'requires_locomotion'):
                write_cast('locomotion_test', raw)
            write_cast('locomotion_test', cast())
            saved = write_single('locomotion_test', body())
            self.assertEqual(saved['locomotion'], route())
            with self.assertRaisesRegex(ValueError, '不同单人计划'):
                write_single('locomotion_test', {**body(), 'duration_seconds': 9})

    def test_required_route_cannot_be_saved_as_plain_walk_run(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(duel_plan, 'JOBS', Path(tmp)):
            write_cast('locomotion_test', cast())
            old = body(); old.pop('locomotion')
            with self.assertRaisesRegex(ValueError, '普通Walk/Run轮播不能替代'):
                write_single('locomotion_test', old)


if __name__ == '__main__':
    unittest.main()
