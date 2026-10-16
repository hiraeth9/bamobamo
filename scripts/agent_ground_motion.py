"""Single-person ground-motion priors retargeted onto the existing Beta.

The source supplies body motion, not another robot or a paired fight recipe.
Hermes chooses a local source interval and the scene timing; the compiler
returns ordinary timeline events. Floor settling and short hand/foot support
windows are kinematic corrections, not a ragdoll or balance simulation.
"""
from __future__ import annotations

import contextlib
import copy
import io
import math
from pathlib import Path

from agent_interactions import _frame, _name, _number, _plan, _runtime, _target_event, smooth


def source_times(start_frame, end_frame, source_window):
    first, last = _frame(start_frame, 'start_frame'), _frame(end_frame, 'end_frame')
    if first >= last:
        raise ValueError('ground motion needs start_frame < end_frame')
    if len(source_window) != 2:
        raise ValueError('source_window needs [start_seconds,end_seconds]')
    low, high = [_number(value, 'source_window') for value in source_window]
    if low < 0 or low >= high:
        raise ValueError('source_window needs 0 <= start_seconds < end_seconds')
    return [(frame, low+(high-low)*(frame-first)/(last-first)) for frame in range(first, last+1)]


def support_windows(points, fps, *, floor_z=0., height_m=.045, speed_m_s=.28, minimum_frames=4):
    """Suggest short near-floor, slow support intervals from actual skin points.

    Suggestions are generated from the retargeted geometry, never file names.
    They still need the normal post-bake contact check.
    """
    result = []
    frames = sorted(points)
    for chain in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot'):
        run = []
        for index, frame in enumerate(frames):
            position = points[frame][chain]
            before = points[frames[max(0, index-1)]][chain]
            after = points[frames[min(len(frames)-1, index+1)]][chain]
            span = frames[min(len(frames)-1, index+1)]-frames[max(0, index-1)]
            speed = math.dist(before, after)*fps/span if span else 0.
            grounded = position[2]-floor_z <= height_m and speed <= speed_m_s
            if grounded:
                run.append(frame)
            if run and (not grounded or index == len(frames)-1):
                if len(run) >= minimum_frames:
                    result.append({'chain': chain, 'start_frame': run[0], 'end_frame': run[-1]})
                run = []
    return result


def _named(bones, short):
    return next((bone for bone in bones if bone.name.split(':')[-1] == short), None)


def _setup(source, target):
    """Measure world/rest rotations; object-root Hips is a supported source."""
    from mathutils import Vector
    reference = source.matrix_world.copy()
    target_basis = target.matrix_basis.copy()
    hip = _named(source.data.bones, 'Hips')
    target_hip = _named(target.data.bones, 'Hips')
    required = ('Head', 'Spine', 'LeftUpLeg', 'LeftLeg', 'LeftFoot', 'RightUpLeg',
                'RightLeg', 'RightFoot', 'LeftArm', 'LeftForeArm', 'LeftHand',
                'RightArm', 'RightForeArm', 'RightHand')
    if target_hip is None or any(_named(source.data.bones, name) is None for name in required):
        raise ValueError('ground source needs a Mixamo body mapping and a Beta Hips target')
    source_hip = reference @ hip.head_local if hip else reference.translation.copy()
    beta_hip = target_basis @ target_hip.head_local
    if source_hip.z <= 0 or beta_hip.z <= 0:
        raise ValueError('ground source must start in its standing calibration pose')
    mapping = {bone.name: _named(source.data.bones, bone.name.split(':')[-1]) for bone in target.data.bones}
    return {'reference': reference, 'source_hip': source_hip,
            'target_basis': target_basis, 'target_rotation': target_basis.to_quaternion(),
            'target_inverse': target_basis.to_3x3().inverted(),
            'source_rest': {b.name: (reference @ b.matrix_local).to_quaternion() for b in source.data.bones},
            'target_rest': {b.name: (target_basis @ b.matrix_local).to_quaternion() for b in target.data.bones},
            'mapping': mapping, 'hip': hip.name if hip else None,
            'scale': beta_hip.z/source_hip.z}


def _retarget(source, target, setup, planar_origin):
    from mathutils import Vector
    source_hip = (source.matrix_world @ source.pose.bones[setup['hip']].head
                  if setup['hip'] else source.matrix_world.translation.copy())
    root = source_hip-planar_origin
    root.z = 0.
    root *= setup['scale']
    desired, pose = {}, {}
    def depth(bone):
        count = 0
        while bone.parent:
            count += 1
            bone = bone.parent
        return count
    for bone in sorted(target.data.bones, key=depth):
        rest = bone.matrix_local.to_quaternion()
        inherited = (desired[bone.parent.name] @ bone.parent.matrix_local.to_quaternion().inverted() @ rest
                     if bone.parent else rest)
        mapped = setup['mapping'][bone.name]
        if mapped:
            rotation = (source.matrix_world @ source.pose.bones[mapped.name].matrix).to_quaternion()
            world = rotation @ setup['source_rest'][mapped.name].inverted() @ setup['target_rest'][bone.name]
            desired[bone.name] = setup['target_rotation'].inverted() @ world
        elif bone.name.split(':')[-1] == 'Hips':
            world = (source.matrix_world.to_quaternion() @ setup['reference'].to_quaternion().inverted()
                     @ setup['target_rest'][bone.name])
            desired[bone.name] = setup['target_rotation'].inverted() @ world
        else:
            desired[bone.name] = inherited
        location = Vector()
        if bone.name.split(':')[-1] == 'Hips':
            delta = (source_hip-setup['source_hip'])*setup['scale']-root
            # Window placement belongs to actor root. Do not retain the
            # source's distance travelled before this selected interval.
            delta.x = delta.y = 0.
            location = rest.inverted() @ (setup['target_inverse'] @ delta)
        pose[bone.name] = {'location': list(location), 'rotation_quaternion': list(
            (inherited.inverted() @ desired[bone.name]).normalized()), 'scale': [1., 1., 1.]}
    return pose, root


def _surface_indices(actor):
    """Fixed candidate sets; each frame can pick the actual underside."""
    result = {}
    for chain in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot'):
        entries = []
        for mesh in actor['meshes']:
            prefix = (chain, chain.removesuffix('Foot')+'Toe') if chain.endswith('Foot') else (chain,)
            groups = {g.index for g in mesh.vertex_groups if g.name.split(':')[-1].startswith(prefix)}
            indices = [v.index for v in mesh.data.vertices if sum(g.weight for g in v.groups if g.group in groups) >= .4]
            entries.extend((mesh.name, index) for index in indices)
        if not entries:
            raise ValueError('ground motion needs actual '+chain+' weighted skin')
        result[chain] = entries
    return result


def _geometry(actor, indices):
    import bpy
    points, all_points = {}, []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for mesh in actor['meshes']:
        evaluated = mesh.evaluated_get(depsgraph)
        geometry = evaluated.to_mesh()
        try:
            coordinates = [evaluated.matrix_world @ vertex.co for vertex in geometry.vertices]
            all_points.extend(coordinates)
            for chain, entries in indices.items():
                for name, index in entries:
                    if name != mesh.name:
                        continue
                    point = coordinates[index]
                    if chain not in points or point.z < points[chain][2][2]:
                        points[chain] = (name, index, list(point))
        finally:
            evaluated.to_mesh_clear()
    return points, min(point.z for point in all_points)


def _temporary_actor(actor, created):
    import bpy
    clones = {}
    for original in (actor['root'], actor['armature'], *actor['meshes']):
        clone = original.copy()
        clone.animation_data_clear()
        if original.type == 'ARMATURE':
            clone.data = original.data.copy()
        bpy.context.scene.collection.objects.link(clone)
        created.append(clone)
        clones[original] = clone
    for original, clone in clones.items():
        clone.parent = clones.get(original.parent)
        for modifier in clone.modifiers:
            if modifier.type == 'ARMATURE' and modifier.object in clones:
                modifier.object = clones[modifier.object]
        if clone.type == 'ARMATURE':
            for bone in clone.pose.bones:
                for constraint in list(bone.constraints):
                    bone.constraints.remove(constraint)
    return {'root': clones[actor['root']], 'armature': clones[actor['armature']],
            'meshes': [clones[mesh] for mesh in actor['meshes']]}, clones


def ground_motion_events(job_id, actor_id, event_id, source_asset, *, source_window,
                         start_frame, end_frame, floor_z=0., supports=None,
                         settle_frames=8, tolerance_m=.025, expect_standing=False,
                         blend_in_frames=6, priority=None, intent=None):
    """Compile a measured source window plus floor/skin support corrections.

    ``supports`` may explicitly set chain/start_frame/end_frame; None suggests
    slow near-floor windows. The clip's planar displacement is extracted into
    the actor root, with vertical pelvis motion retained. Output is unbaked.
    The source must be local and begin with a standing calibration frame.
    ``settle_frames`` holds the final pose and anchors its feet afterwards.
    """
    import bpy
    from mathutils import Quaternion, Vector
    from agent_motion_controls import read_pose, set_pose
    _name(event_id, 'event_id')
    times = source_times(start_frame, end_frame, source_window)
    floor, tolerance = _number(floor_z, 'floor_z'), _number(tolerance_m, 'tolerance_m')
    settle = _frame(settle_frames, 'settle_frames')
    blend = _frame(blend_in_frames, 'blend_in_frames')
    last = times[-1][0]+settle
    if settle < 0 or blend < 0 or tolerance <= 0:
        raise ValueError('settle_frames/blend_in_frames must be nonnegative and tolerance_m positive')
    actor, scene, chosen = _runtime(job_id, actor_id, times[0][0], last, priority)
    if actor['root'].parent or any(abs(value-1) > 1e-6 for value in actor['root'].scale):
        raise ValueError('ground motion needs an unparented, unit-scale actor root')
    path = Path(source_asset).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != '.fbx':
        raise ValueError('source_asset must name an existing local FBX file')
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    old_fps, old_fps_base = scene.render.fps, scene.render.fps_base
    kinds = ('objects', 'collections', 'actions', 'meshes', 'armatures', 'materials', 'images', 'textures')
    before = {kind: set(getattr(bpy.data, kind)) for kind in kinds}
    created = []
    try:
        scene.frame_set(times[0][0]); bpy.context.view_layer.update()
        initial_pose = read_pose(actor['armature'], evaluated=True)
        probe, clones = _temporary_actor(actor, created)
        indices = _surface_indices(probe)
        root_start = probe['root'].location.copy()
        root_rotation = probe['root'].matrix_basis.to_quaternion()
        root_scale = list(probe['root'].scale)
        before_import = set(bpy.data.objects)
        with contextlib.redirect_stdout(io.StringIO()):
            bpy.ops.import_scene.fbx(filepath=str(path))
        imported = set(bpy.data.objects)-before_import
        sources = [obj for obj in imported if obj.type == 'ARMATURE']
        if len(sources) != 1 or not sources[0].animation_data or not sources[0].animation_data.action:
            raise ValueError('source FBX needs one animated armature')
        source = sources[0]
        action = source.animation_data.action
        key_first, key_last = list(action.frame_range)
        key_fps = scene.render.fps
        duration = (key_last-key_first)/key_fps
        if source_window[1] > duration+1e-5:
            raise ValueError('source_window ends beyond actual clip duration %.3fs' % duration)
        scene.frame_set(math.floor(key_first), subframe=key_first%1); bpy.context.view_layer.update()
        setup = _setup(source, probe['armature'])
        first_time = key_first+times[0][1]*key_fps
        scene.frame_set(math.floor(first_time), subframe=first_time%1); bpy.context.view_layer.update()
        origin = (source.matrix_world @ source.pose.bones[setup['hip']].head
                  if setup['hip'] else source.matrix_world.translation.copy())
        samples, positions, geometry_samples = {}, {}, {}
        for frame, time in times:
            key = key_first+time*key_fps
            scene.frame_set(math.floor(key), subframe=key%1); bpy.context.view_layer.update()
            pose, root_delta = _retarget(source, probe['armature'], setup, origin)
            amount = smooth((frame-times[0][0])/blend) if blend else 1.
            if amount < 1:
                for name, channels in pose.items():
                    prior = initial_pose[name]
                    channels['location'] = list(Vector(prior['location']).lerp(Vector(channels['location']), amount))
                    channels['rotation_quaternion'] = list(Quaternion(prior['rotation_quaternion']).slerp(
                        Quaternion(channels['rotation_quaternion']), amount))
                    channels['scale'] = list(Vector(prior['scale']).lerp(Vector(channels['scale']), amount))
            set_pose(probe['armature'], pose)
            probe['root'].location = root_start+(root_rotation @ root_delta)*amount
            bpy.context.view_layer.update()
            surfaces, minimum = _geometry(probe, indices)
            correction = floor-minimum
            probe['root'].location.z += correction
            bpy.context.view_layer.update()
            surfaces, minimum = _geometry(probe, indices)
            arm = probe['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
            samples[frame] = {'frame': frame, 'pose': pose, 'root': list(probe['root'].location),
                              'root_rotation_quaternion': list(root_rotation), 'root_scale': root_scale,
                              'interpolation': 'linear'}
            positions[frame] = {chain: point[2] for chain, point in surfaces.items()}
            hip, head = [_named(arm.pose.bones, name) for name in ('Hips', 'Head')]
            hip, head = [arm.matrix_world @ bone.head for bone in (hip, head)]
            geometry_samples[frame] = {'surfaces': surfaces, 'minimum_z': minimum,
                'hip_height': hip.z-floor, 'torso_up': (head-hip).normalized().z,
                'rotations': {chain: list((arm.matrix_world @ _named(arm.pose.bones, chain).matrix).to_quaternion()) for chain in surfaces},
                'joints': {chain: [list(arm.matrix_world @ _named(arm.pose.bones, chain.removesuffix('Foot')+'UpLeg' if chain.endswith('Foot') else chain.removesuffix('Hand')+'Arm').head),
                                   list(arm.matrix_world @ _named(arm.pose.bones, chain.removesuffix('Foot')+'Leg' if chain.endswith('Foot') else chain.removesuffix('Hand')+'ForeArm').head),
                                   list(arm.matrix_world @ _named(arm.pose.bones, chain).head)] for chain in surfaces}}
        # Final hold is explicit; no animation is stretched beyond the clip window.
        for frame in range(times[-1][0]+1, last+1):
            samples[frame] = {**copy.deepcopy(samples[times[-1][0]]), 'frame': frame}
            positions[frame] = copy.deepcopy(positions[times[-1][0]])
            geometry_samples[frame] = geometry_samples[times[-1][0]]
        if supports is None:
            # Near-floor feet on a supine person are not planted load-bearing
            # feet.  Only suggest them once the pelvis/torso rise again.
            eligible = copy.deepcopy(positions)
            for frame, values in eligible.items():
                state = geometry_samples[frame]
                for chain in values:
                    if (chain.endswith('Foot') and (state['hip_height'] < .5 or state['torso_up'] < .75)
                            or chain.endswith('Hand') and state['torso_up'] < .45):
                        values[chain][2] = floor+1.
            windows = support_windows(eligible, old_fps/old_fps_base, floor_z=floor)
        else:
            windows = copy.deepcopy(supports)
        if settle:
            for chain in ('LeftFoot', 'RightFoot'):
                if positions[last][chain][2]-floor < .06:
                    windows.append({'chain': chain, 'start_frame': times[-1][0], 'end_frame': last})
        # Merge adjacent suggestions for the same actual end effector.
        merged = []
        for item in sorted(windows, key=lambda item: (item['chain'], item['start_frame'])):
            if merged and merged[-1]['chain'] == item['chain'] and item['start_frame'] <= merged[-1]['end_frame']+1:
                merged[-1]['end_frame'] = max(merged[-1]['end_frame'], item['end_frame'])
            else:
                merged.append(item)
        # Release a suggested support before the rising body exceeds its
        # measured limb reach. A hand on the floor cannot follow a shoulder
        # indefinitely. This offset estimate only trims suggestions; explicit
        # skin supports are measured by the actual post-bake solver/check.
        reachable = []
        for window in merged:
            chain, first, finish = window['chain'], window['start_frame'], window['end_frame']
            if chain not in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot') or not times[0][0] <= first <= finish <= last:
                raise ValueError('support needs a hand/foot chain and frames inside the ground-motion window')
            state = geometry_samples[first]
            anchor = Vector(state['surfaces'][chain][2]); anchor.z = floor
            local_offset = Quaternion(state['rotations'][chain]).inverted() @ (
                Vector(state['surfaces'][chain][2])-Vector(state['joints'][chain][-1]))
            end = finish
            for frame in range(first, finish+1):
                state = geometry_samples[frame]
                a, b, c = [Vector(value) for value in state['joints'][chain]]
                goal = anchor-Quaternion(state['rotations'][chain]) @ local_offset
                if supports is None and (goal-a).length > ((b-a).length+(c-b).length)*1.0001:
                    end = frame-1
                    break
            if end >= first:
                reachable.append({**window, 'end_frame': end})
        merged = reachable
        events = [{'event_id': event_id+':body', 'source': 'procedural',
                   'start_frame': times[0][0], 'end_frame': last, 'root_strategy': 'trajectory',
                   'priority': chosen, 'keyframes': list(samples.values()),
                   'intent': str(intent or 'Retargeted single-person ground motion with floor correction'),
                   'ground_motion': {'floor_z': floor, 'tolerance_m': tolerance,
                                     'expect_standing': bool(expect_standing),
                                     'settle_start': times[-1][0] if settle else None}}]
        original_by_clone = {clone.name: original.name for original, clone in clones.items()}
        support_ids = []
        for index, window in enumerate(merged):
            chain, first, finish = window['chain'], window['start_frame'], window['end_frame']
            if chain not in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot') or not times[0][0] <= first <= finish <= last:
                raise ValueError('support needs a hand/foot chain and frames inside the ground-motion window')
            obj, vertex, anchor = geometry_samples[first]['surfaces'][chain]
            anchor = [*anchor[:2], floor]
            keys = []
            for frame in range(first, finish+1):
                a, b, c = [Vector(value) for value in geometry_samples[frame]['joints'][chain]]
                axis = c-a
                projected = a+axis*((b-a).dot(axis)/axis.length_squared)
                bend = b-projected
                if bend.length < .001:
                    bend = root_rotation @ Vector((0, -1, .2))
                pole = b+bend.normalized()*.5
                influence = (smooth((last-frame)/settle) if settle and chain.endswith('Foot')
                             and finish == last and frame >= times[-1][0] else 1.)
                keys.append({'frame': frame, 'position': anchor, 'pole': list(pole), 'influence': influence,
                             'rotation_quaternion': geometry_samples[frame]['rotations'][chain], 'interpolation': 'linear'})
            cid = actor_id+':'+event_id+':support_'+str(index)
            support_ids.append(cid)
            events.append(_target_event(event_id+':support_'+str(index), first, finish, chain,
                {'effector': {'object': original_by_clone[obj], 'vertex_index': vertex},
                 'keyframes': keys, 'contact': {'id': cid, 'kind': 'support', 'tolerance_m': tolerance}}, chosen+1,
                 'Actual skin support during ground-motion prior'))
        result = _plan(actor_id, event_id, events, {'ground_support_ids': support_ids})
        result.update({'source_asset': str(path), 'source_window_seconds': list(source_window),
                       'source_duration_s': duration, 'source_bones': len(source.data.bones),
                       'hips_source': 'bone' if setup['hip'] else 'armature_object',
                       'mapped_bones': sum(value is not None for value in setup['mapping'].values()),
                       'retarget_scale': setup['scale'], 'skin_replaced': False, 'blend_in_frames': blend,
                       'floor_z': floor, 'support_windows': merged,
                       'limitations': 'Single-person prior and kinematic supports; whole-body collision, balance and arbitrary fall direction are not solved.'})
        return result
    finally:
        for kind in kinds:
            collection = getattr(bpy.data, kind)
            for datablock in set(collection)-before[kind]:
                collection.remove(datablock, do_unlink=True)
        scene.render.fps, scene.render.fps_base = old_fps, old_fps_base
        scene.frame_set(old_frame, subframe=old_subframe)
        bpy.context.view_layer.update()


def check_ground_motion(job_id, actor_id, event_id=None, *, frame_start=None, frame_end=None):
    """Measure saved ground events: actual whole skin against the floor.

    Complements normal hand/foot contact checks; this does not prove dynamic
    balance or whole-body self collision.
    """
    import bpy
    from agent_character import resolve_actor
    from agent_contacts import check_contacts
    from agent_motion_timeline import timeline_info
    actor = resolve_actor(job_id, actor_id)
    events = [event for event in timeline_info(job_id, actor_id)['events'] if event.get('ground_motion')
              and (event_id is None or event['event_id'] == event_id+':body')]
    if not events:
        raise ValueError('no registered ground-motion body event was found')
    scene = bpy.context.scene
    low = scene.frame_start if frame_start is None else _frame(frame_start, 'frame_start')
    high = scene.frame_end if frame_end is None else _frame(frame_end, 'frame_end')
    if low > high:
        raise ValueError('ground check needs frame_start <= frame_end')
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    failures, maximum_penetration, maximum_gap, tested = [], 0., 0., 0
    try:
        for event in events:
            first, last = max(low, event['start_frame']), min(high, event['end_frame'])
            if first > last:
                continue
            requirement = event['ground_motion']
            floor, tolerance = requirement['floor_z'], requirement['tolerance_m']
            for frame in range(first, last+1):
                scene.frame_set(frame); bpy.context.view_layer.update()
                minimum = float('inf')
                for obj in actor['meshes']:
                    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
                    geometry = evaluated.to_mesh()
                    try:
                        minimum = min(minimum, min((evaluated.matrix_world @ vertex.co).z for vertex in geometry.vertices))
                    finally:
                        evaluated.to_mesh_clear()
                penetration, gap = max(0., floor-minimum), max(0., minimum-floor)
                maximum_penetration, maximum_gap = max(maximum_penetration, penetration), max(maximum_gap, gap)
                tested += 1
                if penetration > tolerance or gap > tolerance:
                    failures.append({'frame': frame, 'check': 'actual_skin_floor', 'minimum_z': minimum,
                                     'floor_z': floor, 'penetration_m': penetration, 'gap_m': gap})
            if requirement.get('expect_standing') and low <= event['end_frame'] <= high:
                arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
                hip, head = [_named(arm.pose.bones, name) for name in ('Hips', 'Head')]
                hip, head = [arm.matrix_world @ bone.head for bone in (hip, head)]
                upright = (head-hip).normalized().z
                if head.z-floor < 1.2 or upright < .75:
                    failures.append({'frame': event['end_frame'], 'check': 'final_standing_posture',
                                     'head_height_m': head.z-floor, 'torso_up_dot': upright})
        contact = check_contacts(job_id, actor_id=actor_id, frame_start=low, frame_end=high)
        return {'ok': True, 'passed': not failures and contact['passed'], 'tested_frames': tested,
                'maximum_skin_penetration_m': maximum_penetration, 'maximum_skin_floor_gap_m': maximum_gap,
                'failures': failures, 'contact_report': contact,
                'scope': 'Whole-skin floor clearance, registered supports and optional upright end posture; no dynamics or self-collision proof'}
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
