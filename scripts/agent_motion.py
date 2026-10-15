"""Sample existing motions onto a fixed Beta rig without replacing its skin.

Temporary imports are removed immediately; only plain pose samples are cached.
The final timeline owns animation and must bake these samples before saving.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
from agent_motion_catalog import asset_path, motion_info

_CACHE = {}
_ALIASES = {}
PREFIX = 'mixamorig:'


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(name + ' must be finite')
    return float(value)


def _rig_signature(arm):
    from agent_character import rig_signature
    return rig_signature(arm)


def _matrix_signature(matrix):
    return hashlib.sha256(json.dumps([[round(float(x), 6) for x in row] for row in matrix]).encode()).hexdigest()


def _validate_direct(source, target):
    source_bones, target_bones = source.data.bones, target.data.bones
    if set(source_bones.keys()) != set(target_bones.keys()):
        raise ValueError('FBX rig bone names differ; explicit retarget mapping is required')
    differences = []
    for bone in target_bones:
        other = source_bones[bone.name]
        parent = bone.parent.name if bone.parent else None
        other_parent = other.parent.name if other.parent else None
        error = max(abs(bone.matrix_local[i][j] - other.matrix_local[i][j]) for i in range(4) for j in range(4))
        if parent != other_parent or error > 1e-3 or any(
                getattr(bone, name) != getattr(other, name)
                for name in ('inherit_scale', 'use_inherit_rotation', 'use_local_location')):
            differences.append(bone.name)
    # The actor's parent/yaw/position is deliberately excluded.  Only the
    # imported armature's intrinsic orientation/units must match the source.
    transform_error = max(abs(source.matrix_world[i][j] - target.matrix_basis[i][j])
                          for i in range(3) for j in range(3))
    if differences or transform_error > 1e-5:
        raise ValueError('FBX rest/units are incompatible with fixed rig: ' + ', '.join(differences[:8]))
    return {'method': 'measured_same_rest_rig', 'source_rig_signature': _rig_signature(source),
            'target_rig_signature': _rig_signature(target), 'transform_error': transform_error,
            'mapped_bones': len(target_bones), 'unmapped_bones': []}


def _slot_for(action, armature):
    """Select the actual armature channelbag; reject ambiguous multi-slots."""
    slots = list(getattr(action, 'slots', []))
    if not slots:
        return None
    valid = []
    for slot in slots:
        paths = []
        for layer in getattr(action, 'layers', []):
            for strip in layer.strips:
                try:
                    bag = strip.channelbag(slot)
                except (AttributeError, RuntimeError, TypeError):
                    bag = None
                if bag:
                    paths.extend(curve.data_path for curve in bag.fcurves)
        bone_paths = [path for path in paths if path.startswith('pose.bones[')]
        if bone_paths and all(any(('pose.bones[' + json.dumps(name) + ']') in path
                                  for name in armature.pose.bones.keys()) for path in bone_paths):
            valid.append(slot)
    if len(valid) != 1:
        raise ValueError('Source Action has no unique compatible armature slot: ' + action.name)
    return valid[0]


def _bind(action, arm):
    arm.animation_data_create()
    for track in arm.animation_data.nla_tracks:
        track.mute = True
    arm.animation_data.action = action
    slot = _slot_for(action, arm)
    if slot is not None:
        arm.animation_data.action_slot = slot
    return slot


def _pose_dict(arm):
    pose = {}
    for bone in arm.pose.bones:
        loc, rot, scale = bone.matrix_basis.decompose()
        pose[bone.name] = {'location': list(loc), 'rotation_quaternion': list(rot.normalized()), 'scale': list(scale)}
    return pose


def _retarget_setup(source, target):
    from duel_motion_sources import bone_mapping
    from mathutils import Vector
    mapping = bone_mapping()
    required = ('root', 'pelvis', 'hand_l', 'hand_r', 'foot_l', 'foot_r')
    if any(name not in source.data.bones for name in required):
        raise ValueError('Registered Quaternius skeleton does not match its named mapping')
    missing = [name for name in target.data.bones.keys() if name not in mapping and name != PREFIX + 'HeadTop_End']
    if PREFIX + 'Hips' not in target.data.bones or missing:
        raise ValueError('Target is not the supported Beta rest skeleton; missing mapping: ' + ', '.join(missing))
    target_basis = target.matrix_basis.copy()
    source_basis = source.matrix_world.copy()
    target_hip = target_basis @ target.data.bones[PREFIX + 'Hips'].head_local
    source_hip = source_basis @ source.data.bones['pelvis'].head_local
    if source_hip.z <= 0 or target_hip.z <= 0:
        raise ValueError('Unsupported retarget rest-up axis/hip scale')
    return {
        'mapping': mapping, 'target_basis': target_basis,
        'target_inverse': target_basis.to_3x3().inverted(),
        'target_rotation': target_basis.to_quaternion(), 'source_basis': source_basis,
        'target_rest': {b.name: (target_basis @ b.matrix_local).to_quaternion() for b in target.data.bones},
        'source_rest': {b.name: (source_basis @ b.matrix_local).to_quaternion() for b in source.data.bones},
        'source_hip': source_hip, 'scale': target_hip.z / source_hip.z,
        'compatibility': {'method': 'quaternius_to_beta_rest_space', 'mapped_bones': len(mapping.keys() & set(target.data.bones.keys())),
                          'unmapped_bones': [name for name in target.data.bones.keys() if name not in mapping],
                          'source_rig_signature': _rig_signature(source), 'target_rig_signature': _rig_signature(target)},
    }


def _retarget_pose(source, target, setup, root_origin, extract_root):
    from mathutils import Vector
    world_root = source.matrix_world @ source.pose.bones['root'].head
    root = (world_root - root_origin) * setup['scale'] if extract_root else Vector()
    # Vertical root motion remains in the pelvis when only planar extraction is
    # requested, including jumps/falls.  Remove exactly what root owner receives.
    root.z = 0
    desired, pose = {}, {}
    ordered = sorted(target.data.bones, key=lambda bone: len(_ancestors(bone)))
    for bone in ordered:
        source_name = setup['mapping'].get(bone.name)
        rest = bone.matrix_local.to_quaternion()
        if source_name and source_name in source.pose.bones:
            rotation = (source.matrix_world @ source.pose.bones[source_name].matrix).to_quaternion()
            world = rotation @ setup['source_rest'][source_name].inverted() @ setup['target_rest'][bone.name]
            desired[bone.name] = setup['target_rotation'].inverted() @ world
        else:
            desired[bone.name] = (desired[bone.parent.name] @ bone.parent.matrix_local.to_quaternion().inverted() @ rest
                                  if bone.parent else rest)
        inherited = desired[bone.parent.name] @ bone.parent.matrix_local.to_quaternion().inverted() @ rest if bone.parent else rest
        loc = Vector()
        if bone.name == PREFIX + 'Hips':
            current = source.matrix_world @ source.pose.bones['pelvis'].head
            delta = (current - setup['source_hip']) * setup['scale'] - root
            loc = rest.inverted() @ (setup['target_inverse'] @ delta)
        pose[bone.name] = {'location': list(loc), 'rotation_quaternion': list((inherited.inverted() @ desired[bone.name]).normalized()),
                           'scale': [1., 1., 1.]}
    return pose, list(root)


def _ancestors(bone):
    result = []
    while bone.parent:
        bone = bone.parent
        result.append(bone)
    return result


def _import_samples(target, info):
    import bpy
    from mathutils import Vector
    scene = bpy.context.scene
    frame, subframe = scene.frame_current, scene.frame_subframe
    fps, fps_base = scene.render.fps, scene.render.fps_base
    selected = list(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    kinds = ('objects', 'collections', 'actions', 'meshes', 'armatures', 'materials', 'images', 'textures')
    before = {kind: set(getattr(bpy.data, kind)) for kind in kinds}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            if info['provider'] == 'fbx':
                bpy.ops.import_scene.fbx(filepath=str(asset_path(info['asset'])))
            elif info['provider'] == 'quaternius_glb':
                scene.render.fps, scene.render.fps_base = 30, 1.0
                bpy.ops.import_scene.gltf(filepath=str(asset_path(info['asset'])))
            else:
                raise ValueError('No source sampler registered for provider ' + info['provider'])
        imported = set(bpy.data.objects) - before['objects']
        arms = [obj for obj in imported if obj.type == 'ARMATURE']
        if len(arms) != 1:
            raise ValueError('Motion source must provide one unambiguous source armature')
        source = arms[0]
        source_actions = set(bpy.data.actions) - before['actions']
        if info['provider'] == 'fbx':
            action = source.animation_data.action if source.animation_data else None
            if action is None:
                raise ValueError('FBX source has no active bone animation')
            compatibility = _validate_direct(source, target)
            setup = None
        else:
            matches = [action for action in source_actions if action.name == info['clip'] or
                       (action.name.startswith(info['clip'] + '.') and action.name[len(info['clip']) + 1:].isdigit())]
            if len(matches) != 1:
                raise ValueError('Registered GLB named Action missing or ambiguous: ' + info['clip'])
            action = matches[0]
            setup = _retarget_setup(source, target)
            compatibility = setup['compatibility']
        slot = _bind(action, source)
        sample_fps = float(scene.render.fps / scene.render.fps_base)
        source_fps = info.get('source_fps') or sample_fps
        start, end = (float(value) for value in action.frame_range)
        if end <= start:
            raise ValueError('Source Action has no duration')
        # Imported FBX key time conversion uses scene.render.fps (not fps_base).
        # Use that actual key timebase; source_fps remains separate provenance.
        key_fps = float(scene.render.fps)
        frames = [start + i for i in range(math.floor(end - start) + 1)]
        if frames[-1] < end - 1e-7:
            frames.append(end)
        poses, roots, preserved = [], [], []
        origin = None
        for time in frames:
            scene.frame_set(math.floor(time), subframe=time - math.floor(time))
            bpy.context.view_layer.update()
            if setup:
                current_root = source.matrix_world @ source.pose.bones['root'].head
                if origin is None:
                    origin = current_root.copy()
                pose, root = _retarget_pose(source, target, setup, origin, True)
                unextracted, _ = _retarget_pose(source, target, setup, origin, False)
            else:
                hips = source.pose.bones.get(PREFIX + 'Hips')
                if hips is None or hips.parent is not None:
                    raise ValueError('Direct Beta sampler requires a root Hips bone')
                current_root = source.matrix_world @ hips.head
                if origin is None:
                    origin = current_root.copy()
                root_vector = Vector((current_root.x - origin.x, current_root.y - origin.y, 0.0))
                pose = _pose_dict(source)
                unextracted = json.loads(json.dumps(pose))
                local_delta = hips.bone.matrix_local.to_3x3().inverted() @ (source.matrix_world.to_3x3().inverted() @ root_vector)
                pose[hips.name]['location'] = list(Vector(pose[hips.name]['location']) - local_delta)
                root = list(root_vector)
            poses.append(pose)
            roots.append(root)
            preserved.append(unextracted)
        return {'poses': poses, 'preserved_poses': preserved, 'roots': roots, 'frames': frames,
                'source_frame_range': [start, end], 'source_fps': source_fps, 'key_fps': key_fps,
                'duration_s': (end - start) / key_fps,
                'source_action': action.name, 'source_slot': slot.identifier if slot else None,
                'compatibility': compatibility, 'retarget_scale': setup['scale'] if setup else None}
    finally:
        # Remove only datablocks introduced by this import, never generic
        # scene-wide orphans.  No imported Action survives the plain-data cache.
        for kind in kinds:
            collection = getattr(bpy.data, kind)
            for datablock in set(collection) - before[kind]:
                collection.remove(datablock, do_unlink=True)
        scene.render.fps, scene.render.fps_base = fps, fps_base
        scene.frame_set(frame, subframe=subframe)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            if obj.name in scene.objects:
                obj.select_set(True)
        if active and active.name in bpy.context.view_layer.objects:
            bpy.context.view_layer.objects.active = active


def _cached_key(target, motion_id):
    signature = _rig_signature(target), _matrix_signature(target.matrix_basis)
    alias = _ALIASES.get((motion_id, signature))
    if alias and alias in _CACHE:
        cached = _CACHE[alias]
        stat = asset_path(cached['info']['asset']).stat()
        if cached['source_stat'] == (stat.st_mtime_ns, stat.st_size):
            return alias
    return None


def prepare_motion(actor_arm, motion_id):
    """Import/sample once.  Return plain metadata, never a source robot object."""
    if getattr(actor_arm, 'type', None) != 'ARMATURE':
        raise ValueError('actor_arm must be the existing actor armature')
    key = _cached_key(actor_arm, motion_id)
    if key is None:
        info = motion_info(motion_id)
        key = (info['motion_id'], info['asset_sha256'], _rig_signature(actor_arm), _matrix_signature(actor_arm.matrix_basis))
        if key not in _CACHE:
            data = _import_samples(actor_arm, info)
            stat = asset_path(info['asset']).stat()
            _CACHE[key] = {'info': info, 'data': data, 'source_stat': (stat.st_mtime_ns, stat.st_size)}
        _ALIASES[(motion_id, (_rig_signature(actor_arm), _matrix_signature(actor_arm.matrix_basis)))] = key
    cached = _CACHE[key]
    data, info = cached['data'], cached['info']
    return {'motion_id': info['motion_id'], 'source_asset': info['asset'], 'source_sha256': info['asset_sha256'],
            'source_action': data['source_action'], 'source_slot': data['source_slot'],
            'source_fps': data['source_fps'], 'key_timebase_fps': data['key_fps'],
            'source_frame_range': data['source_frame_range'][:], 'duration_s': data['duration_s'],
            'loop_allowed': info.get('loop_allowed', False), 'compatibility': dict(data['compatibility']),
            'retarget_scale': data['retarget_scale'], 'root_space': 'actor_root_local_meters',
            'root_reference': 'source_clip_first_frame', 'skin_replaced': False,
            'quality_scope': 'Rig-compatible source sampling; contact and naturalness remain unverified'}


def sample_motion(actor_arm, motion_id, source_time_s, root_mode='extract_planar', source_window=None):
    """Interpolate a source time in seconds; do not loop or mutate the actor.

    ``source_window`` clamps absolute clip seconds, without adding an offset.
    Its root displacement still refers to clip start; the timeline subtracts
    the chosen window's first root before placing the actor.
    """
    from mathutils import Quaternion, Vector
    time = _finite(source_time_s, 'source_time_s')
    if root_mode not in ('extract_planar', 'preserve'):
        raise ValueError('root_mode must be extract_planar or preserve')
    provenance = prepare_motion(actor_arm, motion_id)
    data = _CACHE[_cached_key(actor_arm, motion_id)]['data']
    duration = data['duration_s']
    low, high = 0., duration
    if source_window is not None:
        if not isinstance(source_window, (list, tuple)) or len(source_window) != 2:
            raise ValueError('source_window must be [start_seconds,end_seconds]')
        low, high = [_finite(value, 'source_window') for value in source_window]
        if low < 0 or high < low or high > duration + 1e-6:
            raise ValueError('source_window lies outside the actual source duration')
    time = min(high, max(low, time))
    source_frame = data['source_frame_range'][0] + time * data['key_fps']
    offset = source_frame - data['frames'][0]
    index = min(len(data['frames']) - 1, max(0, math.floor(offset)))
    following = min(index + 1, len(data['frames']) - 1)
    span = data['frames'][following] - data['frames'][index]
    fraction = min(1., max(0., (source_frame - data['frames'][index]) / span)) if span else 0.
    poses = data['poses'] if root_mode == 'extract_planar' else data['preserved_poses']
    a, b = poses[index], poses[following]
    result = {}
    for name in a:
        result[name] = {
            'location': list(Vector(a[name]['location']).lerp(Vector(b[name]['location']), fraction)),
            'rotation_quaternion': list(Quaternion(a[name]['rotation_quaternion']).slerp(Quaternion(b[name]['rotation_quaternion']), fraction).normalized()),
            'scale': list(Vector(a[name]['scale']).lerp(Vector(b[name]['scale']), fraction)),
        }
    root = (list(Vector(data['roots'][index]).lerp(Vector(data['roots'][following]), fraction))
            if root_mode == 'extract_planar' else [0., 0., 0.])
    return {'pose': result, 'root': root, 'source_frame': source_frame, 'source_time_s': time,
            'source_fps': data['source_fps'], 'provenance': {**provenance, 'root_mode': root_mode,
                                                          'source_window_seconds': [low, high]}}


def motion_apply(job_id, actor_id, segment):
    """Convenience wrapper; the unified timeline owns and bakes this event."""
    if not isinstance(segment, dict) or not segment.get('motion_id'):
        raise ValueError('Library segment needs motion_id and explicit timeline frames')
    from agent_motion_timeline import register_event, motion_commit
    source = segment.get('source', 'hybrid' if segment.get('keyframes') or segment.get('targets') else 'library')
    if source not in ('library', 'hybrid'):
        raise ValueError('motion_apply selects a library/hybrid source; arbitrary procedural motion uses the timeline directly')
    event = {**segment, 'source': source}
    event_id = str(event.pop('event_id', 'library_' + str(segment['motion_id']) + '_' + str(segment.get('start_frame'))))
    register_event(job_id, actor_id, event_id, event)
    return motion_commit(job_id, actor_id)


def clear_sample_cache():
    _CACHE.clear()
    _ALIASES.clear()
