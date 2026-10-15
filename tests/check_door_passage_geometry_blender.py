"""Real Blender geometry diagnostics, without renders or dynamic pose claims.

Run: ./blender -b -t 2 --python-exit-code 1 --python
     tests/check_door_passage_geometry_blender.py
"""
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from scene_objects import SceneObjects
from scene_plan import validate_scene
from door_passage_geometry import DoorPassageGeometry


def main():
    report = {'checks': [], 'scope':
        'Door-local real-source skin envelope planning, actual solid bounds and '
        'physical grip anchors. Does not certify dynamic hand poses or renders.'}

    def add(name, callback, refused_reason=None):
        try:
            details = callback() or {}
            if refused_reason is not None:
                raise AssertionError('infeasible geometry accepted')
            result = {'case': name, 'passed': True, **details}
        except ValueError as error:
            result = {'case': name, 'passed': refused_reason is not None and
                      refused_reason in str(error), 'refused': True,
                      'reason': str(error)}
        except Exception as error:
            result = {'case': name, 'passed': False, 'error': str(error),
                      'traceback': traceback.format_exc()}
        report['checks'].append(result)
        print('DOOR_GEOMETRY', name, 'PASS' if result['passed'] else 'FAIL', flush=True)

    def fixture(width=1.3, angle=80, rotated=False, obstruction=False):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        door = {'id': 'door_1', 'type': 'door', 'size': [width, .14, 2.1],
                'open_angle_deg': angle, 'state': 'open'}
        if rotated:
            door.update(position=[.3, .4, 0], yaw_deg=25)
        objects = [door]
        if obstruction:
            objects.append({'id': 'box_1', 'type': 'box',
                            'position': [.42, 1.6, 0], 'size': [.8, .8, 1.]})
        plan = validate_scene({'version': 1, 'duration_seconds': 8,
            'room': {'enabled': not rotated}, 'objects': objects},
            'door_geometry', 1, 8)
        props = SceneObjects(plan)
        return props, DoorPassageGeometry(props, 'door_1')

    def passage(width, angle, direction='outward', rotated=False, obstruction=False):
        props, geometry = fixture(width, angle, rotated, obstruction)
        hinge_rotation = props.objects['door_1']['hinge'].rotation_euler.copy()
        route = geometry.passage_route(direction)
        timeline = geometry.compile_passage(direction, 0, 8, 8)
        assert props.objects['door_1']['hinge'].rotation_euler == hinge_rotation
        assert len(timeline['frames']) == 240
        assert (Vector(timeline['frames'][0]['position'])-Vector(route['entry'])).length < 1e-6
        assert (Vector(timeline['frames'][-1]['position'])-Vector(route['exit'])).length < 1e-6
        assert timeline['final_settle_seconds'] >= .4-1e-9
        assert timeline['frames'][-1]['speed'] == 0
        initial = {key: props.anchor('door_1', key).copy() for key in ('entry', 'exit')}
        for degree in (0, 40, 80):
            props.set_angle('door_1', -math.radians(degree))
            assert all((props.anchor('door_1', key)-value).length < 1e-6
                       for key, value in initial.items())
            for side in ('front', 'back'):
                anchor = props.anchor('door_1', 'contact_'+side)
                bar = next(obj for obj in props.objects['door_1']['meshes']
                           if obj.name == 'door_1_grip_bar_'+side)
                local = bar.matrix_world.inverted() @ anchor
                assert abs(abs(local.y)-.0125) < 1e-6
                assert abs(local.x) <= bar.dimensions.x/2+1e-6
                assert abs(local.z) <= bar.dimensions.z/2+1e-6
        return {'route': route, 'final_settle_seconds': timeline['final_settle_seconds'],
                'anchors_on_real_bar_surface': True, 'leaf_unchanged_by_planning': True}

    def exterior_scope():
        _, geometry = fixture()
        route = geometry.passage_route()
        assert not geometry.route_blocked(route['exit'])
        assert geometry.route_blocked(geometry.world((route['center_root_x']+.7, 1.25)))
        assert geometry.route_blocked(geometry.world((route['center_root_x'], 2.1)))
        return {'portal_exterior_allowed': True, 'exterior_wall_detour_refused': True}

    def compile_wrong_start():
        _, geometry = fixture()
        route = geometry.passage_route()
        return geometry.compile_passage('outward', 0, 8, 8,
            start_position=[route['entry'][0]+.2, route['entry'][1]])

    def compile_short():
        _, geometry = fixture()
        return geometry.compile_passage('outward', 0, 2, 2)

    def invalid_body():
        props, _ = fixture()
        return DoorPassageGeometry(props, 'door_1',
            body_envelope={'min': [0, 0, 0], 'max': [0, 1, 2]})

    def invalid_clearance():
        props, _ = fixture()
        return DoorPassageGeometry(props, 'door_1', clearance=float('nan'))

    def calibrated_stances():
        _, geometry = fixture()
        rows = []
        for side, expected_xy, expected_yaw in (
                ('inside', [-.93, -.50], 123.0880),
                ('outside', [-.25, .45], -13.4968)):
            samples = [geometry.operation_stance(side, angle) for angle in (0, 40, 80)]
            for sample in samples:
                assert max(abs(a-b) for a, b in zip(sample['local_position'], expected_xy)) < 1e-6
                assert abs(math.degrees(sample['yaw'])-expected_yaw) < .001
                assert sample['requires_actual_fk_ik_reach_and_skin_review']
            assert all(abs(sample['yaw']-samples[0]['yaw']) < 1e-9 for sample in samples)
            rows.append({'side': side, 'local_position': samples[0]['local_position'],
                         'yaw_deg': math.degrees(samples[0]['yaw'])})
        return {'fixed_stances': rows, 'posed_skin_scope': 'Independent pose probe, plus runtime frame QA required.'}

    add('reject_default_narrow_70', lambda: passage(.9, 70), '通行净宽')
    add('reject_120_80', lambda: passage(1.2, 80), '通行净宽')
    add('reject_130_70', lambda: passage(1.3, 70), '通行净宽')
    add('wide_130_80_outward', lambda: passage(1.3, 80))
    add('wide_130_80_inward', lambda: passage(1.3, 80, 'inward'))
    add('rotated_standalone', lambda: passage(1.3, 80, rotated=True))
    add('reject_blocked_exit', lambda: passage(1.3, 80, rotated=True, obstruction=True), '真实门框/墙/家具阻挡')
    add('target_portal_exterior_only', exterior_scope)
    add('reject_wrong_start_without_approach', compile_wrong_start, '请先')
    add('reject_time_too_short', compile_short, '至少需要')
    add('reject_degenerate_body_bounds', invalid_body, 'min必须严格小于max')
    add('reject_nonfinite_clearance', invalid_clearance, '几何安全余量')
    add('fixed_calibrated_operation_stances', calibrated_stances)
    report['passed'] = all(check['passed'] for check in report['checks'])
    report['engine_sources_sha256'] = {name: hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
        for name in ('door_passage_geometry.py', 'scene_objects.py', 'locomotion_path.py')}
    output = ROOT/'reports/door_passage_geometry_regression_20261013.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print('DOOR_GEOMETRY_SUMMARY', json.dumps({'passed': report['passed'],
                                             'checks': len(report['checks'])}), flush=True)
    if not report['passed']:
        raise RuntimeError('Door geometry checks failed')


if __name__ == '__main__':
    main()
