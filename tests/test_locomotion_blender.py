"""Saved-scene movement regression with independent mesh and foot measurements.

Run with Blender. These checks evaluate actual baked animation after reloading
each .blend, and preserve every fixture for subsequent render/visual review.
They do not certify subjective motion quality or full physical collision.
"""
import contextlib
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import sys
import traceback

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from duel_plan import atomic_json
from duel_scene import point
from locomotion_scene import build
from locomotion_scene import SOURCE_HASHES
from scene_plan import validate_scene
from single_plan import validate_single

OUTPUT = ROOT / 'output/verification/locomotion_regression'
REPORT = ROOT / 'reports/locomotion_blender_regression_20261013.json'
ENGINE_FILES = ('locomotion_scene.py', 'locomotion_path.py', 'locomotion_gait.py',
                'locomotion_plan.py', 'single_plan.py', 'scene_objects.py')


def single_plan(name, duration, start, yaw, segments):
    return validate_single({'version': 1, 'duration_seconds': duration,
                            'animations': ['mixamo_fbx/basic/Idle.fbx',
                                           'mixamo_fbx/basic/Walk.fbx',
                                           'mixamo_fbx/basic/Run.fbx'],
                            'locomotion': {'version': 1, 'start_position': start,
                                           'start_yaw_deg': yaw, 'segments': segments}}, name)


def move(start, end, gait, points):
    return {'start': start, 'end': end, 'gait': gait, 'waypoints': points}


def angle_error(actual, target):
    return abs(math.atan2(math.sin(actual-target), math.cos(actual-target)))


def foot_indices(meshes):
    result = {}
    for obj in meshes:
        result[obj.name] = {}
        for side in ('Left', 'Right'):
            groups = {group.index for group in obj.vertex_groups
                      if group.name.startswith('mixamorig:' + side)
                      and any(part in group.name for part in ('Foot', 'Toe'))}
            result[obj.name][side] = [vertex.index for vertex in obj.data.vertices
                                     if any(group.group in groups and group.weight >= .3
                                            for group in vertex.groups)]
    return result


def evaluated_skin(meshes, graph, feet):
    world, by_side = [], {'Left': [], 'Right': []}
    for obj in meshes:
        evaluated = obj.evaluated_get(graph)
        local = np.empty((len(evaluated.data.vertices), 3), dtype=np.float64)
        evaluated.data.vertices.foreach_get('co', local.ravel())
        matrix = np.array(evaluated.matrix_world, dtype=np.float64)
        transformed = local @ matrix[:3, :3].T + matrix[:3, 3]
        world.append(transformed)
        for side in ('Left', 'Right'):
            indices = feet[obj.name][side]
            if indices:
                by_side[side].append(transformed[indices])
    return np.concatenate(world), {side: np.concatenate(arrays) for side, arrays in by_side.items()}


def reload_measurements(folder, single, case):
    bpy.ops.wm.open_mainfile(filepath=str(folder / 'scene.blend'))
    scene = bpy.context.scene
    root, arm = bpy.data.objects['A_Space'], bpy.data.objects['A_Rig']
    meshes = [obj for obj in scene.objects if obj.type == 'MESH' and
              (obj.get('actor_id') == 'A' or any(mod.type == 'ARMATURE' and mod.object == arm
                                               for mod in obj.modifiers))]
    assert meshes, '没有可独立检查的人物变形皮肤'
    assert scene.frame_start == 1 and scene.frame_end == single['frame_count']
    records = json.loads((folder / 'trajectory.json').read_text())['frames']
    assert len(records) == single['frame_count']
    graph = bpy.context.evaluated_depsgraph_get()
    feet = foot_indices(meshes)
    actual_roots, actual_yaws = [], []
    max_marker_error = max_lock_drift = 0.
    marker_count = 0
    support_counts = {'walk': 0, 'run': 0, 'turn': 0}
    lowest_skin = float('inf'); min_camera_margin = 1.
    previous_locks = {}
    final_feet = final_stance = None
    terminal_support = []
    previous_joint_rotations = {}
    max_joint_step_deg = 0.
    failures = []
    for record in records:
        frame = record['frame']; scene.frame_set(frame); bpy.context.view_layer.update()
        actual = root.matrix_world.translation.copy(); actual_roots.append(actual)
        actual_yaws.append(float(root.rotation_euler.z))
        for joint in ('Hips', 'Spine', 'LeftUpLeg', 'RightUpLeg', 'LeftLeg', 'RightLeg',
                      'LeftFoot', 'RightFoot'):
            rotation = arm.pose.bones['mixamorig:' + joint].matrix.to_quaternion()
            if joint in previous_joint_rotations:
                angle = math.degrees(2*math.acos(min(1., abs(rotation.dot(previous_joint_rotations[joint])))))
                max_joint_step_deg = max(max_joint_step_deg, angle)
                if angle > 65:
                    failures.append(f'第{frame}帧{joint}实际关节旋转跳变{angle:.2f}度')
            previous_joint_rotations[joint] = rotation.copy()
        reported = Vector(record['position'])
        if (actual - reported).length > .001:
            failures.append(f'第{frame}帧实际root与路线采样不一致')
        world, foot_skin = evaluated_skin(meshes, graph, feet)
        if case == 'box_detour':
            inside_box = ((np.abs(world[:, 0]) < .396) & (np.abs(world[:, 1]) < .396)
                          & (world[:, 2] > .004) & (world[:, 2] < .796))
            if np.any(inside_box):
                failures.append(f'第{frame}帧实际皮肤进入静态箱体，顶点数{np.count_nonzero(inside_box)}')
        minimum = float(world[:, 2].min()); lowest_skin = min(lowest_skin, minimum)
        if minimum < -.045:
            failures.append(f'第{frame}帧人物皮肤穿地{minimum:.4f}米')
        lower, upper = world.min(axis=0), world.max(axis=0)
        for xyz in itertools.product(*zip(lower, upper)):
            uv = world_to_camera_view(scene, scene.camera, Vector(xyz))
            margin = min(uv.x, 1-uv.x, uv.y, 1-uv.y)
            min_camera_margin = min(min_camera_margin, margin)
            if margin < .04 or uv.z <= 0:
                failures.append(f'第{frame}帧人物全身超出相机安全边距')
                break
        locks = record.get('toe_stance', {})
        if locks and record['gait'] in support_counts:
            support_counts[record['gait']] += 1
        current_locks = {}
        for key, target_raw in locks.items():
            short = key.split(':')[-1]
            actual_toe = point(arm, short)
            target = Vector(target_raw); error = (actual_toe-target).length
            max_marker_error = max(max_marker_error, error); marker_count += 1
            if error > .025:
                failures.append(f'第{frame}帧{short}实际支撑误差{error:.4f}米')
            if key in previous_locks:
                old_target, old_actual = previous_locks[key]
                if (target-old_target).length <= 1e-5:
                    drift = (actual_toe-old_actual).length
                    max_lock_drift = max(max_lock_drift, drift)
                    if drift > .02:
                        failures.append(f'第{frame}帧{short}锁定时滑动{drift:.4f}米')
            current_locks[key] = (target.copy(), actual_toe.copy())
        previous_locks = current_locks
        if frame >= single['frame_count'] - 2:
            terminal_support.append({'frame': frame, 'toe_stance': sorted(locks),
                                     'foot_skin_min_z_m': {side: float(vertices[:, 2].min())
                                                          for side, vertices in foot_skin.items()},
                                     'actual_toes': {side: list(point(arm, side+'ToeBase'))
                                                     for side in ('Left', 'Right')}})
        if frame == single['frame_count']:
            final_feet = {side: float(vertices[:, 2].min()) for side, vertices in foot_skin.items()}
            final_stance = sorted(locks)
    destinations, turns = [], []
    for index, segment in enumerate(single['locomotion']['segments']):
        frame = min(single['frame_count'], max(1, round(segment['end']*30)+1))
        if segment['gait'] in ('walk', 'run'):
            target = Vector((*segment['waypoints'][-1], 0))
            error = (actual_roots[frame-1]-target).xy.length
            destinations.append({'segment_index': index, 'frame': frame, 'error_m': error})
            if error > .04:
                failures.append(f'第{index+1}段未准确到达末点，误差{error:.4f}米')
        elif segment['gait'] == 'turn':
            error = math.degrees(angle_error(actual_yaws[frame-1], math.radians(segment['end_yaw_deg'])))
            turns.append({'segment_index': index, 'frame': frame, 'error_deg': error})
            if error > 2:
                failures.append(f'第{index+1}段未完成指定转身，误差{error:.2f}度')
            first = max(0, round(segment['start']*30))
            turn_displacement = max((actual_roots[i]-actual_roots[first]).xy.length
                                    for i in range(first, frame))
            if turn_displacement > .005:
                failures.append(f'第{index+1}段原地转身期间root平移{turn_displacement:.4f}米')
    speeds = [(b-a).xy.length*30 for a, b in zip(actual_roots, actual_roots[1:])]
    yaw_rates = [math.degrees(angle_error(a, b))*30 for a, b in zip(actual_yaws, actual_yaws[1:])]
    accelerations = [abs(b-a)*30 for a, b in zip(speeds, speeds[1:])]
    if max(speeds, default=0) > 3.55:
        failures.append('实际root速度超过3.55米/秒')
    if max(yaw_rates, default=0) > 125:
        failures.append('实际root转向超过125度/秒')
    if max(accelerations, default=0) > 4.05:
        failures.append('实际root加速度超过4.05米/秒²')
    for side, minimum in final_feet.items():
        if minimum < -.045 or minimum > .025:
            failures.append(f'末帧{side}脚未落稳，实际皮肤最低点{minimum:.4f}米')
    if len(final_stance) != 2:
        failures.append(f'末帧并非双脚稳定支撑：{final_stance}')
    for state in terminal_support:
        if len(state['toe_stance']) != 2:
            failures.append(f"末3帧中第{state['frame']}帧并非双脚稳定支撑")
        if any(z < -.025 or z > .03 for z in state['foot_skin_min_z_m'].values()):
            failures.append(f"末3帧中第{state['frame']}帧脚底未落地")
    terminal_slip = max((Vector(b['actual_toes'][side])-Vector(a['actual_toes'][side])).length
                        for a, b in zip(terminal_support, terminal_support[1:])
                        for side in ('Left', 'Right'))
    if terminal_slip > .01:
        failures.append(f'末3帧实际脚趾滑动{terminal_slip:.4f}米')
    if not marker_count:
        failures.append('整段没有真实支撑脚锁定帧')
    boundary_speed = None
    if case == 'continuous_walk_run':
        at = round(single['locomotion']['segments'][0]['end']*30)
        boundary_speed = speeds[at-1]
        if boundary_speed <= .2:
            failures.append(f'走跑交界速度降为{boundary_speed:.4f}米/秒，出现多余停步')
        before, after = records[at-1], records[at]
        if abs(after['gait_cycle']+after['gait_phase']-before['gait_cycle']-before['gait_phase']) > .15:
            failures.append('走跑交界重启或跳变步相')
    if case == 'implicit_initial_turn':
        early = [(root-actual_roots[0]).xy.length for root, record in zip(actual_roots, records)
                 if record['seconds'] <= 1.4]
        if max(early) > .005:
            failures.append('初始反向朝向尚未安全转身就平移')
        if max(math.degrees(angle_error(yaw, actual_yaws[0])) for yaw in actual_yaws) < 170:
            failures.append('初始反向朝向没有完成约180度转身')
    if case == 'box_detour':
        lateral = max(abs(root.y) for root in actual_roots)
        if lateral < .8:
            failures.append('障碍箱路线没有真实绕行')
    measurements = {'root_start_m': list(actual_roots[0]), 'root_end_m': list(actual_roots[-1]),
                    'destinations': destinations, 'turns': turns,
                    'max_actual_speed_m_s': max(speeds, default=0),
                    'max_actual_acceleration_m_s2': max(accelerations, default=0),
                    'max_actual_yaw_rate_deg_s': max(yaw_rates, default=0),
                    'max_actual_joint_step_deg': max_joint_step_deg,
                    'minimum_deformed_skin_z_m': lowest_skin,
                    'minimum_full_body_camera_margin': min_camera_margin,
                    'toe_stance_markers_rechecked': marker_count, 'support_frames': support_counts,
                    'max_actual_toe_stance_error_m': max_marker_error,
                    'max_consecutive_locked_toe_slip_m': max_lock_drift,
                    'final_foot_skin_min_z_m': final_feet, 'final_toe_stance': final_stance,
                    'terminal_support': terminal_support, 'terminal_max_toe_slip_m': terminal_slip,
                    'walk_run_boundary_speed_m_s': boundary_speed,
                    'failures': list(dict.fromkeys(failures))[:40]}
    if failures:
        error = AssertionError('；'.join(measurements['failures']))
        error.case_details = {'reload_measurements': measurements}
        raise error
    return measurements


def fixtures():
    box_scene = {'version': 1, 'duration_seconds': 10, 'room': {'enabled': True},
                 'objects': [{'id': 'box_1', 'type': 'box', 'size': [.8, .8, .8], 'position': [0, 0, 0]}]}
    result = [
        ('slow_walk_stop', 5, [0, -1], 180, [
            move(0, 3.2, 'walk', [[0, -1], [0, .6]]), {'start': 3.2, 'end': 5, 'gait': 'idle'}], None),
        ('continuous_walk_run', 7, [0, -2], 180, [
            move(0, 2.5, 'walk', [[0, -2], [0, -.5]]),
            move(2.5, 5.5, 'run', [[0, -.5], [0, 2]]), {'start': 5.5, 'end': 7, 'gait': 'idle'}], None),
        ('explicit_turn_resume', 8, [0, -1], 180, [
            move(0, 2.5, 'walk', [[0, -1], [0, .4]]),
            {'start': 2.5, 'end': 4.3, 'gait': 'turn', 'end_yaw_deg': 90},
            move(4.3, 6.5, 'walk', [[0, .4], [1.6, .4]]), {'start': 6.5, 'end': 8, 'gait': 'idle'}], None),
        ('curved_waypoints', 8, [-1.5, -1.5], 135, [
            move(0, 6.8, 'walk', [[-1.5, -1.5], [-.5, -.5], [.7, -.4], [1.5, .8]]),
            {'start': 6.8, 'end': 8, 'gait': 'idle'}], None),
        ('box_detour', 10, [-1.8, 0], 90, [
            move(0, 8.5, 'walk', [[-1.8, 0], [1.8, 0]]), {'start': 8.5, 'end': 10, 'gait': 'idle'}], box_scene),
        ('implicit_initial_turn', 7, [0, -1], 0, [
            move(0, 6, 'walk', [[0, -1], [0, 1]]), {'start': 6, 'end': 7, 'gait': 'idle'}], None),
        ('movement_at_video_end', 4, [0, -1], 180, [move(0, 4, 'walk', [[0, -1], [0, 1]])], None),
    ]
    # Preserve the exact route/timing from the real natural-language job that
    # exposed a foot rotation jump after Run -> Idle -> 180-degree Turn.
    regression_plan = json.loads((ROOT/'output/jobs/add98168ac5e4d22/single_plan.json').read_text())
    route = regression_plan['locomotion']
    result.append(('natural_language_180_turn_chain', regression_plan['duration_seconds'],
                   route['start_position'], route['start_yaw_deg'], route['segments'], None))
    return result


def main():
    report = {'checks': [], 'scope': 'Actual saved-scene roots, deformed skin ground clearance, '
              'full-body camera margins, independent toe contact/slip, destinations and turns; '
              'no rendered-video or subjective motion-naturalness certification.',
              'engine_sources_sha256': SOURCE_HASHES}
    def check(name, callback):
        try:
            entry = {'case': name, 'passed': True, **callback()}
        except Exception as error:
            entry = {'case': name, 'passed': False, 'error': str(error), 'traceback': traceback.format_exc(),
                     **getattr(error, 'case_details', {})}
        report['checks'].append(entry)
        atomic_json(REPORT, report)
        print('LOCOMOTION_REGRESSION', name, 'PASS' if entry['passed'] else 'FAIL', flush=True)
        if not entry['passed']:
            print('LOCOMOTION_FAILURE', name, entry['error'], flush=True)
    for name, duration, start, yaw, segments, environment in fixtures():
        def success(name=name, duration=duration, start=start, yaw=yaw,
                    segments=segments, environment=environment):
            folder = OUTPUT/name; folder.mkdir(parents=True, exist_ok=True)
            if name == 'natural_language_180_turn_chain':
                source = json.loads((ROOT/'output/jobs/add98168ac5e4d22/single_plan.json').read_text())
                single = validate_single(source, name)
            else:
                single = single_plan(name, duration, start, yaw, segments)
            atomic_json(folder/'single_plan.json', single)
            if environment:
                atomic_json(folder/'scene_plan.json', validate_scene(environment, name, 1, duration))
            hashes = {filename: hashlib.sha256((folder/filename).read_bytes()).hexdigest()
                      for filename in ('single_plan.json', 'scene_plan.json') if (folder/filename).exists()}
            with contextlib.redirect_stdout(io.StringIO()):
                quality = build(single, folder)
            try:
                assert quality['engine_sources_sha256'] == SOURCE_HASHES, '场景来源指纹与加载模块不一致'
                measurements = reload_measurements(folder, single, name)
                assert quality['passed'], '实际场景测量通过但执行器几何检查拒绝，请查看quality.json'
                assert hashes == {filename: hashlib.sha256((folder/filename).read_bytes()).hexdigest()
                                  for filename in hashes}, '执行器修改原用户人物或场景计划'
            except Exception as error:
                error.case_details = {**getattr(error, 'case_details', {}), 'quality': quality,
                                      'fixture': str(folder.relative_to(ROOT))}
                raise
            return {'quality': quality, 'reload_measurements': measurements,
                    'fixture': str(folder.relative_to(ROOT)), 'source_plans_unchanged': True}
        check(name, success)
    barriers = [{'id': f'box_{i}', 'type': 'box', 'size': [1.2, 1.2, 1.2],
                 'position': [x, 0, 0]} for i, x in enumerate((-1.25, 0, 1.25), 1)]
    rejected = [
        ('reject_initial_in_box', 3, [0, 0], 90,
         [move(0, 2, 'walk', [[0, 0], [1.5, 0]])],
         {'objects': [{'id': 'box_1', 'type': 'box', 'size': [.8, .8, .8], 'position': [0, 0, 0]}]},
         ('起点', '障碍')),
        ('reject_unreachable_destination', 6, [0, -1.5], 180,
         [move(0, 5, 'walk', [[0, -1.5], [0, 1.5]])],
         {'room': {'enabled': True, 'size': [4, 4, 3]}, 'objects': barriers}, ('可通行', '路径')),
        ('reject_insufficient_time', 1, [0, -2], 180,
         [move(0, .5, 'run', [[0, -2], [0, 2]])], None, ('至少需要', '给定')),
    ]
    for name, duration, start, yaw, segments, environment, reasons in rejected:
        def refusal(name=name, duration=duration, start=start, yaw=yaw, segments=segments,
                    environment=environment, reasons=reasons):
            folder=OUTPUT/name; folder.mkdir(parents=True, exist_ok=True)
            atomic_json(folder/'requested_single_plan.json', {
                'version': 1, 'duration_seconds': duration,
                'animations': ['mixamo_fbx/basic/Idle.fbx', 'mixamo_fbx/basic/Walk.fbx',
                               'mixamo_fbx/basic/Run.fbx'],
                'locomotion': {'version': 1, 'start_position': start, 'start_yaw_deg': yaw,
                               'segments': segments}})
            try:
                single=single_plan(name, duration, start, yaw, segments)
                atomic_json(folder/'single_plan.json', single)
                if environment:
                    atomic_json(folder/'scene_plan.json', validate_scene(
                        {'version': 1, 'duration_seconds': duration, **environment}, name, 1, duration))
                with contextlib.redirect_stdout(io.StringIO()):
                    build(single, folder)
            except (ValueError, RuntimeError) as error:
                assert any(reason in str(error) for reason in reasons), f'拒绝原因与请求不可行性不符：{error}'
                assert not (folder/'scene.blend').exists(), '不可行请求仍保存成可渲染场景'
                return {'rejected': True, 'reason': str(error), 'fixture': str(folder.relative_to(ROOT))}
            raise AssertionError('不可行移动没有拒绝，可能越过障碍或篡改时长')
        check(name, refusal)
    current_hashes={name: hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
                    for name in SOURCE_HASHES}
    unchanged = current_hashes == SOURCE_HASHES
    report.update(passed=all(item['passed'] for item in report['checks']) and unchanged,
                  source_files_unchanged_during_regression=unchanged,
                  count=len(report['checks']),
                  passed_count=sum(item['passed'] for item in report['checks']),
                  current_engine_sources_sha256=current_hashes)
    atomic_json(REPORT, report)
    print('LOCOMOTION_REGRESSION_SUMMARY', report['passed_count'], '/', report['count'], flush=True)
    if not report['passed']:
        raise RuntimeError('Locomotion regression failed: '+', '.join(
            item['case'] for item in report['checks'] if not item['passed']))


if __name__ == '__main__':
    main()
