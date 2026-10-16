"""Finger-only pose layers on an existing actor and its current full-body motion.

Hermes can import ``hand_pose_events`` through ordinary MCP Python execution.
Register its returned events normally and commit the timeline separately. No
actor, wrist, IK target, root or full-body animation is created by this helper.
"""
from __future__ import annotations

from agent_interactions import _finger_closure, _frame, _name, _number, _plan, _runtime


def _hand_sides(hands):
    values = [hands] if isinstance(hands, str) else list(hands)
    sides = []
    for value in values:
        side = {'Left': 'Left', 'Right': 'Right',
                'LeftHand': 'Left', 'RightHand': 'Right'}.get(value)
        if side is None:
            raise ValueError('hands must select Left/Right or LeftHand/RightHand')
        if side not in sides:
            sides.append(side)
    if not sides:
        raise ValueError('hands must select at least one hand')
    return sides


def hand_pose_events(job_id, actor_id, event_id, *, start_frame, end_frame,
                     hands=('Left', 'Right'), finger_curl_deg=65,
                     blend_in_frames=6, blend_out_frames=6, priority=None,
                     intent=None):
    """Return one ordinary masked FK event for the selected fingers only.

    The default target reuses the existing guard's rest-geometry fist, including
    its thumb channels. ``finger_curl_deg=0`` targets the rig's open rest hand.
    Timeline weights blend against the actual moving underlying pose at every
    frame; release therefore returns to that frame's source hand, not a frozen
    initial pose or a full-body T pose. Compile after registering the base so
    the default priority sits above it; recompile if the underlying layers change.

    The first/last frames have zero influence when the corresponding fade is
    nonzero. Set both fades to zero for a fist throughout the whole interval.
    ``blend_out_frames=0`` holds through the end; use a nonzero release when the
    event stops before the scene ends. This does not solve a grip on an object
    or certify hand-skin self-collision on arbitrary character rigs.
    """
    _name(event_id, 'event_id')
    first, last = _frame(start_frame, 'start_frame'), _frame(end_frame, 'end_frame')
    incoming = _frame(blend_in_frames, 'blend_in_frames')
    outgoing = _frame(blend_out_frames, 'blend_out_frames')
    if last < first or incoming < 0 or outgoing < 0 or incoming+outgoing > last-first:
        raise ValueError('hand pose needs start <= end and nonnegative fades fitting within that window')
    curl = _number(finger_curl_deg, 'finger_curl_deg')
    if not 0 <= curl <= 90:
        raise ValueError('finger_curl_deg must be between 0 (open rest hand) and 90 degrees')
    sides = _hand_sides(hands)
    actor, _, chosen = _runtime(job_id, actor_id, first, last, priority)
    rotations = {}
    for side in sides:
        rotations.update(_finger_closure(actor, side, curl))
    pose = {name: {'rotation_quaternion': list(rotation)} for name, rotation in rotations.items()}
    event = {'event_id': event_id, 'source': 'procedural',
             'start_frame': first, 'end_frame': last,
             'mask': list(pose), 'root_strategy': 'hold', 'priority': chosen,
             'blend_in_frames': incoming, 'blend_out_frames': outgoing,
             'keyframes': [{'frame': first, 'pose': pose}],
             'intent': str(intent or 'Blend selected fingers to a fist, then release to the current source hand')}
    plan = _plan(actor_id, event_id, [event], {
        'hands': sides, 'finger_curl_deg': curl,
        'finger_pose': 'absolute_rest_geometry_flexion',
        'base_pose_replaced': False, 'root_translation_generated': False,
        'wrist_pose_generated': False,
        'release_policy': 'smooth_to_same_frame_underlying_finger_pose'})
    plan['scope'] = 'finger-rotation-only layer; full-body source motion and wrist transforms are retained'
    return plan
