"""Actual original-rig gait, foot support, outside layers, other actors/reload."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))


def main():
    import bpy
    import numpy as np
    from mathutils import Vector, Quaternion
    import duel_plan
    from agent_character import actor_ensure, resolve_actor, scene_actor_facts, binding_facts
    from agent_motion_controls import read_pose
    from agent_motion_timeline import register_event, motion_commit, timeline_info
    from agent_motion_quality import animation_fingerprint
    from agent_locomotion import apply_locomotion

    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default=str(PROJECT / 'reports/agent_locomotion_validation_20261014.json'))
    parser.add_argument('--output', help='Save the real scene here and print checks without writing a report')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    report_path = Path(args.report).resolve()
    artifacts = Path(args.output).resolve() if args.output else report_path.with_suffix('')
    artifacts.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    temporary = tempfile.TemporaryDirectory(prefix='bamo_gait_adaptation_')
    duel_plan.JOBS = Path(temporary.name)
    job = 'registered_gait_proof'
    folder = duel_plan.JOBS / job
    folder.mkdir()
    (folder / 'request.json').write_text(json.dumps({'prompt': '原机器人走跑刹停，保留他人及窗口外动作'}), encoding='utf-8')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 150
    scene.render.fps, scene.render.fps_base = 30, 1
    actor_ensure(job, 'A', yaw=math.pi / 2)
    actor_ensure(job, 'B', position=(-2, 2, 0), yaw=.3)
    a, b = resolve_actor(job, 'A'), resolve_actor(job, 'B')

    def smooth(t):
        t = max(0, min(1, t))
        return t * t * (3 - 2 * t)

    def speed(t):
        if t < 1:
            return smooth(t / .4)
        if t < 1.5:
            return 1 + smooth((t - 1) / .5)
        if t < 3:
            return 2.
        if t < 3.6:
            return 2 * (1 - (t - 3) / .6)
        return 0.

    path, distance, previous_speed = [], 0., 0.
    for index in range(120):
        t = index / 30
        velocity = speed(t)
        if index:
            distance += (previous_speed + velocity) / 60
        path.append({'frame': index + 16, 'root': [distance, 0, 0], 'yaw': math.pi / 2,
                     'gait': 'walk' if t < 1 else ('run' if t <= 3.6 else 'idle')})
        previous_speed = velocity
    for actor_id, item in (('A', a), ('B', b)):
        root_keys = ([{'frame': 1, 'root': [0, 0, 0]}, {'frame': 16, 'root': [0, 0, 0]},
                      {'frame': 135, 'root': path[-1]['root']}, {'frame': 150, 'root': path[-1]['root']}]
                     if actor_id == 'A' else [{'frame': 1, 'root': [-2, 2, 0]}, {'frame': 150, 'root': [-2, 2, 0]}])
        register_event(job, actor_id, 'original_idle', {
            'source': 'library', 'motion_id': 'Idle', 'start_frame': 1, 'end_frame': 150,
            'root_strategy': 'trajectory', 'root_path': root_keys, 'loop': True})
        pose = read_pose(item['armature'])
        source_rotation = Quaternion(pose['mixamorig:RightArm']['rotation_quaternion'])
        register_event(job, actor_id, 'existing_half_weight_arm_layer', {
            'source': 'procedural', 'start_frame': 1, 'end_frame': 150, 'mask': ['right_arm'],
            'root_strategy': 'hold', 'weight': .35, 'priority': 5,
            'keyframes': [{'frame': 1, 'pose': {'mixamorig:RightArm': {'rotation_quaternion': list(source_rotation)}}},
                          {'frame': 150, 'pose': {'mixamorig:RightArm': {'rotation_quaternion': list(source_rotation @ Quaternion((0, 0, 1), .65))}}}]})
        motion_commit(job, actor_id)

    foot_indices = {}
    for obj in a['meshes']:
        names = {group.index: group.name for group in obj.vertex_groups}
        foot_indices[obj.name] = {side: np.asarray([vertex.index for vertex in obj.data.vertices
            if any(names[g.group].startswith('mixamorig:' + side) and
                   any(part in names[g.group] for part in ('Foot', 'Toe')) and g.weight >= .3
                   for g in vertex.groups)], dtype=np.int64) for side in ('Left', 'Right')}

    def snapshot(item):
        graph = bpy.context.evaluated_depsgraph_get()
        arm = item['armature'].evaluated_get(graph)
        bones = np.asarray([list(row) for bone in arm.pose.bones for row in arm.matrix_world @ bone.matrix])
        meshes = {}
        for obj in item['meshes']:
            eo = obj.evaluated_get(graph)
            mesh = eo.to_mesh()
            try:
                co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
                mesh.vertices.foreach_get('co', co)
                matrix = np.asarray(eo.matrix_world, dtype=np.float64)
                meshes[obj.name] = co.reshape((-1, 3)) @ matrix[:3, :3].T + matrix[:3, 3]
            finally:
                eo.to_mesh_clear()
        return {'bones': bones, 'root': np.asarray(item['root'].evaluated_get(graph).matrix_world), 'meshes': meshes}

    def difference(expected, actual):
        return max(float(np.max(np.abs(expected['bones'] - actual['bones']))),
                   float(np.max(np.abs(expected['root'] - actual['root']))),
                   max(float(np.max(np.abs(vertices - actual['meshes'][name]))) for name, vertices in expected['meshes'].items()))

    before_other, before_outside = {}, {}
    for frame in range(1, 151):
        scene.frame_set(frame)
        before_other[frame] = snapshot(b)
        if not 16 <= frame <= 135:
            before_outside[frame] = snapshot(a)
    initial_binding = {actor_id: binding_facts(item['meshes'], item['armature'])['signature'] for actor_id, item in (('A', a), ('B', b))}
    original_skin_objects = {obj.name: obj.data.as_pointer() for item in (a, b) for obj in item['meshes']}
    other_animation = [animation_fingerprint(obj) for obj in (b['armature'], b['root'])]
    scene.frame_set(8, subframe=.25)
    print('GAIT_APPLY_START', flush=True)
    result = apply_locomotion(job, 'A', path, event_id='walk_run_stop')
    print('GAIT_APPLY_FINISHED', flush=True)
    restored_frame = scene.frame_current == 8 and abs(scene.frame_subframe - .25) < 1e-7
    event = next(event for event in timeline_info(job, 'A')['events'] if event['event_id'] == 'walk_run_stop')
    records = {record['frame']: record for record in event['locomotion_measurements']}
    errors, maximum_other, maximum_outside = [], 0., 0.
    maximum_toe_anchor_error, maximum_locked_slip = 0., 0.
    previous_locks, actual = {}, {}
    foot_samples, actual_roots = [], []
    for frame in range(1, 151):
        scene.frame_set(frame)
        current_other = snapshot(b)
        maximum_other = max(maximum_other, difference(before_other[frame], current_other))
        current = snapshot(a)
        if frame in before_outside:
            maximum_outside = max(maximum_outside, difference(before_outside[frame], current))
        actual[frame] = current
        if frame not in records:
            continue
        arm = a['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        record = records[frame]
        foot = {'frame': frame, 'skin_min_z_m': {}, 'toes': {}, 'locks': record['toe_stance']}
        current_locks = {}
        for side in ('Left', 'Right'):
            vertices = np.concatenate([current['meshes'][obj.name][foot_indices[obj.name][side]] for obj in a['meshes']])
            foot['skin_min_z_m'][side] = float(vertices[:, 2].min())
            toe = arm.matrix_world @ arm.pose.bones['mixamorig:' + side + 'ToeBase'].head
            foot['toes'][side] = list(toe)
            key = 'A:' + side + 'ToeBase'
            if key in record['toe_stance']:
                goal = Vector(record['toe_stance'][key])
                maximum_toe_anchor_error = max(maximum_toe_anchor_error, (toe - goal).length)
                if key in previous_locks and (goal - previous_locks[key][0]).length < 1e-5:
                    maximum_locked_slip = max(maximum_locked_slip, (toe - previous_locks[key][1]).length)
                current_locks[key] = (goal, toe.copy())
        previous_locks = current_locks
        foot_samples.append(foot)
        actual_roots.append(Vector(current['root'][:3, 3]))
    speeds = [(right - left).length * 30 for left, right in zip(actual_roots, actual_roots[1:])]
    boundary_speed = speeds[29]
    if maximum_other > 1e-5: errors.append('Other actor actual motion changed')
    if maximum_outside > 3e-5: errors.append('Outside-window evaluated bones/root/skin changed')
    if maximum_toe_anchor_error > .015: errors.append('Actual toe missed declared world support anchor')
    if maximum_locked_slip > .015: errors.append('Actual planted toe slipped')
    terminal = foot_samples[-3:]
    if any(len(item['locks']) != 2 for item in terminal): errors.append('Final 3 frames lack both planted feet')
    if any(not -.025 <= z <= .03 for item in terminal for z in item['skin_min_z_m'].values()): errors.append('Actual terminal foot skin is not grounded')
    terminal_slip = max((Vector(right['toes'][side]) - Vector(left['toes'][side])).length
                        for left, right in zip(terminal, terminal[1:]) for side in ('Left', 'Right'))
    if terminal_slip > .01: errors.append('Terminal toes continue to move')
    if boundary_speed <= .2: errors.append('Walk/run boundary inserted a stop')
    if not restored_frame: errors.append('Scene frame/subframe not restored')
    if other_animation != [animation_fingerprint(obj) for obj in (b['armature'], b['root'])]: errors.append('Other actor Actions changed')
    if any(bpy.data.objects[name].data.as_pointer() != pointer for name, pointer in original_skin_objects.items()): errors.append('Original skin data replaced')
    if any(binding_facts(item['meshes'], item['armature'])['signature'] != initial_binding[actor_id] for actor_id, item in (('A', a), ('B', b))): errors.append('Original Beta binding changed')
    if result['unreachable_foot_targets']: errors.append('Foot goals exceeded nonstretching leg reach')

    # Same-id repair must replace exactly one trajectory, while retaining the
    # actual weighted outside animation and the other actor's complete Action.
    first_event_count = len(timeline_info(job, 'A')['events'])
    second = apply_locomotion(job, 'A', path, event_id='walk_run_stop')
    same_id_error = 0.
    for frame in range(1, 151):
        scene.frame_set(frame)
        same_id_error = max(same_id_error, difference(actual[frame], snapshot(a)))
    if len(timeline_info(job, 'A')['events']) != first_event_count: errors.append('Same event id created duplicate segments')
    if same_id_error > 3e-5: errors.append('Identical same-id repair changed actual motion')

    # Unsupported transform diagnoses happen before helper controls/events mutate.
    pre_failure_actions = [animation_fingerprint(obj) for obj in (a['armature'], a['root'])]
    pre_failure_events = a['root'].get('wm_motion_timeline_v1')
    pre_failure_objects = set(bpy.data.objects)
    a['root'].scale = (2, 2, 2)
    rejected_scale = False
    try:
        apply_locomotion(job, 'A', path)
    except ValueError as error:
        rejected_scale = 'unit actor-root scale' in str(error)
    finally:
        a['root'].scale = (1, 1, 1)
    if not rejected_scale or pre_failure_events != a['root'].get('wm_motion_timeline_v1') or pre_failure_objects != set(bpy.data.objects) or pre_failure_actions != [animation_fingerprint(obj) for obj in (a['armature'], a['root'])]: errors.append('Transform rejection mutated animation/registry/objects')

    saved = artifacts / 'registered_beta_walk_run_brake.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    saved_actual = {}
    for frame in range(1, 151):
        scene.frame_set(frame)
        saved_actual[frame] = {'A': snapshot(a), 'B': snapshot(b)}
    bpy.ops.wm.open_mainfile(filepath=str(saved), use_scripts=False)
    scene = bpy.context.scene
    a, b = resolve_actor(job, 'A'), resolve_actor(job, 'B')
    reload_error = 0.
    for frame in range(1, 151):
        scene.frame_set(frame)
        for actor_id, item in (('A', a), ('B', b)):
            reload_error = max(reload_error, difference(saved_actual[frame][actor_id], snapshot(item)))
    if reload_error > 1e-6: errors.append('Saved actual full motion changed after reload')
    identity = scene_actor_facts(job)
    if not identity['identity_verified']: errors.append('Reloaded original-skin identity failed')
    report = {'passed': not errors, 'errors': errors, 'checks': {
        'original_beta_identity': identity, 'library_source_records': result['source_records'],
        'controller': result['controller'], 'path_frames': len(path), 'bake_range': result['actual_bake_range'],
        'preserved_outside_ranges': result['preserved_outside_ranges'],
        'other_actor_all_frame_bone_root_skin_max_error': maximum_other,
        'outside_all_bone_root_skin_max_error': maximum_outside,
        'maximum_actual_toe_support_error_m': maximum_toe_anchor_error,
        'maximum_consecutive_planted_toe_slip_m': maximum_locked_slip,
        'terminal_frames': terminal, 'terminal_max_toe_slip_m': terminal_slip,
        'walk_run_boundary_speed_m_s': boundary_speed,
        'maximum_root_acceleration_m_s2': max(abs(right - left) * 30 for left, right in zip(speeds, speeds[1:])),
        'same_event_id_repair_max_error': same_id_error, 'same_event_id_no_duplicates': len(timeline_info(job, 'A')['events']) == first_event_count,
        'unsupported_scale_rejected_without_mutation': rejected_scale,
        'all150frames_allbones_original_skin_reload_max_error': reload_error,
        'unreachable_targets': result['unreachable_foot_targets']},
        'artifacts': {'scene': str(saved), 'scene_sha256': hashlib.sha256(saved.read_bytes()).hexdigest()},
        'elapsed_s': time.monotonic() - started,
        'scope': 'Real registered original Beta, 120-frame walk/run/brake/settle, weighted outside motion and other actor preservation, real skin/toe support and saved full-channel reload. No rendered-video or arbitrary-motion naturalness certification.'}
    if not args.output:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('REGISTERED_GAIT', json.dumps({'passed': report['passed'], 'errors': errors, 'scene': str(saved),
        'outside_error':maximum_outside,'other_actor_error':maximum_other,'same_id_error':same_id_error,
        'toe_anchor_error_m':maximum_toe_anchor_error,'terminal_slip_m':terminal_slip,'reload_error':reload_error}), flush=True)
    temporary.cleanup()
    if not report['passed']:
        raise RuntimeError('Registered Beta locomotion regression failed')


if __name__ == '__main__':
    main()
