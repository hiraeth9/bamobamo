"""Optional flat-floor kicks compiled onto the existing Beta FK/IK rig.

Hermes chooses the foot, target, direction and timing; no kick clip is needed.
Call register_interaction, motion_commit and verify_interaction separately.
Before combining a kick with a walking/stepping plan, release the attacking
foot's planted contact during the returned release_window. This compiler does
not edit existing foot plans. It returns to the captured root/guard/stance.
The support sole is geometrically pinned; this is not a dynamic balance solver.
"""
from __future__ import annotations

import copy
import math

from agent_interactions import (_bone, _frame, _name, _number, _plan, _runtime,
                                _snapshot, _surface_effector, _target_event,
                                _vector, smooth, support_lowering, support_pole)


def kick_timing(start_frame, impact_frame, end_frame, *, chamber_frame=None,
                recover_frame=None, land_frame=None, contact_frames=1):
    first, hit, last = (_frame(value, name) for value, name in
                        ((start_frame, 'start_frame'), (impact_frame, 'impact_frame'), (end_frame, 'end_frame')))
    count = _frame(contact_frames, 'contact_frames')
    if count not in (1, 2):
        raise ValueError('kick contact_frames must be 1 or 2')
    land = last-max(2, (last-first)//8) if land_frame is None else _frame(land_frame, 'land_frame')
    chamber = first+round((hit-first)*.6) if chamber_frame is None else _frame(chamber_frame, 'chamber_frame')
    release = hit+count-1
    recover = release+round((land-release)*.45) if recover_frame is None else _frame(recover_frame, 'recover_frame')
    if not first < chamber < hit <= release < recover < land <= last:
        raise ValueError('kick needs start < chamber < impact <= release < recover < land <= end')
    return {'start': first, 'chamber': chamber, 'impact': hit, 'release': release,
            'recover': recover, 'land': land, 'end': last}


def kick_position(frame, timing, initial, chamber, impact):
    """A continuous chamber → extension → rechamber → landing trajectory."""
    knots = ((timing['start'], initial), (timing['chamber'], chamber),
             (timing['impact'], impact), (timing['release'], impact),
             (timing['recover'], chamber), (timing['land'], initial))
    if frame <= knots[0][0]:
        return list(initial)
    for (first, a), (last, b) in zip(knots, knots[1:]):
        if first <= frame <= last and last > first:
            amount = smooth((frame-first)/(last-first))
            return [x+(y-x)*amount for x, y in zip(a, b)]
    return list(initial)


def kick_weight(frame, timing):
    if frame <= timing['start'] or frame >= timing['land']:
        return 0.
    if frame < timing['chamber']:
        return smooth((frame-timing['start'])/(timing['chamber']-timing['start']))
    if frame > timing['recover']:
        return 1-smooth((frame-timing['recover'])/(timing['land']-timing['recover']))
    return 1.


def kick_pole(hip, ankle, direction):
    """Knees bend into the kick plane; a horizontal extension bends upward."""
    axis = [b-a for a, b in zip(hip, ankle)]
    size = sum(value*value for value in axis)
    along = sum(a*b for a, b in zip(direction, axis))/size
    projected = [value-along*part for value, part in zip(direction, axis)]
    if sum(value*value for value in projected) < .01:
        direction = [0., 0., 1.]
    return support_pole(hip, ankle, direction)


def foot_contact_conflicts(requirements, foot, start, end, event_id):
    return [item['id'] for item in requirements
            if item['chain'] == foot and item['frame_start'] <= end and item['frame_end'] >= start
            and not item['event_id'].startswith(event_id+':')]


def _strike_surface(actor, foot, direction):
    """Select the real toe/forefoot surface facing the requested kick."""
    import bpy
    from mathutils import Vector
    candidates = []
    for mesh in actor['meshes']:
        groups = {g.index for g in mesh.vertex_groups if
                  g.name.split(':')[-1].startswith((foot, foot.removesuffix('Foot')+'Toe'))}
        evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        geometry = evaluated.to_mesh()
        try:
            for vertex in mesh.data.vertices:
                if sum(g.weight for g in vertex.groups if g.group in groups) < .3:
                    continue
                point = evaluated.matrix_world @ geometry.vertices[vertex.index].co
                candidates.append((-point.dot(Vector(direction)), mesh.name, vertex.index, list(point)))
        finally:
            evaluated.to_mesh_clear()
    if not candidates:
        raise ValueError('kick needs actual Foot/Toe weighted skin geometry')
    _, obj, index, point = min(candidates, key=lambda item: item[0])
    return {'object': obj, 'vertex_index': index}, point


def leg_strike_events(job_id, actor_id, event_id, target_binding, *, start_frame,
                      impact_frame, end_frame, foot='RightFoot', direction_world=None,
                      chamber_frame=None, recover_frame=None, land_frame=None,
                      contact_frames=1, chamber_height_m=.45, root_shift_m=.08,
                      floor_z=0., tolerance_m=.02, effector=None, priority=None, intent=None):
    """Compile a basic front kick, grounded support, and return to the base.

    Target bindings and optional effector address actual skin geometry. Root
    adjustment only shifts laterally toward the support foot and returns; it
    never silently advances an out-of-range kick. Existing attack-foot contact
    conflicts return contact_release_required, leaving those plans untouched.
    """
    import bpy
    from mathutils import Matrix, Quaternion, Vector
    from agent_contacts import _requirements_for_events, resolve_binding, resolve_effector, validate_binding
    from agent_motion_timeline import timeline_info
    timing = kick_timing(start_frame, impact_frame, end_frame, chamber_frame=chamber_frame,
                         recover_frame=recover_frame, land_frame=land_frame, contact_frames=contact_frames)
    if foot not in ('LeftFoot', 'RightFoot'):
        raise ValueError('foot must address LeftFoot or RightFoot')
    _name(event_id, 'event_id')
    floor, height, shift, tolerance = (_number(value, name) for value, name in
        ((floor_z, 'floor_z'), (chamber_height_m, 'chamber_height_m'),
         (root_shift_m, 'root_shift_m'), (tolerance_m, 'tolerance_m')))
    if not 0 < height <= .7 or not 0 <= shift <= .12 or tolerance <= 0:
        raise ValueError('flat-floor kick needs chamber height (0,.7]m, lateral root shift [0,.12]m and positive tolerance')
    binding = validate_binding(target_binding)
    actor, scene, chosen = _runtime(job_id, actor_id, timing['start'], timing['end'], priority)
    requirements = _requirements_for_events(actor_id, timeline_info(job_id, actor_id)['events'])
    conflicts = foot_contact_conflicts(requirements, foot, timing['start']+1, timing['land']-1, event_id)
    release_window = {'foot': foot, 'start_frame': timing['start']+1, 'end_frame': timing['land']-1}
    if conflicts:
        return {'ok': False, 'actor_id': actor_id, 'event_id': event_id, 'events': [],
                'status': 'contact_release_required', 'release_window': release_window,
                'conflicting_contact_ids': conflicts,
                'suggestion': 'Plan/release this foot contact window first; existing foot plans were not changed.'}
    if actor['root'].parent is not None or max(abs(value-1) for value in actor['root'].scale) > 1e-6:
        raise ValueError('flat-floor kick needs an unparented, unit-scale actor root')
    samples = _snapshot(actor, scene, timing['start'], timing['end'])
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    support = 'LeftFoot' if foot == 'RightFoot' else 'RightFoot'
    try:
        scene.frame_set(timing['start']); bpy.context.view_layer.update()
        soles, anchors, rotations, ankle_offsets, lengths = {}, {}, {}, {}, {}
        for chain in (foot, support):
            soles[chain], anchors[chain] = _surface_effector(actor, chain)
            if abs(anchors[chain][2]-floor) > .04:
                raise ValueError('kick needs an initially grounded stance; settle the actual sole first')
            arm, bone = _bone(actor, chain, evaluated=True)
            rotations[chain] = (arm.matrix_world @ bone.matrix).to_quaternion()
            ankle_offsets[chain] = Vector(anchors[chain])-(arm.matrix_world @ bone.head)
            _, upper = _bone(actor, chain.removesuffix('Foot')+'UpLeg', evaluated=True)
            _, knee = _bone(actor, chain.removesuffix('Foot')+'Leg', evaluated=True)
            a, b, c = [arm.matrix_world @ item.head for item in (upper, knee, bone)]
            lengths[chain] = (a-b).length+(b-c).length
            anchors[chain][2] = floor
        scene.frame_set(timing['impact']); bpy.context.view_layer.update()
        target = Vector(resolve_binding(job_id, binding)['position'])
        scene.frame_set(timing['start']); bpy.context.view_layer.update()
        arm, foot_bone = _bone(actor, foot, evaluated=True)
        ankle = arm.matrix_world @ foot_bone.head
        direction = Vector(_vector(direction_world, 'direction_world')) if direction_world is not None else target-ankle
        direction.z = 0.
        if direction.length < .001:
            raise ValueError('kick needs a horizontal approach direction')
        direction.normalize()
        if effector is None:
            effector, initial = _strike_surface(actor, foot, direction)
        else:
            initial = resolve_effector(job_id, actor, foot_bone.name, {'effector': effector})['position']
        initial = Vector(initial)
        try:
            _, toe = _bone(actor, foot.removesuffix('Foot')+'ToeBase', evaluated=True)
            toe_direction = arm.matrix_world @ toe.head-ankle
        except ValueError:
            toe_direction = actor['root'].matrix_world.to_quaternion() @ Vector((0, -1, 0))
        toe_direction.z = 0.; toe_direction.normalize()
        rotation = toe_direction.rotation_difference(direction) @ rotations[foot]
        chamber = initial+direction*.10+Vector((0, 0, height))
        root_initial = Vector(samples[timing['start']]['root'])
        toward_support = Vector(anchors[support])-root_initial
        toward_support.z = 0.
        lateral = actor['root'].matrix_world.to_quaternion() @ Vector((1, 0, 0))
        lateral_shift = lateral*max(-shift, min(shift, toward_support.dot(lateral)))
        body_keys, attack_keys, support_keys = [], [], []
        _, hips_bone = _bone(actor, 'Hips')
        for frame in range(timing['start'], timing['end']+1):
            weight = kick_weight(frame, timing)
            root = Vector(samples[frame]['root'])+lateral_shift*weight
            root_rotation = Quaternion(samples[frame]['root_rotation_quaternion'])
            new_root = Matrix.LocRotScale(root, root_rotation, Vector(samples[frame]['root_scale']))
            delta = new_root @ samples[frame]['root_world'].inverted()
            hip_positions = {}
            for chain in (foot, support):
                _, upper = _bone(actor, chain.removesuffix('Foot')+'UpLeg')
                hip_positions[chain] = delta @ samples[frame]['bone_world'][upper.name].translation
            support_ankle = Vector(anchors[support])-ankle_offsets[support]
            lowering = support_lowering(hip_positions[support], support_ankle, lengths[support])*weight
            hip_positions = {chain: point-Vector((0, 0, lowering)) for chain, point in hip_positions.items()}
            point = Vector(kick_position(frame, timing, initial, chamber, target))
            current_rotation = rotations[foot].slerp(rotation, weight)
            offset = current_rotation @ (rotations[foot].inverted() @ (initial-ankle))
            planned_ankle = point-offset
            reach = (planned_ankle-hip_positions[foot]).length
            allowed_reach = lengths[foot]*(.995 if timing['impact'] <= frame <= timing['release'] else 1.00001)
            if reach > allowed_reach:
                return {'ok': False, 'actor_id': actor_id, 'event_id': event_id, 'events': [],
                        'status': 'approach_required', 'frame': frame,
                        'target_distance_m': reach, 'leg_reach_m': lengths[foot],
                        'missing_reach_m': reach-allowed_reach,
                        'suggestion': 'Approach or lower the target, then recompile; bones were not stretched and the actor was not moved.'}
            pose = copy.deepcopy(samples[frame]['pose'])
            if lowering:
                inverse = samples[frame]['armature_world'].inverted()
                matrix = inverse @ samples[frame]['bone_world'][hips_bone.name]
                matrix.translation += inverse.to_3x3() @ Vector((0, 0, -lowering))
                kwargs = {}
                if hips_bone.parent:
                    kwargs = {'parent_matrix': inverse @ samples[frame]['bone_world'][hips_bone.parent.name],
                              'parent_matrix_local': hips_bone.parent.bone.matrix_local}
                pose[hips_bone.name]['location'] = list(hips_bone.bone.convert_local_to_pose(
                    matrix, hips_bone.bone.matrix_local, invert=True, **kwargs).to_translation())
            body_keys.append({'frame': frame, 'pose': pose, 'root': list(root),
                              'root_rotation_quaternion': list(root_rotation), 'root_scale': samples[frame]['root_scale'],
                              'interpolation': 'linear'})
            attack_keys.append({'frame': frame, 'position': list(point),
                                'rotation_quaternion': list(current_rotation),
                                'pole': kick_pole(hip_positions[foot], planned_ankle, direction),
                                'influence': 1, 'interpolation': 'linear'})
            forward = root_rotation @ Vector((0, -1, 0))
            support_keys.append({'frame': frame, 'position': anchors[support],
                                 'pole': support_pole(hip_positions[support], support_ankle, forward),
                                 'influence': 1, 'interpolation': 'linear'})
        source = str(intent or 'Flat-floor knee chamber, foot strike, rechamber and planted guard recovery')
        events = [{'event_id': event_id+':body', 'source': 'procedural', 'start_frame': timing['start'],
                   'end_frame': timing['end'], 'root_strategy': 'trajectory', 'priority': chosen,
                   'keyframes': body_keys, 'intent': source}]
        sid = actor_id+':'+event_id+':support'
        events.append(_target_event(event_id+':support', timing['start'], timing['end'], support,
                      {'effector': soles[support], 'rotation_quaternion': list(rotations[support]),
                       'keyframes': support_keys, 'contact': {'id': sid, 'kind': 'support', 'tolerance_m': .01}}, chosen+1, source))
        for label, begin, finish in (('lift', timing['start'], timing['impact']-1),
                                     ('retract', timing['release']+1, timing['land']-1)):
            events.append(_target_event(event_id+':'+label, begin, finish, foot,
                          {'effector': effector, 'keyframes': [key for key in attack_keys if begin <= key['frame'] <= finish],
                           'contact': False}, chosen+1, source))
        cid = actor_id+':'+event_id+':impact'
        key = next(key for key in attack_keys if key['frame'] == timing['impact'])
        events.append(_target_event(event_id+':impact', timing['impact'], timing['release'], foot,
                      {'effector': effector, 'binding': binding, 'rotation_quaternion': list(rotation),
                       'pole': key['pole'], 'influence': 1,
                       'contact': {'id': cid, 'kind': 'strike', 'tolerance_m': tolerance}}, chosen+1, source))
        for label, begin, finish in (('initial_plant', timing['start'], timing['start']),
                                    ('land', timing['land'], timing['end'])):
            events.append(_target_event(event_id+':'+label, begin, finish, foot,
                          {'effector': soles[foot], 'position': anchors[foot], 'rotation_quaternion': list(rotations[foot]),
                           'keyframes': [{'frame': key['frame'], 'position': anchors[foot], 'pole': key['pole'], 'influence': 1}
                                         for key in attack_keys if begin <= key['frame'] <= finish],
                           'contact': {'id': actor_id+':'+event_id+':'+label, 'kind': 'support', 'tolerance_m': .01}}, chosen+1, source))
        return _plan(actor_id, event_id, events, {'impact_contact_ids': [cid], 'effector': {'effector': effector},
                     'support_foot': support, 'support_contact_id': sid, 'release_window': release_window,
                     'timing': timing, 'leg_reach_m': lengths[foot], 'root_shift_m': lateral_shift.length,
                     'base_recovery': 'captured_root_guard_and_grounded_stance',
                     'support_verified': False, 'balance_verified': False, 'attack_source': 'procedural_existing_Beta_skin_FK_IK'})
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
