"""Compile existing motion clips into an overlapping full-body sequence.

Hermes selects the sources from natural-language intent. This helper arranges
actual source durations and moving overlaps; it does not choose an action
vocabulary, add neutral poses, bake animation, or render. Register the returned
plan with register_interaction, then commit through the ordinary timeline.
"""
from __future__ import annotations

import math
from agent_interactions import _frame, _name, _number, _plan
from agent_motion import prepare_motion
from agent_motion_catalog import motion_info


def _context(job_id, actor_id):
    import bpy
    from agent_character import resolve_actor
    from agent_motion_timeline import timeline_info
    actor = resolve_actor(job_id, actor_id)
    scene = bpy.context.scene
    return {'armature': actor['armature'], 'fps': scene.render.fps/scene.render.fps_base,
            'frame_end': scene.frame_end, 'events': timeline_info(job_id, actor_id)['events']}


def _source_window(segment, measured, definition=None):
    duration = float(measured['duration_s'])
    if 'source_window' in segment:
        window = segment['source_window']
        origin = 'explicit_seconds'
    elif definition and definition.get('requires_source_window'):
        window = definition['valid_motion_window_seconds']
        origin = 'verified_motion_window_excluding_calibration'
    else:
        window = [0., duration]
        origin = 'whole_clip_needs_neutral_frame_review'
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        raise ValueError('source_window needs [start_seconds,end_seconds]')
    first, last = [_number(value, 'source_window') for value in window]
    if first < 0 or first >= last or last > duration+1e-6:
        raise ValueError('source_window [%g,%g] is outside the measured %.3fs clip; choose a positive window inside it' % (first, last, duration))
    return [first, min(last, duration)], origin


def _suggested_source_window(definition, measured):
    if not definition.get('source_window_frames'):
        return None
    first, last = definition['source_window_frames']
    reference = (definition.get('source_frame_range') or measured['source_frame_range'])[0]
    source_fps = definition.get('source_fps') or measured['source_fps']
    return [(first-reference)/source_fps, (last-reference)/source_fps]


def _overlaps(intervals, requested):
    """Keep moving pair overlaps without putting three clips on one frame."""
    if requested == 0:
        return [0]*(len(intervals)-1)
    if any(value < 2 for value in intervals) and len(intervals) > 1:
        raise ValueError('a clip is too short for a moving transition; choose a window of at least two scene-frame intervals or set transition_frames=0')
    if any(value < 3 for value in intervals[1:-1]):
        raise ValueError('an internal clip is too short for both transitions; choose at least three scene-frame intervals or set transition_frames=0')
    result = [min(requested, a-1, b-1) for a, b in zip(intervals, intervals[1:])]
    for index in range(1, len(intervals)-1):
        left, right = result[index-1], result[index]
        capacity = intervals[index]-1
        if left+right > capacity:
            new_left = max(1, min(left, math.floor(capacity*left/(left+right))))
            new_right = max(1, min(right, capacity-new_left))
            result[index-1], result[index] = new_left, new_right
    return result


def library_sequence_events(job_id, actor_id, event_id, segments, *, start_frame=1,
                            transition_frames=6, priority=None, intent=None):
    """Return open library events at source speed with real pairwise overlap.

    Each segment needs ``motion_id`` and may supply ``source_window`` in clip
    seconds and ``time_scale`` (1 means original speed). Otherwise the whole
    clip is used, except for a verified calibration-only prefix. The motion's
    preparation and recovery remain intact. Old catalog frame windows are only
    returned as optional editing suggestions. Timeline root origins
    are aligned by the existing compositor, never by copying a source robot.

    No scene range is changed. The compact ``sequence`` result tells the caller
    the required end frame, chosen source times and any shortened transitions.
    """
    _name(event_id, 'event_id')
    first = _frame(start_frame, 'start_frame')
    requested = _frame(transition_frames, 'transition_frames')
    if requested < 0:
        raise ValueError('transition_frames must be nonnegative')
    if not isinstance(segments, (list, tuple)) or not segments:
        raise ValueError('segments needs at least one selected motion_id')
    context = _context(job_id, actor_id)
    fps = float(context['fps'])
    chosen = (max((float(event.get('priority', 0)) for event in context['events']), default=0)+1
              if priority is None else _number(priority, 'priority'))
    sources, notes = [], []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or not segment.get('motion_id'):
            raise ValueError('segment %s needs a motion_id selected from the motion catalog' % (index+1))
        definition = motion_info(segment['motion_id'])
        measured = prepare_motion(context['armature'], definition['motion_id'])
        window, origin = _source_window(segment, measured, definition)
        suggested = _suggested_source_window(definition, measured)
        speed = _number(segment.get('time_scale', 1), 'time_scale')
        if speed <= 0:
            raise ValueError('time_scale must be positive; 1 preserves the source speed')
        duration = (window[1]-window[0])/speed
        if duration*fps < 1-1e-7:
            raise ValueError('segment %s is shorter than one scene-frame interval; choose a longer source window or lower its time_scale' % (index+1))
        # Keep the final source pose. Fractional scene-frame durations are
        # padded by less than one sample, without stretching source playback.
        intervals = math.ceil(duration*fps-1e-7)
        sources.append({'motion_id': measured.get('motion_id', definition['motion_id']),
                        'source_window_seconds': window, 'source_window_origin': origin,
                        'source_duration_s': float(measured['duration_s']), 'time_scale': speed,
                        'playback_duration_s': duration, 'intervals': intervals,
                        **({'suggested_source_window_seconds': suggested} if suggested else {})})
        if suggested:
            notes.append('Segment %s has an optional catalog edit suggestion; it was not applied automatically, so the selected clip keeps its recovery.' % (index+1))
        if origin == 'whole_clip_needs_neutral_frame_review':
            notes.append('Segment %s uses the whole clip; review calibration/neutral frames before claiming a natural transition.' % (index+1))
        elif origin == 'verified_motion_window_excluding_calibration':
            notes.append('Segment %s excludes the verified calibration prefix and retains the remaining performance; naturalness still needs review.' % (index+1))
    overlaps = _overlaps([source['intervals'] for source in sources], requested)
    events, timing, transitions = [], [], []
    cursor = first
    for index, source in enumerate(sources):
        overlap = overlaps[index-1] if index else 0
        if index:
            cursor = events[-1]['end_frame']-overlap
        finish = cursor+source['intervals']
        identifier = event_id+':clip_'+str(index)
        events.append({'event_id': identifier, 'source': 'library', 'motion_id': source['motion_id'],
                       'start_frame': cursor, 'end_frame': finish,
                       'source_window': source['source_window_seconds'], 'time_scale': source['time_scale'],
                       'fit': False, 'loop': False, 'blend_in_frames': overlap,
                       'weight': 1., 'root_strategy': 'source', 'priority': chosen,
                       'intent': str(segments[index].get('intent') or intent or 'Existing motion source with a moving overlap')})
        timing.append({'event_id': identifier, 'frame_start': cursor, 'frame_end': finish,
                       **{key: value for key, value in source.items() if key != 'intervals'},
                       'blend_in_frames': overlap,
                       'scene_duration_s': source['intervals']/fps})
        if index:
            transitions.append({'from_event_id': events[-2]['event_id'], 'to_event_id': identifier,
                                'frames': overlap, 'frame_start': cursor, 'frame_end': cursor+overlap})
            if overlap != requested:
                notes.append('Transition %s shortened from %s to %s intervals so the clips retain exclusive frames and no three clips overlap.' % (index, requested, overlap))
    end = events[-1]['end_frame']
    if end > context['frame_end']:
        notes.append('Set scene.frame_end to at least %s before committing; the sequence was not sped up to fit the current scene range.' % end)
    result = _plan(actor_id, event_id, events)
    result['scope'] = 'Existing full-body motion sources arranged at original or explicitly chosen speed; contacts and transition quality still need actual evaluation'
    result['sequence'] = {'frame_start': first, 'frame_end': end, 'fps': fps,
                          'duration_s': (end-first)/fps, 'segment_count': len(events),
                          'segments': timing, 'transitions': transitions, 'notes': notes,
                          'root_alignment': 'existing timeline compositor at each event start',
                          'neutral_pose_fillers_added': False, 'baked': False}
    return result
