"""Composable reach/hold/release IK overlays on existing skinned characters.

Hermes may call this compiler through MCP execute_code, register its output
with agent_interactions.register_interaction, then commit and verify normally.
The target's motion belongs to the director: this module creates no actors,
props, root trajectories, camera or whole-scene recipe. Multiple actors may
follow separate points of one prop without making the prop follow their hands.
"""
from __future__ import annotations

import copy

from agent_interactions import (_frame, _name, _number, _runtime, _surface_effector,
                                _hand_elbow_target, _target_event, _plan, _snapshot, _bone, smooth)


def contact_envelope(frame, start_frame, contact_frame, release_frame, end_frame):
    """Smooth FK→IK handover, full contact, and IK→current FK release."""
    if not start_frame < contact_frame <= release_frame < end_frame:
        raise ValueError('contact timing needs start < contact <= release < end')
    if frame <= start_frame or frame >= end_frame:
        return 0.
    if frame < contact_frame:
        return smooth((frame-start_frame)/(contact_frame-start_frame))
    if frame <= release_frame:
        return 1.
    return 1-smooth((frame-release_frame)/(end_frame-release_frame))


def contact_events(job_id, actor_id, event_id, target_binding, *, start_frame,
                   contact_frame, release_frame, end_frame, chain='RightHand',
                   effector=None, kind='hold', tolerance_m=.03,
                   rotation_quaternion=None, priority=None, intent=None):
    """Compile a geometric contact without choosing its semantic action.

    ``target_binding`` addresses an actor bone local point, an actual skin mesh
    vertex, or an object's local point/vertex. It is evaluated at the current
    animation frame during the coordinated commit. ``effector`` must be a
    vertex on this actor's skin; omitted selects a weighted hand/sole vertex.
    ``chain`` names an existing IK chain, not a natural-language action list.

    The compiler captures this chain's actual evaluated pose at each frame,
    retaining its existing animation under the IK handover. This matters when
    the base posture itself was established with IK rather than FK keys.
    Influence ramps from zero to full contact and back to zero, so the first
    and last frames retain the actual base. A hand overlay sets no root/leg
    channels. Recompile after editing the underlying animation. An explicit
    foot overlay should replace another target for that same foot/window.

    An optional world quaternion orients the hand/foot during contact. Omitting
    it retains FK orientation. Moving targets remain position-bound in both
    cases. This is kinematic following, not finger gripping, collision volume,
    object force, balance, or a simultaneous cyclic constraint solver.
    """
    from agent_contacts import validate_binding, validate_targets, resolve_binding, resolve_effector

    first, contact, release, last = (_frame(value, name) for value, name in (
        (start_frame, 'start_frame'), (contact_frame, 'contact_frame'),
        (release_frame, 'release_frame'), (end_frame, 'end_frame')))
    contact_envelope(first, first, contact, release, last)
    _name(event_id, 'event_id'); _name(kind, 'kind')
    if chain not in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot'):
        raise ValueError('chain must address an existing Left/Right Hand/Foot IK chain')
    tolerance = _number(tolerance_m, 'tolerance_m')
    if tolerance <= 0:
        raise ValueError('contact tolerance_m must be positive')
    binding = validate_binding(target_binding)
    actor, scene, chosen = _runtime(job_id, actor_id, first, last, priority)
    if chain.endswith('Foot'):
        from agent_contacts import _requirements_for_events
        from agent_motion_timeline import timeline_info
        existing = _requirements_for_events(actor_id, timeline_info(job_id, actor_id)['events'])
        conflicts = [item['id'] for item in existing
                     if item['chain'] == chain and item['frame_start'] < last and item['frame_end'] > first
                     and item['event_id'] != event_id+':contact']
        if conflicts:
            raise ValueError('release this foot existing contact windows before assigning another target: '+', '.join(conflicts))
    samples = _snapshot(actor, scene, first, last)
    if effector is None:
        effector, _ = _surface_effector(actor, chain)
    else:
        effector = copy.deepcopy(effector)

    cid = actor_id+':'+event_id+':contact'
    _, end_bone = _bone(actor, chain)
    target = {'binding': binding, 'effector': effector,
              'keyframes': [{'frame': frame,
                             'influence': contact_envelope(frame, first, contact, release, last),
                             'rotation_quaternion': (list(rotation_quaternion) if rotation_quaternion is not None else
                                 list(samples[frame]['bone_world'][end_bone.name].to_quaternion())),
                             'interpolation': 'smooth'}
                            for frame in range(first, last+1)],
              'contact': {'id': cid, 'kind': kind, 'start_frame': contact,
                          'end_frame': release, 'tolerance_m': tolerance}}
    if chain.endswith('Hand'):
        target.update(_hand_elbow_target(actor, actor_id, chain))
    # Reuse the execution schema and ownership checks, rather than maintaining
    # another interpretation of binding coordinates or effector membership.
    target = validate_targets({chain: target}, first, last)[chain]
    resolve_binding(job_id, binding)
    resolve_effector(job_id, actor, chain, target)
    event = _target_event(event_id+':contact', first, last, chain, target, chosen,
                          str(intent or 'Reach, maintain '+kind+' contact, then release to the current base pose'))
    from agent_motion_timeline import _mask
    names = _mask(actor['armature'], event)
    event['keyframes'] = [{'frame': frame,
                          'pose': {name: copy.deepcopy(channels)
                                   for name, channels in samples[frame]['pose'].items() if name in names},
                          'interpolation': 'linear'} for frame in range(first, last+1)]
    plan = _plan(actor_id, event_id, [event], {
        'contact_kind': kind, 'contact_frame': contact, 'release_frame': release,
        'root_translation_generated': False, 'base_pose_replaced': False,
        'chain_base_policy': 'per_frame_evaluated_chain_snapshot_recompile_after_base_edits',
        'leg_pose_generated': chain.endswith('Foot'),
        'effector': {'effector': copy.deepcopy(effector)},
        'release_policy': 'smooth_IK_influence_to_zero_on_current_base_FK',
        'target_policy': 'same_frame_evaluated_binding',
        'impact_contact_ids': []})
    plan['scope'] = 'kinematic contact overlay on the existing skin; target motion is authored separately'
    plan['limitations'] = ['no force or simultaneous cyclic contact solver',
                           'no whole-body collision or finger-grip validation',
                           'author root/support motion separately when the target exceeds limb reach']
    return plan
