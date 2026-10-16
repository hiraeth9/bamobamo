"""Source facing and support hints on the registered character's real rig.

FK is evaluated from sampled channels without changing scene objects or time.
These features guide source selection; bone speed/height is not skin contact
verification. Candidate windows never change a timeline until Hermes selects one.
"""
from __future__ import annotations

import math


def pose_points(armature, pose):
    """Return bone heads in actor-root metres, excluding the actor transform."""
    from mathutils import Matrix, Quaternion, Vector
    from agent_motion import _ancestors
    matrices, points = {}, {}
    for bone in sorted(armature.data.bones, key=lambda item: len(_ancestors(item))):
        channels = pose.get(bone.name, {})
        basis = Matrix.LocRotScale(Vector(channels.get('location', (0, 0, 0))),
                                  Quaternion(channels.get('rotation_quaternion', (1, 0, 0, 0))),
                                  Vector(channels.get('scale', (1, 1, 1))))
        parents = ({'parent_matrix': matrices[bone.parent.name],
                    'parent_matrix_local': bone.parent.matrix_local} if bone.parent else {})
        matrices[bone.name] = bone.convert_local_to_pose(basis, bone.matrix_local, **parents)
        points[bone.name.split(':')[-1]] = (armature.matrix_basis @ matrices[bone.name]).translation
    return points


def _heading(points):
    across = points['LeftUpLeg'] - points['RightUpLeg']
    if math.hypot(across.x, across.y) < 1e-6:
        raise ValueError('Cannot measure upright pelvis facing from this pose; use explicit root orientation instead of align_heading')
    return math.atan2(across.y, across.x)


def pose_heading(armature, pose):
    """Pelvis yaw in radians; zero faces -Y, positive turns toward +X."""
    return _heading(pose_points(armature, pose))


def _canonical(vector, heading):
    c, s = math.cos(heading), math.sin(heading)
    return [c*vector[0]+s*vector[1], -s*vector[0]+c*vector[1], vector[2]]


def sample_features(armature, motion_id, source_time_s, *, source_window=None, time_scale=1.):
    """Measure source FK and bone motion at one time.

    Relative feet and root velocity use a pelvis-facing frame (forward -Y).
    Source positions/headings exclude the actor root's placement transform.
    """
    from mathutils import Vector
    from agent_motion import prepare_motion, sample_motion
    info = prepare_motion(armature, motion_id)
    speed_scale = float(time_scale)
    if not math.isfinite(speed_scale) or speed_scale <= 0:
        raise ValueError('time_scale must be positive; 1 preserves the source speed')
    low, high = source_window or info.get('default_source_window', [0., info['duration_s']])
    time = min(high, max(low, float(source_time_s)))
    step = 1/info['key_timebase_fps']
    times = sorted({max(low, time-step), time, min(high, time+step)})
    frames = {}
    for value in times:
        sample = sample_motion(armature, motion_id, value, source_window=[low, high])
        frames[value] = (pose_points(armature, sample['pose']), Vector(sample['root']))
    points, root = frames[time]
    first, last = frames[times[0]], frames[times[-1]]
    span = times[-1]-times[0]
    heading = _heading(points)
    velocity = (last[1]-first[1])*speed_scale/span if span else Vector((0, 0, 0))
    lowest = min(points[side+'ToeBase'].z for side in ('Left', 'Right'))
    feet = {}
    for side in ('Left', 'Right'):
        name = side+'ToeBase'
        speed = ((last[0][name]+last[1]-first[0][name]-first[1])*speed_scale/span).length if span else 0.
        height = points[name].z-lowest
        feet[side] = {'relative_position_m': _canonical(points[name]-points['Hips'], heading),
                      'source_position_m': list(points[name]+root),
                      'speed_m_s': speed, 'height_above_lowest_toe_m': height,
                      'support_score': math.exp(-(speed/.35)**2-(height/.05)**2)}
    return {'motion_id': info['motion_id'], 'source_time_s': time, 'time_scale': speed_scale,
            'heading_radians': heading, 'root_velocity_m_s': _canonical(velocity, heading),
            'hip_height_m': points['Hips'].z+root.z, 'feet': feet,
            'quality_scope': 'Pelvis facing and bone speed/height hints; skin contact and naturalness are not verified'}


def transition_cost(outgoing, incoming):
    """Compare body-relative feet, support identity and actual source speed."""
    gaps = [sum((a-b)**2 for a, b in zip(outgoing['feet'][side]['relative_position_m'],
                                        incoming['feet'][side]['relative_position_m']))
            for side in ('Left', 'Right')]
    feet_gap = math.sqrt(sum(gaps)/2)
    support_gap = sum(abs(outgoing['feet'][side]['support_score']-
                          incoming['feet'][side]['support_score']) for side in ('Left', 'Right'))/2
    velocity_gap = math.dist(outgoing['root_velocity_m_s'], incoming['root_velocity_m_s'])
    height_gap = abs(outgoing['hip_height_m']-incoming['hip_height_m'])
    return {'score': feet_gap+.12*support_gap+.08*velocity_gap+.25*height_gap,
            'foot_pose_gap_m': feet_gap, 'support_mismatch': support_gap,
            'root_velocity_gap_m_s': velocity_gap, 'hip_height_gap_m': height_gap}


def _times(first, last, step):
    values = [first+i*step for i in range(math.floor((last-first)/step)+1)]
    if last-values[-1] > 1e-8:
        values.append(last)
    return values


def transition_candidates(job_id, actor_id, outgoing, incoming, *, exit_search_seconds=0.,
                          entry_search_seconds=0., sample_step_seconds=None,
                          transition_seconds=.2, limit=5):
    """Suggest windows only within explicitly permitted entry/exit trim ranges.

    Zero search leaves both windows intact. Entry search preserves the entire
    incoming recovery; exit search is opt-in because it can shorten recovery.
    Hermes must decide whether a candidate still performs the requested action.
    Lower scores mean a closer kinematic match, not a certified natural join.
    """
    from agent_character import resolve_actor
    from agent_motion import prepare_motion
    from agent_motion_sequence import _source_window
    from agent_motion_catalog import motion_info
    actor = resolve_actor(job_id, actor_id)
    armature = actor['armature']
    definitions = [motion_info(segment['motion_id']) for segment in (outgoing, incoming)]
    measured = [prepare_motion(armature, definition['motion_id']) for definition in definitions]
    windows = [_source_window(segment, info, definition)[0]
               for segment, info, definition in zip((outgoing, incoming), measured, definitions)]
    exits, entries, overlap = float(exit_search_seconds), float(entry_search_seconds), float(transition_seconds)
    step = (1/max(info['key_timebase_fps'] for info in measured) if sample_step_seconds is None
            else float(sample_step_seconds))
    if not all(math.isfinite(value) for value in (exits, entries, overlap, step)) or min(exits, entries, overlap) < 0 or step <= 0:
        raise ValueError('search/transition seconds must be nonnegative and sample_step_seconds must be positive')
    if type(limit) is not int or limit < 1:
        raise ValueError('limit must be a positive number of candidates')
    # Retain at least one actual source interval on each side of the join.
    exit_first = max(windows[0][0]+1/measured[0]['key_timebase_fps'], windows[0][1]-exits)
    entry_last = min(windows[1][1]-1/measured[1]['key_timebase_fps'], windows[1][0]+entries)
    if exit_first > windows[0][1] or entry_last < windows[1][0]:
        raise ValueError('Choose source windows containing at least one source frame interval')
    exit_times = _times(exit_first, windows[0][1], step)
    entry_times = _times(windows[1][0], entry_last, step)
    scales = [float(segment.get('time_scale', 1.)) for segment in (outgoing, incoming)]
    if any(not math.isfinite(value) or value <= 0 for value in scales):
        raise ValueError('time_scale must be positive; 1 preserves source speed')
    cache = {}
    def feature(index, time):
        key = (index, round(time, 10))
        if key not in cache:
            cache[key] = sample_features(armature, measured[index]['motion_id'], time,
                                         source_window=windows[index], time_scale=scales[index])
        return cache[key]
    pairs = []
    for exit_time in exit_times:
        for entry_time in entry_times:
            old, new = feature(0, exit_time), feature(1, entry_time)
            duration = min(overlap, (exit_time-windows[0][0])/scales[0],
                           (windows[1][1]-entry_time)/scales[1])
            # Compare concurrent source times through the moving crossfade,
            # not the outgoing end against the incoming start alone.
            costs = [transition_cost(feature(0, exit_time-duration*(1-fraction)*scales[0]),
                                     feature(1, entry_time+duration*fraction*scales[1]))
                     for fraction in ((0., .5, 1.) if duration else (0.,))]
            cost = {key: sum(row[key] for row in costs)/len(costs) for key in costs[0]}
            reference_time = exit_time-duration*scales[0]
            old_heading = feature(0, reference_time)['heading_radians']
            offset = (old_heading-new['heading_radians']+math.pi)%(2*math.pi)-math.pi
            pairs.append({'outgoing_source_window': [windows[0][0], old['source_time_s']],
                          'incoming_source_window': [new['source_time_s'], windows[1][1]],
                          'outgoing': old, 'incoming': new, 'heading_offset_radians': offset,
                          'alignment_reference_source_time_s': reference_time,
                          'scored_overlap_seconds': duration, **cost})
    pairs.sort(key=lambda item: (item['score'], item['incoming_source_window'][0], -item['outgoing_source_window'][1]))
    return {'actor_id': actor_id, 'candidates': pairs[:limit], 'candidate_count': len(pairs),
            'permitted_trim_seconds': {'exit': exits, 'entry': entries},
            'score_basis': 'Concurrent first/middle/last source poses through the requested moving overlap',
            'timeline_modified': False, 'naturalness_verified': False,
            'scope': 'Original-speed source-window suggestions; apply semantic review, align_heading and actual skin/support checks separately'}
