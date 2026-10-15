"""Optional calibrated floor gait on an existing registered Beta character.

This component consumes an explicit trajectory, not the user's natural-language
prompt. Other movement/body behavior remains available through the agent's bpy
and timeline APIs. No visible rig, skin, rest bone, or mesh binding is created.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

SOURCE_IDS = {'guard': 'mixamo.basic.idle', 'walk': 'mixamo.basic.walk',
              'run': 'mixamo.basic.run'}


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite')
    return float(value)


def _path(path, scene):
    """Dense integer samples are deliberate; never infer a missing trajectory."""
    if not isinstance(path, (list, tuple)) or len(path) < 2:
        raise ValueError('path needs at least two consecutive frame samples')
    result = []
    for item in path:
        if not isinstance(item, dict):
            raise ValueError('Each path sample needs frame/root/yaw/gait')
        frame = _number(item.get('frame'), 'path.frame')
        if frame != int(frame):
            raise ValueError('path.frame must be an integer')
        root = item.get('root')
        if not isinstance(root, (list, tuple)) or len(root) != 3:
            raise ValueError('path.root needs [x,y,z] in world metres')
        root = [_number(value, 'path.root') for value in root]
        if abs(root[2]) > 1e-5:
            raise ValueError('This optional gait component uses a flat world-Z=0 floor; use other bpy motion tools for elevation')
        gait = item.get('gait')
        if gait not in ('walk', 'run', 'idle'):
            raise ValueError('This gait component accepts walk/run/idle trajectory phases; this is not a prompt vocabulary restriction')
        sample = {'frame': int(frame), 'root': root,
                  'yaw': _number(item.get('yaw'), 'path.yaw'), 'gait': gait}
        if result:
            if sample['frame'] != result[-1]['frame'] + 1:
                raise ValueError('path samples must cover each consecutive integer frame')
            if gait == 'idle' and math.dist(root, result[-1]['root']) > 1e-6:
                raise ValueError('A translating brake phase still needs walk/run gait; idle cannot slide planted feet')
        result.append(sample)
    if result[0]['frame'] < scene.frame_start or result[-1]['frame'] > scene.frame_end:
        raise ValueError('Set the scene duration/frame range before applying this trajectory')
    return result


def _validate_root(root):
    from mathutils import Vector
    if root.parent is not None:
        raise ValueError('This optional gait adapter currently requires an unparented actor root; parent-space conversion is not implemented')
    if max(abs(float(value) - 1) for value in root.scale) > 1e-6:
        raise ValueError('This optional gait adapter currently requires unit actor-root scale; the original FBX armature .01 scale is preserved')
    up = root.matrix_world.to_quaternion() @ Vector((0, 0, 1))
    if (up - Vector((0, 0, 1))).length > 1e-5:
        raise ValueError('This optional flat-floor gait adapter requires an upright actor root')


def _sources(armature, fps):
    from mathutils import Vector, Quaternion
    from agent_motion import prepare_motion, sample_motion
    sources = SimpleNamespace(ends={}, poses={}, root_motion={}, metadata={})

    def tuples(pose):
        return {name: (Vector(channels['location']), Quaternion(channels['rotation_quaternion']),
                       Vector(channels['scale'])) for name, channels in pose.items()}

    def sample(name, frame):
        seconds = min(sources.metadata[name]['duration_s'], max(0., (frame - 1) / fps))
        return tuples(sample_motion(armature, SOURCE_IDS[name], seconds,
                                    root_mode='extract_planar')['pose'])

    sources.sample = sample
    for name, motion_id in SOURCE_IDS.items():
        facts = prepare_motion(armature, motion_id)
        # Contact phases were measured on precisely these native cycles.
        if name in ('walk', 'run'):
            expected = 30 if name == 'walk' else 22
            span = facts['source_frame_range'][1] - facts['source_frame_range'][0]
            if abs(span - expected) > 1e-5 or abs(facts['key_timebase_fps'] - 30) > 1e-5:
                raise ValueError('Calibrated footfall phases no longer match the actual ' + name + ' source')
        sources.metadata[name] = facts
        sources.ends[name] = round(facts['duration_s'] * fps) + 1
        sources.poses[name] = [sample(name, 1), sample(name, sources.ends[name])]
        first = sample_motion(armature, motion_id, 0, root_mode='extract_planar')
        last = sample_motion(armature, motion_id, facts['duration_s'], root_mode='extract_planar')
        sources.root_motion[name] = [Vector(first['root']), Vector(last['root'])]
    return sources


def _constraint_state(armature):
    return [(bone, constraint, constraint.mute, constraint.influence)
            for bone in armature.pose.bones for constraint in bone.constraints]


def _restore_constraints(state):
    for bone, constraint, muted, influence in state:
        constraint.mute, constraint.influence = muted, influence


def _animation_state(obj):
    ad = obj.animation_data
    return {'exists': ad is not None, 'action': ad.action if ad else None,
            'slot': ad.action_slot if ad and ad.action else None,
            'rotation_mode': obj.rotation_mode}


def _restore_animation(obj, state):
    if state['exists']:
        obj.animation_data_create()
        obj.animation_data.action = state['action']
        if state['action'] and state['slot']:
            obj.animation_data.action_slot = state['slot']
    else:
        obj.animation_data_clear()
    obj.rotation_mode = state['rotation_mode']


def _capture_outside(actor, first, last, event_id, priority):
    import bpy
    from agent_motion_controls import read_pose
    scene = bpy.context.scene
    from agent_motion_timeline import _actual_animation_ranges
    ranges = [(scene.frame_start, scene.frame_end), *_actual_animation_ranges(actor)]
    begin = math.floor(min(pair[0] for pair in ranges))
    finish = math.ceil(max(pair[1] for pair in ranges))
    events = []
    for suffix, start, end in (('before', begin, first - 1), ('after', last + 1, finish)):
        if start > end:
            continue
        keys = []
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            root = actor['root'].evaluated_get(bpy.context.evaluated_depsgraph_get())
            keys.append({'frame': frame, 'interpolation': 'linear',
                         'pose': read_pose(actor['armature'], evaluated=True),
                         'root': list(root.location),
                         'root_rotation_quaternion': list(root.matrix_basis.to_quaternion()),
                         'root_scale': list(root.scale)})
        events.append({'event_id': event_id + '__outside_' + suffix, 'source': 'procedural',
                       'start_frame': start, 'end_frame': end, 'root_strategy': 'trajectory',
                       'keyframes': keys, 'priority': priority, 'capture': 'evaluated_final_channels',
                       'preservation_reason': 'Exact existing evaluated motion outside this locomotion trajectory'})
    return events


def apply_locomotion(job_id, actor_id, path, event_id='locomotion_base', *, priority=None,
                     intent=None):
    """Bake a complete walk/run/brake/settle trajectory onto the original rig.

    ``path`` is dense ``{frame,root:[world metres],yaw:radians,gait}`` data.
    A moving braking phase uses walk/run; zero-speed idle continues foot settling.
    One gait clock spans the whole path. Source IDs are internal calibration
    choices, not user-action restrictions. Existing animation must already have
    a timeline commit (or be explicitly captured first). Outside-window actual
    motion and other actors are preserved. Saved full-channel poses have no
    dependency on Python caches, a generated helper rig, or live IK constraints.
    """
    import bpy
    from mathutils import Vector, Quaternion, Euler
    from agent_character import resolve_actor
    from agent_motion_controls import (CONTROL_STORE, controls_ensure, set_pose, read_pose,
                                       recipe_pose_adapter, mute_controls, _apply_targets_resolved)
    from agent_motion_timeline import (STORE, _read, _require_registered_animation,
                                       register_event, motion_commit)
    from locomotion_gait import LocomotionGait
    from duel_scene import Actor

    scene = bpy.context.scene
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError('event_id must be stable nonempty text')
    path = _path(path, scene)
    actor = resolve_actor(job_id, actor_id)
    arm, root = actor['armature'], actor['root']
    _validate_root(root)
    store = _read(root)
    _require_registered_animation(actor, store)
    previous_max = max((float(event.get('priority', 0)) for event in store.get('events', [])), default=0)
    chosen_priority = previous_max + 1 if priority is None else _number(priority, 'priority')
    if chosen_priority <= previous_max:
        raise ValueError('Locomotion final-channel priority must supersede existing layers in its window')
    fps = scene.render.fps / scene.render.fps_base
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('Scene FPS must be positive')
    original_frame, original_subframe = scene.frame_current, scene.frame_subframe
    original_objects = set(bpy.data.objects)
    original_constraints = _constraint_state(arm)
    old_control_store = root.get(CONTROL_STORE)
    old_timeline = root.get(STORE)
    animations = {obj: _animation_state(obj) for obj in (arm, root)}
    original_pose = read_pose(arm)
    original_root_matrix = root.matrix_basis.copy()
    first, last = path[0]['frame'], path[-1]['frame']
    keys, records, target_failures = [], [], []
    committed = False
    try:
        sources = _sources(arm, fps)
        source_records = [{**facts, 'role': name, 'source_window_seconds': [0., facts['duration_s']]}
                          for name, facts in sources.metadata.items()]
        outside = _capture_outside(actor, first, last, event_id, chosen_priority)
        scene.frame_set(first)
        for obj in (arm, root):
            if obj.animation_data:
                obj.animation_data.action = None
        for bone in arm.pose.bones:
            for constraint in bone.constraints:
                constraint.mute = True
        root.rotation_mode = 'QUATERNION'
        root.location = path[0]['root']
        root.rotation_quaternion = Quaternion((0, 0, 1), path[0]['yaw'])
        root.scale = (1, 1, 1)
        set_pose(arm, recipe_pose_adapter(sources.sample('guard', 1)))
        bpy.context.view_layer.update()
        info = controls_ensure(job_id, actor_id)
        view = SimpleNamespace(id=actor_id, arm=arm, meshes=actor['meshes'],
                               root=SimpleNamespace(location=Vector(path[0]['root']),
                                                    rotation_euler=Euler((0, 0, path[0]['yaw']))),
                               forward=Vector((math.sin(path[0]['yaw']), -math.cos(path[0]['yaw']), 0)),
                               lateral=Vector((math.cos(path[0]['yaw']), math.sin(path[0]['yaw']), 0)),
                               pole_directions={})
        view.set_pose = lambda pose: set_pose(arm, recipe_pose_adapter(pose))
        gait = LocomotionGait(view, sources, fps=fps)
        distance = 0.
        previous = path[0]
        for index, sample in enumerate(path):
            frame = sample['frame']
            scene.frame_set(frame)
            mute_controls(actor, True)
            delta = (Vector(sample['root']) - Vector(previous['root'])).xy.length if index else 0.
            yaw_delta = math.atan2(math.sin(sample['yaw'] - previous['yaw']),
                                   math.cos(sample['yaw'] - previous['yaw'])) if index else 0.
            turn_distance = abs(yaw_delta) * .25
            distance += delta
            previously_moving = gait.active
            pose = gait.pose(delta * fps, sample['gait'], delta, turn_distance, dt=1 / fps)
            if index and previously_moving and not gait.active:
                # Dense path deltas describe the elapsed interval, rather than
                # an instantaneous velocity at its beginning. The first zero
                # delta has already held the root still for one full interval;
                # count that actual time toward lowering the last swing foot.
                # Keep the calibrated .28-second settle duration unchanged.
                gait.stop_age = 1 / fps
            view.set_pose(pose)
            root.location = sample['root']
            root.rotation_quaternion = Quaternion((0, 0, 1), sample['yaw'])
            root.scale = (1, 1, 1)
            view.root.location = Vector(sample['root'])
            view.root.rotation_euler.z = sample['yaw']
            view.forward = Vector((math.sin(sample['yaw']), -math.cos(sample['yaw']), 0))
            view.lateral = Vector((math.cos(sample['yaw']), math.sin(sample['yaw']), 0))
            bpy.context.view_layer.update()
            record = {**sample, 'speed_m_s': delta * fps, 'distance_m': distance}
            feet = gait.feet(record)
            targets = {key: {'position': list(feet['desired'][key]),
                             'rotation_quaternion': list(feet['rotations'][key]),
                             'influence': feet['influences'][key],
                             'pole': list(Actor.pole_location(view, key.removesuffix('Foot'),
                                                            'Leg', 'UpLeg', feet['desired'][key]))}
                       for key in ('LeftFoot', 'RightFoot')}
            applied = _apply_targets_resolved(actor, info, targets, frame, 1)
            bpy.context.view_layer.update()
            keys.append({'frame': frame, 'interpolation': 'linear', 'pose': read_pose(arm, evaluated=True),
                         'root': list(root.location), 'root_rotation_quaternion': list(root.rotation_quaternion),
                         'root_scale': list(root.scale)})
            for target in applied['targets']:
                if not target['reachable']:
                    target_failures.append({'frame': frame, **target})
            record['actual_toes_world_m'] = {side: list(arm.matrix_world @ arm.pose.bones['mixamorig:' + side + 'ToeBase'].head)
                                             for side in ('Left', 'Right')}
            record['actual_foot_skin_min_z_m'] = {side: gait._skin_min(side) for side in ('Left', 'Right')}
            records.append(record)
            previous = sample
        # Restore pre-generation ownership before asking the timeline to mutate.
        for obj, state in animations.items():
            _restore_animation(obj, state)
        _restore_constraints(original_constraints)
        mute_controls(actor, True)
        root.matrix_basis = original_root_matrix
        set_pose(arm, original_pose)
        scene.frame_set(original_frame, subframe=original_subframe)
        from agent_motion import prepare_motion
        for name, expected in sources.metadata.items():
            current = prepare_motion(arm, SOURCE_IDS[name])
            if current['source_sha256'] != expected['source_sha256'] or current['source_frame_range'] != expected['source_frame_range']:
                raise ValueError('Motion source changed while generating the gait; retry with reviewed source metadata')
        event = {'event_id': event_id, 'source': 'hybrid', 'motion_id': SOURCE_IDS['walk'],
                 'source_window': [0., sources.metadata['walk']['duration_s']],
                 'start_frame': first, 'end_frame': last, 'root_strategy': 'trajectory',
                 'keyframes': keys, 'priority': chosen_priority,
                 'final_pose_layers': True,
                 'intent': str(intent or 'Trajectory-driven calibrated walk/run/brake/settle'),
                 'locomotion_sources': source_records, 'locomotion_path': path,
                 'locomotion_controller': {'module': 'locomotion_gait', 'class': 'LocomotionGait',
                     'sha256': hashlib.sha256(Path(__file__).with_name('locomotion_gait.py').read_bytes()).hexdigest(),
                     'adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     'fps': fps, 'phase_continuous': True, 'root_space': 'world_metres',
                     'calibration_scope': 'Original Idle/Walk/Run, flat world-Z=0 floor, upright unparented unit-scale actor root'},
                 'locomotion_measurements': records}
        for preserved in outside:
            register_event(job_id, actor_id, preserved['event_id'], preserved)
        register_event(job_id, actor_id, event_id, event)
        result = motion_commit(job_id, actor_id, frame_start=first, frame_end=last)
        committed = True
        return {'ok': True, 'job_id': job_id, 'actor_id': actor_id, 'event_id': event_id,
                'path_range': [first, last], 'actual_bake_range': result['actual_bake_range'],
                'preserved_outside_ranges': [[item['start_frame'], item['end_frame']] for item in outside],
                'source_records': source_records, 'controller': event['locomotion_controller'],
                'unreachable_foot_targets': target_failures, 'skin_replaced': False,
                'gait_phase_start_end': [records[0]['gait_phase'], records[-1]['gait_phase']],
                'status': 'baked_full_channels_render_review_required', 'naturalness_verified': False,
                'terminal_foot_skin_min_z_m': records[-1]['actual_foot_skin_min_z_m']}
    finally:
        if not committed:
            for obj, state in animations.items():
                _restore_animation(obj, state)
            _restore_constraints(original_constraints)
            old_constraints = {constraint.as_pointer() for _, constraint, _, _ in original_constraints}
            for bone in arm.pose.bones:
                for constraint in list(bone.constraints):
                    if constraint.as_pointer() not in old_constraints:
                        bone.constraints.remove(constraint)
            for obj in set(bpy.data.objects) - original_objects:
                bpy.data.objects.remove(obj, do_unlink=True)
            for name, previous_value in ((CONTROL_STORE, old_control_store), (STORE, old_timeline)):
                if previous_value is None:
                    if name in root:
                        del root[name]
                else:
                    root[name] = previous_value
            root.matrix_basis = original_root_matrix
            set_pose(arm, original_pose)
        scene.frame_set(original_frame, subframe=original_subframe)
