"""FK pose shaping for the Mixamo Beta window-vault executor.

Call after Actor.set_pose on every frame. This module changes bone rotations
only: the executor owns the root trajectory, pelvis translation, IK targets,
constraints, support phases and collision/clearance acceptance.

Calibrated on the real Actor rig, not on a substitute skeleton. In its world
basis positive lateral-axis rotation leans the torso forward; negative rotates
the thighs forward and positive folds the knees. Source idle has staggered legs,
so a smooth neutralization is needed before applying the compact pose.
"""
import math

import bpy
from mathutils import Quaternion, Vector

from duel_scene import PREFIX, point, rotate_world


def _smooth(value):
    value = min(1.0, max(0.0, value))
    return value * value * (3 - 2 * value)


def _ramp(phase, start, end):
    return _smooth((phase - start) / (end - start))


def _curve(phase, keys):
    if phase <= keys[0][0]:
        return keys[0][1]
    for (start, first), (end, second) in zip(keys, keys[1:]):
        if phase <= end:
            return first + (second - first) * _ramp(phase, start, end)
    return keys[-1][1]


def _finite(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + ' must be a finite number')
    if not low <= value <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return float(value)


def _neutralize(actor, name, weight):
    bone = actor.arm.pose.bones.get(PREFIX + name)
    if bone is not None:
        bone.rotation_mode = 'QUATERNION'
        bone.rotation_quaternion = bone.rotation_quaternion.slerp(Quaternion(), weight)


def _turn_direction(actor, bone_name, current, desired, weight=1.0, max_angle=None):
    if weight <= 0 or current.length < 1e-6 or desired.length < 1e-6:
        return
    delta = current.normalized().rotation_difference(desired.normalized())
    axis, angle = delta.to_axis_angle()
    if max_angle is not None:
        angle = min(angle, max_angle)
    if angle > 1e-6:
        rotate_world(actor, bone_name, axis, angle * weight)
        bpy.context.view_layer.update()


def _arm_to_target(actor, side, contact, weight):
    """Aim a FK arm at the support point; final hard contact remains caller IK."""
    shoulder = point(actor.arm, side + 'Arm')
    elbow = point(actor.arm, side + 'ForeArm')
    wrist = point(actor.arm, side + 'Hand')
    tip = point(actor.arm, side + 'HandMiddle1')
    upper, lower = (elbow - shoulder).length, (wrist - elbow).length
    wrist_target = contact - actor.forward * (tip - wrist).length
    delta = wrist_target - shoulder
    distance = delta.length
    if distance < .01:
        return {'distance_m': distance, 'chain_length_m': upper + lower, 'reachable': False}
    direction = delta / distance
    clamped = min((upper + lower) * .985, max(abs(upper - lower) + .001, distance))
    lateral = actor.lateral * (1 if side == 'Left' else -1)
    preferred = lateral * .65 - actor.forward * .5
    bend = preferred - direction * preferred.dot(direction)
    if bend.length < .01:
        bend = Vector((0, 0, 1)) - direction * direction.z
    bend.normalize()
    projected = (upper * upper - lower * lower + clamped * clamped) / (2 * clamped)
    height = math.sqrt(max(0, upper * upper - projected * projected))
    elbow_target = shoulder + direction * projected + bend * height
    _turn_direction(actor, side + 'Arm', elbow - shoulder, elbow_target - shoulder, weight)
    elbow = point(actor.arm, side + 'ForeArm')
    wrist = point(actor.arm, side + 'Hand')
    _turn_direction(actor, side + 'ForeArm', wrist - elbow,
                    shoulder + direction * clamped - elbow, weight)
    wrist = point(actor.arm, side + 'Hand')
    tip = point(actor.arm, side + 'HandMiddle1')
    # Fingers point across the sill; the executor copies this FK hand rotation
    # onto its support goal and uses the marker offset for wrist placement.
    _turn_direction(actor, side + 'Hand', tip - wrist, actor.forward, weight)
    return {'distance_m': distance, 'chain_length_m': upper + lower,
            'reachable': distance <= (upper + lower) * .985}


def apply_vault_pose(actor, phase, *, tuck=1.0, lean=1.0, clearance=None):
    """Apply continuous takeoff/support/tuck/descent/landing FK rotations.

    ``phase`` spans lift-off through settled landing, 0..1. ``tuck`` and
    ``lean`` scale the calibrated angles. Optional ``clearance`` keys:

    - hip_max_rad / knee_max_rad / lean_max_rad: calibrated compact angles.
    - hand_release_start / hand_release_end: support release schedule.
    - tuck_release_start (or tuck_hold_end): compact pose end, default .72.
    - hand_targets: {"Left": world_contact, "Right": world_contact}.

    Returned hand/foot weights are recommendations, not installed constraints.
    The executor must keep support points reachable and check the actual mesh
    against the sill/frame, including during transitions. This pose generator
    alone does not guarantee a successful or collision-free vault.
    """
    phase = _finite(phase, 'phase', 0, 1)
    tuck = _finite(tuck, 'tuck', .5, 1.2)
    lean = _finite(lean, 'lean', .5, 1.2)
    if clearance is None:
        clearance = {}
    if not isinstance(clearance, dict):
        raise ValueError('clearance must be a dictionary')
    hip_max = _finite(clearance.get('hip_max_rad', 2.0), 'hip_max_rad', 1.5, 2.25)
    knee_max = _finite(clearance.get('knee_max_rad', 2.5), 'knee_max_rad', 2.0, 2.65)
    lean_max = _finite(clearance.get('lean_max_rad', 1.9), 'lean_max_rad', 1.0, 2.1)
    release_start = _finite(clearance.get('hand_release_start', .48), 'hand_release_start', .3, .7)
    release_end = _finite(clearance.get('hand_release_end', .64), 'hand_release_end', release_start + .01, .8)
    tuck_end = _finite(clearance.get('tuck_release_start', clearance.get('tuck_hold_end', .72)),
                       'tuck_release_start', .6, .78)

    neutral = _ramp(phase, .02, .2) * (1 - _ramp(phase, .87, 1))
    for bone in ('Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head',
                 'LeftShoulder', 'RightShoulder'):
        _neutralize(actor, bone, neutral)
    for side in ('Left', 'Right'):
        # A rest-aligned arm avoids an ambiguous 180-degree aim from the
        # hanging source arm after the torso folds past horizontal.
        for bone in ('Arm', 'ForeArm', 'Hand'):
            _neutralize(actor, side + bone, neutral)
        for bone in ('UpLeg', 'Leg', 'Foot', 'ToeBase', 'Toe_End'):
            _neutralize(actor, side + bone, neutral)
        for finger in ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky'):
            for index in range(1, 5):
                _neutralize(actor, f'{side}Hand{finger}{index}', neutral)
    bpy.context.view_layer.update()

    body_pitch = _curve(phase, [(0, 0), (.1, .45), (.22, lean_max),
                               (.57, lean_max), (.82, .25), (.93, .38), (1, 0)]) * lean
    hip_flex = _curve(phase, [(0, 0), (.1, .35), (.22, hip_max * .8),
                             (.31, hip_max), (tuck_end, hip_max), (.86, .45),
                             (.93, .62), (1, 0)]) * tuck
    knee_flex = _curve(phase, [(0, 0), (.1, .6), (.22, knee_max * .9),
                              (.3, knee_max), (tuck_end, knee_max), (.86, .9),
                              (.93, 1.2), (1, 0)]) * tuck
    # Share the fold between pelvis and spine. Bending only Spine leaves its
    # fixed base above the hips and makes the sill unreachable by this rig's
    # 0.562 m arms while the pelvis is high enough for foot clearance.
    pelvis_pitch = body_pitch * .58
    rotate_world(actor, 'Hips', actor.lateral, pelvis_pitch)
    bpy.context.view_layer.update()
    for spine, fraction in (('Spine', .27), ('Spine1', .11), ('Spine2', .04)):
        rotate_world(actor, spine, actor.lateral, body_pitch * fraction)
        bpy.context.view_layer.update()
    for side in ('Left', 'Right'):
        # Hip flexion is relative to the tilted pelvis. Counter-rotating the
        # pelvis as well would demand almost 180 degrees at this joint and
        # lift the knees above the head. The forward pelvic fold already lifts
        # the folded shins; do not add it again to the hip flexion.
        rotate_world(actor, side + 'UpLeg', actor.lateral, -hip_flex)
        bpy.context.view_layer.update()
        rotate_world(actor, side + 'Leg', actor.lateral, knee_flex)
        bpy.context.view_layer.update()
        ankle = point(actor.arm, side + 'Foot')
        toe = point(actor.arm, side + 'ToeBase')
        # Toe tips must not hang below the sill while the knees are tucked.
        _turn_direction(actor, side + 'Foot', toe - ankle, actor.forward,
                        neutral * _ramp(phase, .1, .27) * (1 - _ramp(phase, .72, .87)),
                        max_angle=.45)

    hand_weight = _ramp(phase, .04, .2) * (1 - _ramp(phase, release_start, release_end))
    targets = clearance.get('hand_targets')
    reaches = {}
    if targets is not None:
        if not isinstance(targets, dict) or set(targets) != {'Left', 'Right'}:
            raise ValueError('hand_targets must contain Left and Right world points')
        for side in ('Left', 'Right'):
            try:
                contact = Vector(targets[side])
            except (TypeError, ValueError) as exc:
                raise ValueError('hand target must be a three-dimensional world point') from exc
            if len(contact) != 3 or any(not math.isfinite(v) for v in contact):
                raise ValueError('hand target must be a finite three-dimensional world point')
            reaches[side] = _arm_to_target(actor, side, contact,
                                          max(hand_weight, neutral * .65) *
                                          (1 - _ramp(phase, release_start, release_end)))
    else:
        # Avoid arms swinging behind/above the torso when the chest pitches
        # forward. A support target is needed for exact contact orientation.
        for side in ('Left', 'Right'):
            shoulder = point(actor.arm, side + 'Arm')
            elbow = point(actor.arm, side + 'ForeArm')
            _turn_direction(actor, side + 'Arm', elbow - shoulder,
                            actor.forward * .5 + Vector((0, 0, -1)), neutral)

    release_weight = neutral * _ramp(phase, release_start, release_end)
    if targets is not None and release_weight > 0:
        for side in ('Left', 'Right'):
            shoulder = point(actor.arm, side + 'Arm')
            elbow = point(actor.arm, side + 'ForeArm')
            _turn_direction(actor, side + 'Arm', elbow - shoulder,
                            actor.forward * 1.5 + Vector((0, 0, -.7)), release_weight)
            elbow = point(actor.arm, side + 'ForeArm')
            wrist = point(actor.arm, side + 'Hand')
            _turn_direction(actor, side + 'ForeArm', wrist - elbow,
                            actor.forward * .7 + Vector((0, 0, -.8)), release_weight)

    if phase < .12:
        stage = 'takeoff'
    elif phase < .25:
        stage = 'hand_support'
    elif phase < .64:
        stage = 'tuck_through'
    elif phase < .87:
        stage = 'release_descent'
    elif phase < .97:
        stage = 'landing_compression'
    else:
        stage = 'settle'
    return {'stage': stage, 'hand_ik_weight': hand_weight,
            'foot_ik_weight': _ramp(phase, .87, .98),
            'lean_rad': body_pitch, 'hip_flexion_rad': hip_flex,
            'knee_flexion_rad': knee_flex, 'neutral_weight': neutral,
            'landing_compression_weight': _curve(phase, [(0, 0), (.86, 0), (.93, 1), (1, 0)]),
            'suggested_hand_forward': list(actor.forward), 'hand_reach': reaches}
