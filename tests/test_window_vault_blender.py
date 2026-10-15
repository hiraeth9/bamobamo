"""Real Blender vault QA, including independent measurements after scene reload.

Run only with Blender after window_vault_scene.build is available. No frames or
videos are rendered here; mesh deformation, saved animation and contacts are
evaluated directly. Visual motion quality still requires watching the preview.
"""
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from duel_plan import atomic_json
from duel_scene import point
from scene_objects import SceneObjects
from scene_plan import validate_scene
from single_plan import validate_single
from window_vault_scene import build

REPORT = ROOT / 'reports/window_vault_blender_regression.json'
CONTACT_TOLERANCE_M = .0495
GROUND_TOLERANCE_M = -.045


def plans(name, approach='walk_run', window=None, room=None, extra_objects=None):
    single = validate_single({
        'version': 1, 'duration_seconds': 8,
        'animations': ['mixamo_fbx/basic/Idle.fbx', 'mixamo_fbx/basic/Walk.fbx',
                       'mixamo_fbx/basic/Run.fbx'],
    }, name)
    event = {'actor': 'A', 'object': 'window_1', 'action': 'vault_window',
             'start': 0, 'end': 8, 'approach': approach}
    environment = validate_scene({
        'version': 1, 'duration_seconds': 8,
        'room': room or {'enabled': True, 'size': [6, 5, 3]},
        'objects': [{'id': 'window_1', 'type': 'window', **(window or {})}]
                   + (extra_objects or []),
        'interactions': [event],
    }, name, 1, 8)
    return single, environment


def mesh_vertices(meshes):
    """Read evaluated skin vertices, including the baked armature deformation."""
    graph = bpy.context.evaluated_depsgraph_get()
    for obj in meshes:
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        try:
            for vertex in mesh.vertices:
                yield evaluated.matrix_world @ vertex.co
        finally:
            evaluated.to_mesh_clear()


def reload_measurements(folder, single, environment, quality):
    scene_path = folder / 'scene.blend'
    assert scene_path.is_file(), '没有保存可独立重载的scene.blend'
    bpy.ops.wm.open_mainfile(filepath=str(scene_path))
    scene = bpy.context.scene
    actor_id = quality.get('actor', 'A')
    window_id = quality.get('window_id', 'window_1')
    assert actor_id == 'A' and window_id == 'window_1'
    root = bpy.data.objects[actor_id + '_Space']
    arm = bpy.data.objects[actor_id + '_Rig']
    frame = bpy.data.objects['Prop_' + window_id]
    # Use the fixed frame's outside-facing normal: initial is negative, final
    # is positive. This is independent of the rotating sash/hinge normal.
    normal = (frame.matrix_world.to_3x3() @ Vector((0, 1, 0))).normalized()
    plane_origin = frame.matrix_world.translation.copy()
    assert scene.frame_start == 1 and scene.frame_end == single['frame_count']
    roots = []
    for at_frame in (1, single['frame_count']):
        scene.frame_set(at_frame)
        bpy.context.view_layer.update()
        roots.append(root.matrix_world.translation.copy())
    signed = [(position - plane_origin).dot(normal) for position in roots]
    assert signed[0] < -.5, f'初始root并不在窗前：{signed[0]}'
    assert signed[1] > .5, f'最终root没有穿到窗外：{signed[1]}'
    reported = quality['traversal']
    assert abs(signed[0] - reported['start_signed_distance_m']) <= .001
    assert abs(signed[1] - reported['end_signed_distance_m']) <= .001

    meshes = [obj for obj in scene.objects if obj.type == 'MESH'
              and (obj.get('actor_id') == actor_id or
                   any(mod.type == 'ARMATURE' and mod.object == arm for mod in obj.modifiers))]
    assert meshes, '找不到人物变形皮肤'
    scene.frame_set(single['frame_count'])
    vertices = list(mesh_vertices(meshes))
    final_signed_min = min((vertex - plane_origin).dot(normal) for vertex in vertices)
    final_min_z = min(vertex.z for vertex in vertices)
    final_height = max(vertex.z for vertex in vertices) - final_min_z
    assert final_signed_min > .05, f'root通过但最终身体仍跨在窗框平面上：{final_signed_min}'
    assert final_min_z >= GROUND_TOLERANCE_M, f'最终皮肤落到地下：{final_min_z}'
    assert final_height > 1.2, f'落地后没有恢复人物高度：{final_height}'

    anchors = [bpy.data.objects[window_id + '_sill_' + side + '_anchor']
               for side in ('left', 'right')]
    contacts = quality['contacts']
    by_limb = {limb: [entry for entry in contacts if entry['limb'] == limb]
               for limb in ('LeftHand', 'RightHand')}
    assert all(by_limb.values()), '两只手都必须有实际撑窗接触帧'
    max_marker_error = 0
    max_anchor_error = 0
    anchor_by_limb = {}
    checked = 0
    for limb, entries in by_limb.items():
        # All contact records are re-evaluated, rather than only a claimed peak.
        for contact in entries:
            scene.frame_set(contact['frame'])
            bpy.context.view_layer.update()
            marker = point(arm, limb.replace('Hand', 'HandMiddle1'))
            target = Vector(contact['target'])
            error = (marker - target).length
            distances = [(anchor.matrix_world.translation - target).length for anchor in anchors]
            selected = min(range(2), key=distances.__getitem__)
            anchor_by_limb.setdefault(limb, set()).add(selected)
            max_marker_error = max(max_marker_error, error)
            max_anchor_error = max(max_anchor_error, distances[selected])
            assert error <= CONTACT_TOLERANCE_M, f'重载后{limb}接触误差：{error}'
            assert distances[selected] <= .02, f'接触目标不是固定窗台锚点：{distances[selected]}'
            assert abs(error - contact['error_m']) <= .001, '报告接触误差与实际场景不匹配'
            checked += 1
    assert anchor_by_limb['LeftHand'].isdisjoint(anchor_by_limb['RightHand']), '双手不能伪装成撑同一锚点'
    actual_spec = next(obj for obj in environment['objects'] if obj['id'] == window_id)
    assert (frame.matrix_world.translation - Vector(actual_spec['position'])).length < 1e-5
    saved_spec = json.loads((folder / 'scene_plan.json').read_text())['objects'][0]
    assert saved_spec['size'] == actual_spec['size'], '执行器篡改用户窗口尺寸'
    return {'root_signed_distance_m': signed, 'final_skin_signed_min_m': final_signed_min,
            'final_skin_min_z_m': final_min_z, 'final_skin_height_m': final_height,
            'contacts_rechecked': checked, 'max_reloaded_marker_error_m': max_marker_error,
            'max_fixed_anchor_error_m': max_anchor_error}


def assert_quality(quality, approach):
    assert quality['passed'], '执行器总质量检查未通过'
    assert quality['kind'] == 'single_window_vault'
    assert quality['traversal']['passed']
    assert quality['landing']['passed']
    for key in ('skin_clearance_failures', 'ground_failures', 'joint_flip_failures'):
        assert not quality[key], f'{key}不为空'
    phases = quality['phase_counts']
    if approach == 'walk_run':
        assert phases.get('walk', 0) > 0 and phases.get('run', 0) > 0, '先走后跑必须有两种阶段'
    else:
        assert phases.get(approach, 0) > 0
        other = 'run' if approach == 'walk' else 'walk'
        assert phases.get(other, 0) == 0, f'{approach}模式不应添加{other}接近动作'


def anchor_invariance():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _, environment = plans('vault_anchors', window={
        'position': [.3, .5, .85], 'yaw_deg': 25,
    }, room={'enabled': False})
    props = SceneObjects(environment)
    original_normal = props.frame_normal('window_1').copy()
    original = {label: props.anchor('window_1', label).copy()
                for label in ('sill_left', 'sill_right', 'entry', 'exit')}
    handle_before = props.anchor('window_1', 'handle').copy()
    max_error = 0
    for angle in (0, -math.radians(35), -math.radians(80)):
        props.set_angle('window_1', angle)
        max_error = max(max_error, (props.frame_normal('window_1') - original_normal).length)
        for label, position in original.items():
            max_error = max(max_error, (props.anchor('window_1', label) - position).length)
    assert max_error < 1e-6, '翻窗法线或窗台锚点被窗扇hinge带动'
    assert (props.anchor('window_1', 'handle') - handle_before).length > .2, '测试没有实际转动窗扇'
    return {'max_fixed_frame_error': max_error, 'fixed_anchors': list(original)}


def main():
    report = {'checks': [], 'scope': 'Real Blender animated scene and deformed skin; '
              'independent root traversal and bilateral contact measurements after reload. '
              'No rendered-video or subjective motion-naturalness certification.'}
    def check(name, callback):
        try:
            details = callback()
            entry = {'case': name, 'passed': True, **details}
        except Exception as error:
            entry = {'case': name, 'passed': False, 'error': str(error),
                     'traceback': traceback.format_exc(), **getattr(error, 'case_details', {})}
        report['checks'].append(entry)
        print('WINDOW_VAULT', name, 'PASS' if entry['passed'] else 'FAIL', flush=True)
        atomic_json(REPORT, report)

    successful = [
        ('default_walk_run', 'walk_run', {}, {}),
        ('direct_run', 'run', {}, {}),
        ('walk_only', 'walk', {}, {}),
        ('large_offset_window', 'walk_run', {
            'size': [1.4, .14, 1.4], 'position': [.8, 3, .75],
        }, {'enabled': True, 'size': [6, 6, 3]}),
        ('rotated_standalone_window', 'walk_run', {
            'position': [.3, .5, .85], 'yaw_deg': 25,
        }, {'enabled': False}),
    ]
    for name, approach, window, room in successful:
        def success(name=name, approach=approach, window=window, room=room):
            with tempfile.TemporaryDirectory(prefix='bamo-vault-') as temporary:
                folder = Path(temporary)
                single, environment = plans(name, approach, window, room or None)
                atomic_json(folder / 'scene_plan.json', environment)
                original_hash = hashlib.sha256((folder / 'scene_plan.json').read_bytes()).hexdigest()
                quality = None
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        quality = build(single, folder)
                    assert_quality(quality, approach)
                    assert hashlib.sha256((folder / 'scene_plan.json').read_bytes()).hexdigest() == original_hash
                    measurements = reload_measurements(folder, single, environment, quality)
                except Exception as error:
                    diagnostic = ROOT / 'output/verification/window_vault_regression' / name
                    shutil.copytree(folder, diagnostic, dirs_exist_ok=True)
                    error.case_details = {'quality': quality,
                                          'diagnostic_scene_folder': str(diagnostic.relative_to(ROOT))}
                    raise
                return {'quality': quality, 'reload_measurements': measurements,
                        'source_scene_plan_unchanged': True}
        check(name, success)

    rejected = [
        ('reject_narrow_window', {'size': [.7, .12, 1.2]}, [], ('宽', '尺寸', '净空', 'clearance')),
        ('reject_high_sill', {'position': [-1.35, 2.5, 1.5]}, [], ('高度', '窗底', '窗台', '可达', 'sill')),
        ('reject_blocked_approach', {}, [{
            'id': 'box_blocker', 'type': 'box', 'size': [1.2, 1.2, 1.2],
            'position': [-1.35, 1.89, 0],
        }], ('封堵', '阻挡', '障碍', '路径', '路线', '站位', '碰撞', '箱', 'blocked', 'obstacle')),
    ]
    for name, window, objects, reasons in rejected:
        def refusal(name=name, window=window, objects=objects, reasons=reasons):
            with tempfile.TemporaryDirectory(prefix='bamo-vault-refusal-') as temporary:
                folder = Path(temporary)
                single, environment = plans(name, window=window, extra_objects=objects)
                atomic_json(folder / 'scene_plan.json', environment)
                original = (folder / 'scene_plan.json').read_bytes()
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        build(single, folder)
                except (ValueError, RuntimeError) as error:
                    assert any(term in str(error) for term in reasons), f'拒绝原因与请求几何无关：{error}'
                    assert (folder / 'scene_plan.json').read_bytes() == original, '拒绝前修改了用户尺寸或位置'
                    quality_path = folder / 'quality.json'
                    if quality_path.exists():
                        assert not json.loads(quality_path.read_text())['passed']
                    return {'rejected': True, 'reason': str(error), 'source_scene_plan_unchanged': True}
                raise AssertionError('不可行请求没有拒绝；不得靠改尺寸、越墙或地下通行生成')
        check(name, refusal)

    check('fixed_frame_and_sill_anchors', anchor_invariance)
    report['passed'] = all(item['passed'] for item in report['checks'])
    report['count'] = len(report['checks'])
    report['passed_count'] = sum(item['passed'] for item in report['checks'])
    report['engine_sources_sha256'] = {
        name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
        for name in ('window_vault_scene.py', 'scene_objects.py', 'scene_plan.py', 'vault_pose.py')
    }
    atomic_json(REPORT, report)
    print('WINDOW_VAULT_SUMMARY', report['passed_count'], '/', report['count'], flush=True)
    if not report['passed']:
        raise RuntimeError('Window vault regression failed: ' + ', '.join(
            item['case'] for item in report['checks'] if not item['passed']))


if __name__ == '__main__':
    main()
