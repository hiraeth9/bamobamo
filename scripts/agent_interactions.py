"""Optional, event-producing kinematic tools on existing registered characters.

Hermes may import these ordinary Python functions through MCP execute_code.
The event compilers do not clear scenes, create actors, select cameras, or
publish a video. register_interaction optionally registers their output for a
separate commit. Coordinates are world metres unless explicitly bone-local;
times are scene integer frames. No attack animation clip is required.
"""
from __future__ import annotations

import copy
import json
import math

INTERACTION_STORE = 'wm_interaction_plans_v1'


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name+' must be a finite number')
    return float(value)


def _frame(value, name):
    value = _number(value, name)
    if value != int(value):
        raise ValueError(name+' must be an integer scene frame')
    return int(value)


def _vector(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(name+' needs three world-metre coordinates')
    return [_number(item, name) for item in value]


def _name(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name+' must be a stable nonempty string')
    return value


def smooth(value):
    value = min(1., max(0., float(value)))
    return value*value*(3-2*value)


def strike_progress(frame, start_frame, impact_frame, release_frame, end_frame):
    """Accelerate into contact; release goes back to authored FK, never rest."""
    if not start_frame < impact_frame <= release_frame < end_frame:
        raise ValueError('strike timing needs start < impact <= release < end')
    if frame <= start_frame or frame >= end_frame:
        return 0.
    if frame <= impact_frame:
        return ((frame-start_frame)/(impact_frame-start_frame))**1.65
    if frame <= release_frame:
        return 1.
    return 1-smooth((frame-release_frame)/(end_frame-release_frame))


def recoil_amount(frame, impact_frame, end_frame, fps):
    """A short damped response with strictly zero pre-impact displacement."""
    if end_frame <= impact_frame or fps <= 0:
        raise ValueError('recoil needs a positive frame window and FPS')
    if frame <= impact_frame or frame >= end_frame:
        return 0.
    seconds = (frame-impact_frame)/fps
    remaining = (end_frame-frame)/(end_frame-impact_frame)
    return smooth(seconds/.065)*math.exp(-max(0., seconds-.065)/.20)*smooth(remaining/.20)


def normalize_steps(steps, start_frame, end_frame):
    """Foot identifiers describe rig effectors, not a user action vocabulary."""
    if not isinstance(steps, (list, tuple)) or not steps:
        raise ValueError('steps needs explicit foot lift/landing instructions')
    result = []
    for step in steps:
        if not isinstance(step, dict) or step.get('foot') not in ('LeftFoot', 'RightFoot'):
            raise ValueError('step.foot must address the LeftFoot or RightFoot rig chain')
        item = copy.deepcopy(step)
        item['lift_frame'] = _frame(item.get('lift_frame'), 'step.lift_frame')
        item['land_frame'] = _frame(item.get('land_frame'), 'step.land_frame')
        item['position'] = _vector(item.get('position'), 'step.position')
        if not start_frame <= item['lift_frame'] < item['land_frame'] <= end_frame:
            raise ValueError('step lift/landing must form a positive interval within the root path')
        result.append(item)
    result.sort(key=lambda item: (item['lift_frame'], item['land_frame']))
    for previous, current in zip(result, result[1:]):
        if current['lift_frame'] < previous['land_frame']:
            raise ValueError('kinematic support steps need one grounded support foot; swing intervals cannot overlap')
        if current['foot'] == previous['foot']:
            raise ValueError('support steps alternate feet; describe a separate planted recovery before repeating one foot')
    return result


def foot_state(steps, foot, frame, initial_position, clearance_m=.08):
    """A fixed stance point or a smooth swing arc, with planted endpoints."""
    anchor = list(initial_position)
    for step in steps:
        if step['foot'] != foot:
            continue
        if frame >= step['land_frame']:
            anchor = list(step['position'])
        elif step['lift_frame'] < frame < step['land_frame']:
            t = (frame-step['lift_frame'])/(step['land_frame']-step['lift_frame'])
            amount = smooth(t)
            point = [a+(b-a)*amount for a, b in zip(anchor, step['position'])]
            point[2] += clearance_m*math.sin(math.pi*t)**2
            return {'position': point, 'stance': False, 'phase': t}
        elif frame <= step['lift_frame']:
            break
    return {'position': anchor, 'stance': True, 'phase': None}


def support_lowering(hip, ankle, leg_length):
    """Lower the pelvis enough for a non-stretching leg to reach its foot goal."""
    horizontal = math.hypot(hip[0]-ankle[0], hip[1]-ankle[1])
    reach = leg_length*.99
    if horizontal >= reach:
        raise ValueError('step is too long for the existing leg; shorten the foot/root displacement')
    return max(0., hip[2]-ankle[2]-math.sqrt(reach*reach-horizontal*horizontal))


def support_pole(hip, ankle, forward, distance_m=.7):
    """A knee bend plane from planned body facing, independent of old FK bend."""
    axis = [b-a for a, b in zip(hip, ankle)]
    length_squared = sum(value*value for value in axis)
    if length_squared < 1e-12:
        raise ValueError('step hip and ankle must define a leg axis')
    along = sum(a*b for a, b in zip(forward, axis))/length_squared
    projected = [value-along*axis_value for value, axis_value in zip(forward, axis)]
    length = math.sqrt(sum(value*value for value in projected))
    if length < 1e-8:
        raise ValueError('step body facing must define a knee bend plane across its leg axis')
    return [(a+b)*.5+value/length*distance_m for a, b, value in zip(hip, ankle, projected)]


def _runtime(job_id, actor_id, start, end, priority):
    import bpy
    from agent_character import resolve_actor
    from agent_motion_timeline import timeline_info
    actor = resolve_actor(job_id, actor_id)
    scene = bpy.context.scene
    if not scene.frame_start <= start <= end <= scene.frame_end:
        raise ValueError('set the scene frame range before compiling interaction events')
    maximum = max((float(item.get('priority', 0)) for item in timeline_info(job_id, actor_id)['events']), default=0.)
    chosen = maximum+1 if priority is None else _number(priority, 'priority')
    if chosen <= maximum:
        raise ValueError('interaction priority must exceed existing pose layers in its window')
    return actor, scene, chosen


def _bone(actor, short, evaluated=False):
    import bpy
    arm = actor['armature']
    if evaluated:
        arm = arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
    matches = [bone for bone in arm.pose.bones if bone.name.split(':')[-1] == short]
    if len(matches) != 1:
        raise ValueError('cannot resolve existing interaction bone: '+short)
    return arm, matches[0]


def _snapshot(actor, scene, first, last):
    import bpy
    from agent_motion_controls import read_pose
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    samples = {}
    try:
        for frame in range(first, last+1):
            scene.frame_set(frame); bpy.context.view_layer.update()
            arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
            root = actor['root'].evaluated_get(bpy.context.evaluated_depsgraph_get())
            samples[frame] = {'pose': read_pose(actor['armature'], evaluated=True), 'root': list(root.location),
                              'root_rotation_quaternion': list(root.matrix_basis.to_quaternion()),
                              'root_scale': list(root.scale),
                              'bone_world': {bone.name: arm.matrix_world @ bone.matrix for bone in arm.pose.bones},
                              'armature_world': arm.matrix_world.copy(), 'root_world': root.matrix_world.copy()}
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
    return samples


def _rotate(sample, actor, short, world_axis, radians):
    from mathutils import Quaternion, Vector
    _, bone = _bone(actor, short)
    axis = sample['bone_world'][bone.name].to_quaternion().inverted() @ Vector(world_axis)
    rotation = Quaternion(sample['pose'][bone.name]['rotation_quaternion']) @ Quaternion(axis.normalized(), radians)
    return bone.name, {'rotation_quaternion': list(rotation)}


def _surface_effector(actor, chain):
    """Choose a real skin vertex near a hand knuckle or at the foot sole."""
    import bpy
    _, end = _bone(actor, chain, evaluated=True)
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    center = arm.matrix_world @ end.head
    if chain.endswith('Hand'):
        try:
            _, knuckle = _bone(actor, chain+'Middle1', evaluated=True)
            center = arm.matrix_world @ knuckle.head
        except ValueError:
            pass
    candidates = []
    for mesh in actor['meshes']:
        groups = {group.index for group in mesh.vertex_groups if group.name.split(':')[-1].startswith(chain)}
        if chain.endswith('Foot'):
            groups |= {group.index for group in mesh.vertex_groups if group.name.split(':')[-1].startswith(chain.removesuffix('Foot')+'Toe')}
        indices = [vertex.index for vertex in mesh.data.vertices if sum(g.weight for g in vertex.groups if g.group in groups) >= .3]
        evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        geometry = evaluated.to_mesh()
        try:
            for index in indices:
                point = evaluated.matrix_world @ geometry.vertices[index].co
                score = (point-center).length_squared if chain.endswith('Hand') else point.z
                candidates.append((score, mesh.name, index, point.copy()))
        finally:
            evaluated.to_mesh_clear()
    if not candidates:
        raise ValueError('no actual weighted skin vertices for interaction chain '+chain)
    _, mesh_name, index, point = min(candidates, key=lambda item: item[0])
    return {'object': mesh_name, 'vertex_index': index}, list(point)


def _plan(actor_id, event_id, events, expectations=None):
    from agent_motion_timeline import validate_event
    from agent_contacts import _requirements_for_events
    events = [validate_event(event) for event in events]
    contracts = _requirements_for_events(actor_id, events)
    return {'ok': True, 'actor_id': actor_id, 'event_id': event_id, 'events': events,
            'expectations': {'contact_requirements': contracts, 'contact_ids': [item['id'] for item in contracts],
                             'impact_contact_ids': [], **(expectations or {})},
            'status': 'events_compiled_not_registered_or_baked', 'hit_confirmed': False,
            'scope': 'kinematic event generation on the existing registered character',
            'naturalness_verified': False, 'dynamics_verified': False}


def _compact_expectations(plan):
    source = plan.get('expectations', {})
    fields = ('impact_contact_ids', 'block_contact_ids', 'required_impact_contact_ids', 'miss_checks')
    compact = {key: copy.deepcopy(source[key]) for key in fields if source.get(key)}
    compact['contact_ids'] = list(dict.fromkeys([*source.get('contact_ids', []),
                              *(item['id'] for item in source.get('contact_requirements', []))]))
    return compact


def register_interaction(job_id, plan):
    """Register compiled events and keep small expectations across MCP calls.

    Replacing a stable plan ID removes its old, no-longer-used subevents on
    this actor. Commit separately, then verify_interaction(job_id) can measure
    all registered plans, including after saving/reopening the .blend file.
    """
    from agent_character import resolve_actor
    from agent_motion_timeline import register_event, remove_event
    if not plan.get('ok'):
        raise ValueError('cannot register an interaction plan that did not compile')
    actor_id, event_id = plan['actor_id'], _name(plan['event_id'], 'event_id')
    actor = resolve_actor(job_id, actor_id)
    records = json.loads(actor['root'].get(INTERACTION_STORE, '{}'))
    event_ids = [event['event_id'] for event in plan['events']]
    other_ids = {eid for name, record in records.items() if name != event_id for eid in record.get('event_ids', [])}
    for old_id in records.get(event_id, {}).get('event_ids', []):
        if old_id not in event_ids and old_id not in other_ids:
            remove_event(job_id, actor_id, old_id)
    for event in plan['events']:
        register_event(job_id, actor_id, event['event_id'], event)
    compact = _compact_expectations(plan)
    records[event_id] = {'event_ids': event_ids, **compact}
    actor['root'][INTERACTION_STORE] = json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    return {'ok': True, 'actor_id': actor_id, 'event_id': event_id,
            'status': 'interaction_registered_not_baked', 'events_count': len(event_ids),
            'contact_ids': compact['contact_ids'], 'impact_contact_ids': compact.get('impact_contact_ids', []),
            'block_contact_ids': compact.get('block_contact_ids', []), 'miss_checks_count': len(compact.get('miss_checks', []))}


def _registered_interactions(job_id):
    from agent_character import actor_list, resolve_actor
    plans = []
    for item in actor_list(job_id)['actors']:
        actor_id = item['actor_id']
        actor = resolve_actor(job_id, actor_id)
        for event_id, expectations in json.loads(actor['root'].get(INTERACTION_STORE, '{}')).items():
            plans.append({'ok': True, 'actor_id': actor_id, 'event_id': event_id, 'expectations': expectations})
    return plans


def _target_event(event_id, first, last, chain, target, priority, intent):
    side = chain.removesuffix('Hand').removesuffix('Foot')
    mask = [side.lower()+'_arm'] if chain.endswith('Hand') else [side+name for name in ('UpLeg', 'Leg', 'Foot', 'ToeBase')]
    return {'event_id': event_id, 'source': 'procedural', 'start_frame': first, 'end_frame': last,
            'mask': mask,
            'root_strategy': 'hold', 'priority': priority, 'targets': {chain: target}, 'intent': intent}


def _elbow_pole_point(shoulder, forward, forward_m=.10, drop_m=.30):
    point = [position+direction*forward_m for position, direction in zip(shoulder, forward)]
    point[2] -= drop_m
    return point


def _hand_elbow_target(actor, actor_id, hand):
    """Follow a chain-external shoulder; keep guard and strike elbow planes."""
    import bpy
    from mathutils import Vector
    side = hand.removesuffix('Hand')
    arm, upper = _bone(actor, side+'Arm', evaluated=True)
    _, shoulder = _bone(actor, side+'Shoulder', evaluated=True)
    root = actor['root'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    forward = root.matrix_world.to_quaternion() @ Vector((0, -1, 0))
    point = _elbow_pole_point(list(arm.matrix_world @ upper.head), list(forward))
    local = (arm.matrix_world @ shoulder.matrix).inverted() @ Vector(point)
    return {'pole_binding': {'actor_id': actor_id, 'bone': shoulder.name, 'local_point': list(local)},
            'pole_angle': 0. if side == 'Left' else -math.pi}


def _outcome_options(outcome, aim_offset_world, miss_clearance_m):
    """Execution outcomes selected by the director, never a prompt vocabulary."""
    if outcome not in ('hit', 'block', 'miss'):
        raise ValueError('outcome must be hit, block, or miss')
    offset = None if aim_offset_world is None else _vector(aim_offset_world, 'aim_offset_world')
    clearance = _number(miss_clearance_m, 'miss_clearance_m')
    if clearance <= 0:
        raise ValueError('miss_clearance_m must be positive')
    if outcome != 'miss' and offset is not None:
        raise ValueError('aim_offset_world is only used by a miss')
    if offset is not None and math.dist(offset, [0, 0, 0]) <= clearance:
        raise ValueError('miss aim offset must exceed its minimum clearance')
    return offset, clearance


def _validate_strike_target(binding):
    """A hit/block addresses geometry; held shields and new props are valid."""
    import bpy
    if 'actor_id' in binding:
        return
    obj = bpy.data.objects.get(binding['object'])
    if obj is None or obj.type != 'MESH':
        raise ValueError('hand strike target must bind a registered actor bone or an actual mesh, not a scene empty')


def _miss_sample(effector_position, target_position, offset, tolerance_m, clearance_m):
    """Measure one actual miss at the intended pass point, in world metres."""
    aim = [a+b for a, b in zip(target_position, offset)]
    clearance = math.dist(effector_position, target_position)
    aim_error = math.dist(effector_position, aim)
    return {'clearance_m': clearance, 'aim_error_m': aim_error,
            'passed': clearance >= clearance_m and aim_error <= tolerance_m}


def _measure_miss(job_id, requirement, detail=False):
    import bpy
    from agent_character import resolve_actor
    from agent_contacts import resolve_binding, resolve_effector
    scene = bpy.context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    samples = []
    try:
        actor = resolve_actor(job_id, requirement['actor_id'])
        first, last = requirement['frame_start'], requirement['frame_end']
        if not scene.frame_start <= first <= last <= scene.frame_end:
            raise ValueError('miss measurement window is outside the scene frame range')
        for frame in range(first, last+1):
            scene.frame_set(frame); bpy.context.view_layer.update()
            target = resolve_binding(job_id, requirement['target_binding'])
            effector = resolve_effector(job_id, actor, requirement['chain'], requirement['effector'])
            measured = _miss_sample(effector['position'], target['position'], requirement['aim_offset_world'],
                                    requirement['tolerance_m'], requirement['min_clearance_m'])
            samples.append({'frame': frame, **measured})
        return {'id': requirement['id'], 'outcome': 'miss', 'passed': all(row['passed'] for row in samples),
                'expected_frames': last-first+1, 'tested_frames': len(samples), 'coverage': 'full',
                'min_clearance_m': min(row['clearance_m'] for row in samples),
                'max_aim_error_m': max(row['aim_error_m'] for row in samples),
                'target_scope': target['scope'], 'effector_scope': effector['scope'],
                **({'frames': samples} if detail else {})}
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)


def guard_weight(frame, start_frame, raise_frames=0):
    """An immediate guard by default, with an optional explicit raising phase."""
    return 1. if raise_frames == 0 else smooth((frame-start_frame)/raise_frames)


def _guard_sample_frames(start_frame, raise_frames):
    return [start_frame] if raise_frames == 0 else [start_frame, start_frame+raise_frames]


def _finger_closure(actor, side, degrees):
    """Absolute fist FK from real rest geometry, independent of world axes."""
    from mathutils import Quaternion
    _, hand = _bone(actor, side+'Hand')
    _, index = _bone(actor, side+'HandIndex1')
    _, middle = _bone(actor, side+'HandMiddle1')
    _, pinky = _bone(actor, side+'HandPinky1')
    forward = (middle.bone.head_local-hand.bone.head_local).normalized()
    across = index.bone.head_local-pinky.bone.head_local
    across = (across-forward*across.dot(forward)).normalized()
    flex_axis = across*(1 if side == 'Right' else -1)
    palmar = flex_axis.cross(forward).normalized()
    rotations = {}
    for digit in ('Index', 'Middle', 'Ring', 'Pinky'):
        for joint in (1, 2, 3):
            _, bone = _bone(actor, side+'Hand'+digit+str(joint))
            inverse = bone.bone.matrix_local.to_3x3().inverted()
            axis = (inverse @ flex_axis).normalized()
            # MCP/PIP must fold far enough for the tips to return toward the
            # palm. A shallow 49/65/52-degree curl leaves a visible open claw.
            gain = (1.35 if joint == 1 else 1.50 if joint == 2 else 1.)
            rotations[bone.name] = Quaternion(axis, math.radians(degrees)*gain)
    palm_length = (middle.bone.head_local-hand.bone.head_local).length
    palm_target = hand.bone.head_local.lerp((index.bone.head_local+middle.bone.head_local)*.5, .70)
    palm_target += palmar*(palm_length*.25)
    parent_pose = hand.bone.matrix_local.to_quaternion()
    parent_rest = parent_pose.copy()
    for joint in (1, 2, 3):
        _, bone = _bone(actor, side+'HandThumb'+str(joint))
        if joint == 1:
            direction = palm_target-bone.bone.head_local
        else:
            direction = -across*.9+forward*(.25 if joint == 2 else -.15)+palmar*.20
        rest_direction = bone.bone.tail_local-bone.bone.head_local
        desired = rest_direction.normalized().rotation_difference(direction.normalized()) @ bone.bone.matrix_local.to_quaternion()
        relative_rest = parent_rest.inverted() @ bone.bone.matrix_local.to_quaternion()
        basis = (parent_pose @ relative_rest).inverted() @ desired
        rotations[bone.name] = Quaternion().slerp(basis, min(1., max(0., degrees/65)))
        parent_pose, parent_rest = desired, bone.bone.matrix_local.to_quaternion()
    return rotations


def guard_events(job_id, actor_id, event_id, start_frame, end_frame, *, raise_frames=0,
                 hand_distance_m=.22, hand_spread_m=.16, hand_drop_m=.08,
                 finger_curl_deg=65, priority=None, intent=None):
    """Compile a reusable two-hand guard and fist FK on the existing actor.

    This sets no root or leg channels. Guard points are world-metre offsets
    from the actual head; finger axes come from the actual rest hierarchy.
    Default raise_frames=0 supplies the guard immediately, including on a
    newly imported T-pose character. It emits one held key; explicit raising
    emits two smoothly interpolated keys. Head-local bindings follow motion
    without sampling the entire window. A hand strike retains this base.
    """
    import bpy
    from mathutils import Quaternion, Vector
    first, last = _frame(start_frame, 'start_frame'), _frame(end_frame, 'end_frame')
    rise = _frame(raise_frames, 'raise_frames')
    _name(event_id, 'event_id')
    if rise < 0 or rise > last-first:
        raise ValueError('raise_frames must fit within the guard window')
    distance, spread, drop = (_number(value, name) for value, name in
        ((hand_distance_m, 'hand_distance_m'), (hand_spread_m, 'hand_spread_m'), (hand_drop_m, 'hand_drop_m')))
    curl = _number(finger_curl_deg, 'finger_curl_deg')
    actor, scene, chosen = _runtime(job_id, actor_id, first, last, priority)
    samples = _snapshot(actor, scene, first, first)
    key_frames = _guard_sample_frames(first, rise)
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    try:
        scene.frame_set(first); bpy.context.view_layer.update()
        arm, head = _bone(actor, 'Head', evaluated=True)
        head_center = arm.matrix_world @ head.head.lerp(head.tail, .5)
        root_rotation = Quaternion(samples[first]['root_rotation_quaternion'])
        forward, lateral = root_rotation @ Vector((0, -1, 0)), root_rotation @ Vector((1, 0, 0))
        finger_rotations = {}
        targets = {}
        head_matrix = arm.matrix_world @ head.matrix
        for side, sign in (('Left', 1), ('Right', -1)):
            finger_rotations.update(_finger_closure(actor, side, curl))
            hand = side+'Hand'; arm, bone = _bone(actor, hand, evaluated=True)
            _, middle = _bone(actor, hand+'Middle1', evaluated=True)
            _, shoulder = _bone(actor, side+'Arm', evaluated=True)
            wrist = arm.matrix_world @ bone.head
            finger_forward = arm.matrix_world @ middle.head-wrist
            initial_rotation = (arm.matrix_world @ bone.matrix).to_quaternion()
            hand_rotation = finger_forward.normalized().rotation_difference(forward.normalized()) @ initial_rotation
            destination = head_center+forward*distance+lateral*(sign*spread)+Vector((0, 0, -drop))
            keys = []
            for frame in key_frames:
                weight = guard_weight(frame, first, rise)
                local = head_matrix.inverted() @ wrist.lerp(destination, weight)
                keys.append({'frame': frame, 'binding': {'actor_id': actor_id, 'bone': head.name, 'local_point': list(local)},
                             'rotation_quaternion': list(initial_rotation.slerp(hand_rotation, weight)),
                             'influence': 1, 'interpolation': 'smooth'})
            targets[hand] = {'keyframes': keys, 'contact': False,
                             **_hand_elbow_target(actor, actor_id, hand)}
        pose_keys = []
        for frame in key_frames:
            weight = guard_weight(frame, first, rise)
            pose_keys.append({'frame': frame, 'pose': {
                name: {'rotation_quaternion': list(Quaternion(samples[first]['pose'][name]['rotation_quaternion']).slerp(closed, weight))}
                for name, closed in finger_rotations.items()}, 'interpolation': 'smooth'})
        source_text = str(intent or 'Two-hand head guard with bent elbows and a basic fist shape')
        event = {'event_id': event_id, 'source': 'procedural', 'start_frame': first, 'end_frame': last,
                 'mask': ['left_arm', 'right_arm'], 'root_strategy': 'hold', 'priority': chosen,
                 'keyframes': pose_keys, 'targets': targets, 'intent': source_text}
        return _plan(actor_id, event_id, [event], {'base_posture': 'procedural_guard',
                     'root_translation_generated': False, 'leg_pose_generated': False,
                     'elbow_policy': 'same_side_Shoulder_binding_with_Beta_pole_angle',
                     'finger_pose': 'absolute_rest_geometry_flexion', 'raise_frames': rise})
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)


def hand_strike_events(job_id, actor_id, event_id, target_binding, *, start_frame, impact_frame,
                       end_frame, hand='RightHand', effector=None, effector_point=None,
                       intensity=.6, windup_m=.08, arc_m=.03, torso_twist_deg=12,
                       hip_twist_deg=5, contact_frames=1, tolerance_m=.02, priority=None,
                       intent=None, outcome='hit', aim_offset_world=None, miss_clearance_m=.08):
    """Compile preparation, 1–2 contact frames and release without a punch clip.

    ``target_binding`` uses P1 object/bone-local units or a real mesh vertex.
    ``effector_point`` uses the hand bone's actual local units; an ``effector``
    mesh vertex is world-evaluated. Remaining distances are world metres.
    ``outcome`` is execution intent, not proof: hit requires a measured strike;
    block binds the actual guard surface/prop and registers kind=block. For a
    miss, aim_offset_world defaults to 0.20m sideways from the attack direction;
    verification measures the actual pass-point clearance, not whole-body CCD.
    """
    import bpy
    from mathutils import Vector
    from agent_contacts import validate_binding, resolve_binding, resolve_effector
    first, impact, last = (_frame(value, name) for value, name in
                           ((start_frame, 'start_frame'), (impact_frame, 'impact_frame'), (end_frame, 'end_frame')))
    count = _frame(contact_frames, 'contact_frames')
    if count not in (1, 2) or not first+2 <= impact or not impact+count < last:
        raise ValueError('hand strike needs preparation, 1–2 impact frames, and a recovery window')
    if hand not in ('LeftHand', 'RightHand'):
        raise ValueError('hand addresses LeftHand or RightHand rig chain')
    offset, clearance = _outcome_options(outcome, aim_offset_world, miss_clearance_m)
    strength = _number(intensity, 'intensity')
    if not 0 <= strength <= 1 or _number(tolerance_m, 'tolerance_m') <= 0:
        raise ValueError('intensity must be in [0,1] and contact tolerance positive')
    windup, arc = _number(windup_m, 'windup_m'), _number(arc_m, 'arc_m')
    twist, hip_twist = _number(torso_twist_deg, 'torso_twist_deg'), _number(hip_twist_deg, 'hip_twist_deg')
    _name(event_id, 'event_id'); binding = validate_binding(target_binding)
    actor, scene, chosen = _runtime(job_id, actor_id, first, last, priority)
    samples = _snapshot(actor, scene, first, last)
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    release = impact+count-1
    source_text = str(intent or 'Procedural hand preparation, '+outcome+', and recovery')
    try:
        scene.frame_set(first); bpy.context.view_layer.update()
        other_hand = 'LeftHand' if hand == 'RightHand' else 'RightHand'
        other_side = other_hand.removesuffix('Hand')
        other_arm, other_upper = _bone(actor, other_side+'Arm', evaluated=True)
        _, other_fore = _bone(actor, other_side+'ForeArm', evaluated=True)
        _, other_end = _bone(actor, other_hand, evaluated=True)
        a, b, c = [other_arm.matrix_world @ bone.head for bone in (other_upper, other_fore, other_end)]
        other_length = (b-a).length+(c-b).length
        if (c-a).length > other_length*.85 and abs(c.z-a.z) < .12:
            return {'ok': False, 'actor_id': actor_id, 'event_id': event_id, 'events': [],
                    'hit_confirmed': False, 'status': 'base_pose_required',
                    'unprepared_chain': other_hand,
                    'suggestion': 'Establish and commit an actual neutral/guard base pose first; the non-attacking arm is still extended horizontally.'}
        if effector is None and effector_point is None:
            effector, _ = _surface_effector(actor, hand)
        if effector is not None and effector_point is not None:
            raise ValueError('choose an actual skin effector or a bone-local effector point')
        elbow_target = _hand_elbow_target(actor, actor_id, hand)
        effector_spec = {'effector': effector} if effector is not None else {'effector_point': _vector(effector_point, 'effector_point')}
        initial = Vector(resolve_effector(job_id, actor, hand, effector_spec)['position'])
        initial_arm, initial_hand = _bone(actor, hand, evaluated=True)
        _, knuckle = _bone(actor, hand+'Middle1', evaluated=True)
        initial_matrix = initial_arm.matrix_world @ initial_hand.matrix
        initial_rotation = initial_matrix.to_quaternion()
        initial_wrist = initial_arm.matrix_world @ initial_hand.head
        metacarpal = initial_arm.matrix_world @ knuckle.head-initial_wrist
        initial_offset = initial-initial_wrist
        scene.frame_set(impact); bpy.context.view_layer.update()
        _validate_strike_target(binding)
        target = Vector(resolve_binding(job_id, binding)['position'])
        direction = target-initial
        if direction.length < .001:
            raise ValueError('hand already coincides with target; provide a meaningful preparation direction')
        direction.normalize()
        lateral = Vector((direction.y, -direction.x, 0))
        if lateral.length < .001:
            lateral = Vector((1, 0, 0))
        lateral.normalize(); side = 1 if hand == 'RightHand' else -1
        if outcome == 'miss':
            offset = list(lateral*(side*.20)) if offset is None else offset
            target += Vector(offset)
        direction = (target-initial).normalized()
        impact_rotation = metacarpal.normalized().rotation_difference(direction) @ initial_rotation
        arm, upper = _bone(actor, hand.removesuffix('Hand')+'Arm', evaluated=True)
        _, fore = _bone(actor, hand.removesuffix('Hand')+'ForeArm', evaluated=True)
        _, end = _bone(actor, hand, evaluated=True)
        shoulder, elbow, wrist = [arm.matrix_world @ bone.head for bone in (upper, fore, end)]
        reach = (elbow-shoulder).length+(wrist-elbow).length
        # The planned fist orientation changes its skin/local-point offset
        # from the wrist. Reach must use that offset, rather than the old
        # guard offset which can send the IK goal beyond arm length.
        planned_offset = impact_rotation @ (initial_rotation.inverted() @ initial_offset)
        distance = (target-planned_offset-shoulder).length
        if distance > reach+.005:
            return {'ok': False, 'actor_id': actor_id, 'event_id': event_id, 'events': [], 'hit_confirmed': False,
                    'status': 'approach_required', 'target_distance_m': distance, 'arm_reach_m': reach,
                    'missing_reach_m': distance-reach, 'suggestion': 'Use support_step_events or another trajectory tool to approach, then recompile; the actor was not moved.'}
        prep_keys, recovery_keys, pose_keys, miss_keys = [], [], [], []
        for frame in range(first, last+1):
            amount = strike_progress(frame, first, impact, release, last)
            # Preserve the actual already-established guard as the FK release
            # base. A zero-influence preparation/recovery must not reveal an
            # older T pose beneath the hand-target overlay.
            arm_prefix = hand.removesuffix('Hand')
            rotations = {name: copy.deepcopy(channels) for name, channels in samples[frame]['pose'].items()
                         if name.split(':')[-1] in (arm_prefix+'Shoulder', arm_prefix+'Arm', arm_prefix+'ForeArm')
                         or name.split(':')[-1].startswith(hand)}
            for bone_name, degrees in (('Spine', twist*.25), ('Spine1', twist*.35),
                                       ('Spine2', twist*.40), ('Hips', hip_twist),
                                       (hand.removesuffix('Hand')+'Shoulder', twist*.25)):
                name, channels = _rotate(samples[frame], actor, bone_name, (0, 0, 1),
                                          math.radians(degrees)*side*strength*amount)
                rotations[name] = channels
            pose_keys.append({'frame': frame, 'pose': rotations, 'interpolation': 'linear'})
            if frame < impact:
                t = (frame-first)/(impact-first)
                point = initial.lerp(target, amount)-direction*(windup*math.sin(math.pi*t))
                point += lateral*(side*arc*math.sin(math.pi*t))
                prep_keys.append({'frame': frame, 'position': list(point),
                                  'rotation_quaternion': list(initial_rotation.slerp(impact_rotation, amount)),
                                  'influence': smooth(t/.20), 'interpolation': 'linear'})
            elif frame <= release and outcome == 'miss':
                scene.frame_set(frame); bpy.context.view_layer.update()
                point = Vector(resolve_binding(job_id, binding)['position'])+Vector(offset)
                miss_keys.append({'frame': frame, 'position': list(point), 'rotation_quaternion': list(impact_rotation),
                                  'influence': 1, 'interpolation': 'linear'})
            elif frame > release:
                t = (frame-release)/(last-release)
                scene.frame_set(frame); bpy.context.view_layer.update()
                baseline = Vector(resolve_effector(job_id, actor, hand, effector_spec)['position'])
                point = target.lerp(baseline, smooth(t))
                _, hand_bone = _bone(actor, hand)
                baseline_rotation = samples[frame]['bone_world'][hand_bone.name].to_quaternion()
                recovery_keys.append({'frame': frame, 'position': list(point),
                                      'rotation_quaternion': list(impact_rotation.slerp(baseline_rotation, smooth(t))),
                                      'influence': 1-smooth(t), 'interpolation': 'linear'})
        events = [{'event_id': event_id+':body', 'source': 'procedural', 'start_frame': first,
                   'end_frame': last, 'mask': ['upper_body', 'Hips'], 'priority': chosen,
                   'root_strategy': 'hold', 'keyframes': pose_keys, 'intent': source_text}]
        events.append(_target_event(event_id+':prepare', first, impact-1, hand,
                                   {**effector_spec, **elbow_target, 'keyframes': prep_keys}, chosen+1, source_text))
        contact_id = actor_id+':'+event_id+':impact'
        contact = False if outcome == 'miss' else {'id': contact_id, 'start_frame': impact, 'end_frame': release,
                                                  'tolerance_m': tolerance_m,
                                                  'kind': 'block' if outcome == 'block' else 'strike',
                                                  'source_text': source_text}
        impact_target = {'keyframes': miss_keys} if outcome == 'miss' else {
            'binding': binding, 'influence': 1, 'rotation_quaternion': list(impact_rotation)}
        events.append(_target_event(event_id+':impact', impact, release, hand,
                      {**effector_spec, **elbow_target, **impact_target, 'contact': contact}, chosen+1, source_text))
        events.append(_target_event(event_id+':recovery', release+1, last, hand,
                                   {**effector_spec, **elbow_target, 'keyframes': recovery_keys}, chosen+1, source_text))
        miss_checks = [] if outcome != 'miss' else [{'id': actor_id+':'+event_id+':miss', 'actor_id': actor_id,
                       'chain': hand, 'frame_start': impact, 'frame_end': release, 'target_binding': binding,
                       'effector': effector_spec, 'aim_offset_world': offset, 'min_clearance_m': clearance,
                       'tolerance_m': tolerance_m}]
        return _plan(actor_id, event_id, events, {'impact_contact_ids': [contact_id] if outcome == 'hit' else [],
                     'block_contact_ids': [contact_id] if outcome == 'block' else [],
                     'miss_checks': miss_checks, 'outcome': outcome, 'recoil_allowed': outcome == 'hit',
                     'impact_frame': impact, 'release_frame': release, 'effector': effector_spec,
                     'attack_source': 'procedural_existing_pose_and_FK_IK', 'arm_reach_m': reach,
                     'planned_impact_hand_rotation_quaternion': list(impact_rotation),
                     'hand_orientation_policy': 'actual_metacarpal_aligned_to_punch_path_then_recover',
                     'elbow_policy': 'same_side_Shoulder_binding_with_Beta_pole_angle',
                     'initial_target_distance_m': distance, 'support_verified': False})
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)


def recoil_events(job_id, actor_id, event_id, *, impact_frame, end_frame, direction_world,
                  amount=.5, contact_id=None, priority=None, intent=None, outcome='hit'):
    """Compile an in-place upper-body response; combine with steps for retreat."""
    _outcome_options(outcome, None, .08)
    if outcome != 'hit':
        return _plan(actor_id, event_id, [], {'outcome': outcome, 'reaction_skipped': True,
                     'reason': 'block and miss do not produce a struck-body recoil'})
    from mathutils import Vector
    first, last = _frame(impact_frame, 'impact_frame'), _frame(end_frame, 'end_frame')
    _name(event_id, 'event_id'); _name(contact_id, 'contact_id')
    strength = _number(amount, 'amount')
    if not 0 <= strength <= 1 or first >= last:
        raise ValueError('recoil needs amount in [0,1] and impact < end')
    direction = Vector(_vector(direction_world, 'direction_world'))
    if direction.xy.length < 1e-6:
        raise ValueError('recoil direction needs a planar component')
    direction.z = 0; direction.normalize()
    axis = Vector((-direction.y, direction.x, 0))
    actor, scene, chosen = _runtime(job_id, actor_id, first, last, priority)
    samples = _snapshot(actor, scene, first, last)
    fps = scene.render.fps/scene.render.fps_base
    keys = []
    for frame in range(first, last+1):
        weight = recoil_amount(frame, first, last, fps)*strength
        pose = {}
        for short, degrees in (('Spine', 5), ('Spine1', 7), ('Spine2', 6), ('Neck', 4), ('Head', 8)):
            name, channels = _rotate(samples[frame], actor, short, axis, math.radians(degrees)*weight)
            pose[name] = channels
        keys.append({'frame': frame, 'pose': pose, 'interpolation': 'linear'})
    event = {'event_id': event_id, 'source': 'procedural', 'start_frame': first, 'end_frame': last,
             'mask': ['upper_body'], 'root_strategy': 'hold', 'priority': chosen,
             'keyframes': keys, 'requires_contact_id': contact_id,
             'intent': str(intent or 'Contact-gated short head/torso recoil')}
    return _plan(actor_id, event_id, [event], {'required_impact_contact_ids': [contact_id],
                 'reaction_starts_after_frame': first, 'root_translation_generated': False})


def support_step_events(job_id, actor_id, event_id, steps, root_path, *, floor_z=0,
                        foot_clearance_m=.08, priority=None, intent=None):
    """Compile explicit world-space steps without reversing a forward walk clip.

    Steps use {foot,lift_frame,land_frame,position:[world metres]}; positions
    locate actual sole skin contact points, not ankle bone origins. The root
    path uses the existing {frame,root:[world metres]} timeline convention.
    Root orientation is preserved unless the path explicitly provides it.
    """
    import bpy
    from mathutils import Matrix, Quaternion, Vector
    from agent_motion_timeline import sample_keyframes
    _name(event_id, 'event_id')
    if not isinstance(root_path, (list, tuple)) or len(root_path) < 2:
        raise ValueError('root_path needs at least two frame/root samples')
    path = []
    for item in root_path:
        row = copy.deepcopy(item)
        row['frame'] = _frame(row.get('frame'), 'root_path.frame')
        row['root'] = _vector(row.get('root'), 'root_path.root')
        path.append(row)
    path.sort(key=lambda row: row['frame'])
    if len({row['frame'] for row in path}) != len(path):
        raise ValueError('duplicate root_path frame')
    first, last = path[0]['frame'], path[-1]['frame']
    schedule = normalize_steps(steps, first, last)
    ground, clearance = _number(floor_z, 'floor_z'), _number(foot_clearance_m, 'foot_clearance_m')
    if clearance <= 0:
        raise ValueError('foot_clearance_m must be positive')
    if any(abs(step['position'][2]-ground) > .005 for step in schedule):
        raise ValueError('this optional step helper uses sole landing points on one flat floor')
    actor, scene, chosen = _runtime(job_id, actor_id, first, last, priority)
    if actor['root'].parent is not None or max(abs(value-1) for value in actor['root'].scale) > 1e-6:
        raise ValueError('world-space support steps need an unparented, unit-scale actor root')
    samples = _snapshot(actor, scene, first, last)
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    try:
        scene.frame_set(first); bpy.context.view_layer.update()
        anchors, effectors, rotations, sole_offsets, leg_lengths = {}, {}, {}, {}, {}
        for foot in ('LeftFoot', 'RightFoot'):
            effectors[foot], anchors[foot] = _surface_effector(actor, foot)
            if abs(anchors[foot][2]-ground) > .04:
                raise ValueError('step helper needs an initially grounded stance; settle the existing feet first')
            arm, bone = _bone(actor, foot, evaluated=True)
            rotations[foot] = list((arm.matrix_world @ bone.matrix).to_quaternion())
            sole_offsets[foot] = Vector(anchors[foot])-(arm.matrix_world @ bone.head)
            anchors[foot][2] = ground
            _, upper = _bone(actor, foot.removesuffix('Foot')+'UpLeg', evaluated=True)
            _, knee = _bone(actor, foot.removesuffix('Foot')+'Leg', evaluated=True)
            a, b, c = [arm.matrix_world @ item.head for item in (upper, knee, bone)]
            leg_lengths[foot] = (a-b).length+(b-c).length
        base_keys, planned_hips, planned_forward = [], {}, {}
        _, hips = _bone(actor, 'Hips')
        for frame in range(first, last+1):
            state = sample_keyframes(path, frame)
            root_rotation = state.get('root_rotation_quaternion', samples[first]['root_rotation_quaternion'])
            new_root = Matrix.LocRotScale(Vector(state['root']), Quaternion(root_rotation), Vector(samples[first]['root_scale']))
            delta = new_root @ samples[frame]['root_world'].inverted()
            hips_world = {}
            lowering = 0.
            for foot in ('LeftFoot', 'RightFoot'):
                _, upper = _bone(actor, foot.removesuffix('Foot')+'UpLeg')
                hip = delta @ samples[frame]['bone_world'][upper.name].translation
                hips_world[foot] = hip
                ankle = Vector(foot_state(schedule, foot, frame, anchors[foot], clearance)['position'])-sole_offsets[foot]
                lowering = max(lowering, support_lowering(hip, ankle, leg_lengths[foot]))
            planned_hips[frame] = {foot: list(hip-Vector((0, 0, lowering))) for foot, hip in hips_world.items()}
            planned_forward[frame] = list(Quaternion(root_rotation) @ Vector((0, -1, 0)))
            pose = copy.deepcopy(samples[frame]['pose'])
            if lowering:
                inverse = samples[frame]['armature_world'].inverted()
                matrix = inverse @ samples[frame]['bone_world'][hips.name]
                matrix.translation += inverse.to_3x3() @ Vector((0, 0, -lowering))
                kwargs = {}
                if hips.parent:
                    kwargs = {'parent_matrix': inverse @ samples[frame]['bone_world'][hips.parent.name],
                              'parent_matrix_local': hips.parent.bone.matrix_local}
                basis = hips.bone.convert_local_to_pose(matrix, hips.bone.matrix_local, invert=True, **kwargs)
                pose[hips.name]['location'] = list(basis.to_translation())
            base_keys.append({'frame': frame, 'pose': pose, 'root': state['root'],
                'root_rotation_quaternion': root_rotation,
                'root_scale': samples[first]['root_scale'], 'interpolation': 'linear'})
        source_text = str(intent or 'Explicit alternating support steps with a root trajectory')
        events = [{'event_id': event_id+':body', 'source': 'procedural', 'start_frame': first,
                   'end_frame': last, 'root_strategy': 'trajectory', 'priority': chosen,
                   'keyframes': base_keys, 'intent': source_text}]
        phase_records, support_windows = [], []
        for foot in ('LeftFoot', 'RightFoot'):
            segment, previous_stance = [], None
            def append_segment(rows, stance, index):
                if not rows:
                    return
                begin, finish = rows[0]['frame'], rows[-1]['frame']
                target = {'effector': effectors[foot], 'rotation_quaternion': rotations[foot],
                          'keyframes': rows}
                suffix = ':support:' if stance else ':swing:'
                if stance:
                    cid = actor_id+':'+event_id+suffix+foot+':'+str(index)
                    target['contact'] = {'id': cid, 'start_frame': begin, 'end_frame': finish,
                                         'tolerance_m': .01, 'kind': 'support'}
                    support_windows.append({'id': cid, 'foot': foot, 'start_frame': begin, 'end_frame': finish})
                events.append(_target_event(event_id+suffix+foot+':'+str(index), begin, finish,
                                            foot, target, chosen+1, source_text))
            index = 0
            for frame in range(first, last+1):
                state = foot_state(schedule, foot, frame, anchors[foot], clearance)
                if previous_stance is not None and state['stance'] != previous_stance:
                    append_segment(segment, previous_stance, index); segment = []; index += 1
                ankle = Vector(state['position'])-sole_offsets[foot]
                pole = support_pole(planned_hips[frame][foot], list(ankle), planned_forward[frame])
                segment.append({'frame': frame, 'position': state['position'], 'pole': pole,
                                'influence': 1, 'interpolation': 'linear'})
                phase_records.append({'frame': frame, 'foot': foot, **state})
                previous_stance = state['stance']
            append_segment(segment, previous_stance, index)
        return _plan(actor_id, event_id, events, {'support_windows': support_windows,
                     'foot_phases': phase_records, 'floor_z': ground, 'root_path': path,
                     'facing_policy': 'preserve_start_orientation_unless_explicit_root_rotation',
                     'knee_pole_policy': 'planned_body_forward_projected_across_each_leg_axis',
                     'step_sources': 'procedural_world_foot_targets_no_reversed_walk_clip'})
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)


def verify_interaction(job_id, plans=None, *, detail=False):
    """Measure hit/block/miss; omitted plans use the actor-root registrations."""
    from agent_contacts import check_contacts
    if plans is None:
        plans = _registered_interactions(job_id)
    if isinstance(plans, dict):
        plans = [plans]
    if not isinstance(plans, (list, tuple)) or not plans:
        raise ValueError('verify_interaction needs compiled plans or plans saved by register_interaction')
    expected, impact_ids, block_ids, miss_checks = set(), [], [], []
    for plan in plans:
        if not plan.get('ok'):
            raise ValueError('cannot verify an interaction plan that did not compile')
        expectations = plan.get('expectations', {})
        expected.update(expectations.get('contact_ids', []))
        expected.update(requirement['id'] for requirement in expectations.get('contact_requirements', []))
        impact_ids.extend(expectations.get('impact_contact_ids', []))
        impact_ids.extend(expectations.get('required_impact_contact_ids', []))
        block_ids.extend(expectations.get('block_contact_ids', []))
        miss_checks.extend(expectations.get('miss_checks', []))
    if not expected and not impact_ids and not miss_checks:
        raise ValueError('interaction has no geometric contact or miss expectations to verify')
    mismatches = []
    report = check_contacts(job_id, detail=detail)
    summaries = {item['id']: item for item in report.get('contacts', [])}
    required = set(expected)|set(impact_ids)|set(block_ids)
    for cid in sorted(set(impact_ids)):
        if summaries.get(cid, {}).get('kind') != 'strike':
            mismatches.append({'check': 'interaction_impact_contract_missing_or_not_strike', 'id': cid})
    for cid in sorted(set(block_ids)):
        if summaries.get(cid, {}).get('kind') != 'block':
            mismatches.append({'check': 'interaction_block_contract_missing_or_not_block', 'id': cid})
    for cid in sorted(required):
        measured = summaries.get(cid)
        if (not measured or not measured.get('passed') or measured.get('coverage') != 'full' or
                measured.get('tested_frames') != measured.get('expected_frames')):
            mismatches.append({'check': 'interaction_contact_not_verified', 'id': cid})
    miss_reports = []
    for requirement in miss_checks:
        try:
            measured = _measure_miss(job_id, requirement, detail)
            miss_reports.append(measured)
            if not measured['passed'] or measured['coverage'] != 'full' or measured['tested_frames'] != measured['expected_frames']:
                mismatches.append({'check': 'interaction_miss_not_verified', 'id': requirement['id']})
        except Exception as exc:
            mismatches.append({'check': 'interaction_miss_measurement_error', 'id': requirement['id'], 'error': str(exc)})
    passed = bool(report.get('ok') and report.get('passed') and not mismatches)
    return {'ok': True, 'passed': passed, 'hit_confirmed': bool(impact_ids) and passed,
            'block_confirmed': bool(block_ids) and passed, 'miss_confirmed': bool(miss_checks) and passed,
            'impact_contact_ids': sorted(set(impact_ids)), 'block_contact_ids': sorted(set(block_ids)),
            'failures': mismatches, 'contact_report': report, 'miss_reports': miss_reports,
            'scope': 'actual impact/block/support windows and miss pass-point clearance; no whole-body swept collision test',
            'naturalness_verified': False, 'dynamics_verified': False}
