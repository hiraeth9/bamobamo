"""Persistent, event-addressed motion composition on an existing character rig.

Events carry arbitrary intent strings; this module validates coordinates and
channels, never an allowed-action vocabulary.  Base poses cover gaps by holding
the preceding end pose.  Bone masks overlay that base without a rest-pose filler.
"""
from __future__ import annotations

import json
import math

STORE = 'wm_motion_timeline_v1'
REGIONS = {
    'left_arm': ('LeftShoulder', 'LeftArm', 'LeftForeArm', 'LeftHand'),
    'right_arm': ('RightShoulder', 'RightArm', 'RightForeArm', 'RightHand'),
    'upper_body': ('Spine', 'Spine1', 'Spine2', 'Neck', 'Head', 'LeftShoulder',
                   'LeftArm', 'LeftForeArm', 'LeftHand', 'RightShoulder',
                   'RightArm', 'RightForeArm', 'RightHand'),
    'lower_body': ('Hips', 'LeftUpLeg', 'LeftLeg', 'LeftFoot', 'LeftToeBase',
                   'RightUpLeg', 'RightLeg', 'RightFoot', 'RightToeBase'),
}


def _finite(value):
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (tuple, list)):
        return all(_finite(v) for v in value)
    return not isinstance(value, (int, float)) or math.isfinite(value)


def validate_event(event):
    event = json.loads(json.dumps(event, allow_nan=False))
    if not isinstance(event, dict):
        raise ValueError('motion event must be a dictionary')
    if 'root_trajectory' in event:
        raise ValueError('root_trajectory is not an execution field. Use root_path=[{"frame":...,"root":[x,y,z]}] in root-parent coordinates (world metres for standard actors).')
    if 'source_window_frames' in event:
        raise ValueError('source_window_frames is not an execution field. Use source_window=[start_seconds,end_seconds] relative to the source clip start; this field uses seconds, not scene/source frames.')
    for key in ('event_id', 'start_frame', 'end_frame'):
        if key not in event:
            raise ValueError('motion event missing ' + key)
    if not str(event['event_id']).strip():
        raise ValueError('event_id must be stable and nonempty')
    for key in ('start_frame', 'end_frame'):
        if isinstance(event[key], bool) or int(event[key]) != event[key]:
            raise ValueError(key + ' must be an integer frame')
        event[key] = int(event[key])
    if event['end_frame'] < event['start_frame']:
        raise ValueError('motion event has inverted frame range')
    source = event.get('source', 'procedural')
    if source not in ('library', 'procedural', 'hybrid'):
        raise ValueError('source describes implementation: library/procedural/hybrid')
    if source in ('library', 'hybrid') and not event.get('motion_id'):
        raise ValueError('library source requires an actual motion_id')
    if source == 'procedural' and not event.get('keyframes') and not event.get('targets'):
        raise ValueError('procedural event needs pose/root keyframes or IK targets')
    for key in ('blend_in_frames', 'blend_out_frames'):
        if float(event.get(key, 0)) < 0:
            raise ValueError(key + ' must be nonnegative')
    if not 0 <= float(event.get('weight', 1)) <= 1:
        raise ValueError('motion weight must be in [0,1]')
    if event.get('mask') and event.get('root_owner', False):
        raise ValueError('masked overlays have root_owner=False and cannot also own global root motion; put root motion in a full-body base event.')
    if not _finite(event):
        raise ValueError('nonfinite motion event')
    if 'targets' in event:
        from agent_contacts import validate_targets
        event['targets'] = validate_targets(event['targets'], event['start_frame'], event['end_frame'])
    event['source'] = source
    event['event_id'] = str(event['event_id'])
    event.setdefault('root_strategy', 'hold' if event.get('mask') else 'source')
    if event['root_strategy'] not in ('source', 'trajectory', 'hold'):
        raise ValueError('root_strategy must be source/trajectory/hold')
    if event.get('root_path'):
        if not isinstance(event['root_path'],list) or any('frame' not in key or 'root' not in key or len(key['root'])!=3 for key in event['root_path']):
            raise ValueError('root_path needs frame-keyed root=[x,y,z] coordinates in root-parent metres; position/root_trajectory are not aliases.')
    if event['root_strategy']=='trajectory' and not event.get('root_path') and not any('root' in key for key in event.get('keyframes',[])):
        raise ValueError('root_strategy=trajectory requires root_path frame/root coordinates or actual root=[x,y,z] keyframes. Use root_strategy=hold for orientation-only or pose-only motion.')
    return event


def upsert_event(events, event):
    """Replace one event id while preserving every unrelated event."""
    event = validate_event(event)
    result = [old for old in events if str(old['event_id']) != event['event_id']]
    result.append(event)
    return sorted(result, key=lambda e: (e['start_frame'], e.get('priority', 0), e['event_id']))


def _coverage_id(event):
    # A preserved copy keeps its predecessor ordering despite its archive name.
    return event.get('_preserved_event_order_id',event['event_id'])


def coverage_event(events, frame):
    """One base owner; gaps hold prior motion, before first use its first pose."""
    base = [e for e in events if not e.get('mask')]
    if not base:
        return None, frame
    active = [e for e in base if e['start_frame'] <= frame <= e['end_frame']]
    if active:
        chosen = max(active, key=lambda e: (e.get('priority', 0), e['start_frame'], _coverage_id(e)))
        return chosen, frame
    preceding = [e for e in base if e['end_frame'] < frame]
    if preceding:
        chosen = max(preceding, key=lambda e: (e['end_frame'], e.get('priority', 0), e['start_frame'], _coverage_id(e)))
        return chosen, chosen['end_frame']
    chosen = min(base, key=lambda e: e['start_frame'])
    return chosen, chosen['start_frame']


def event_weight(event, frame):
    if not event['start_frame'] <= frame <= event['end_frame']:
        return 0.0
    w = float(event.get('weight', 1))
    incoming, outgoing = float(event.get('blend_in_frames', 0)), float(event.get('blend_out_frames', 0))
    if incoming:
        t = min(1.0, max(0.0, (frame - event['start_frame']) / incoming))
        w *= t * t * (3 - 2 * t)
    if outgoing:
        t = min(1.0, max(0.0, (event['end_frame'] - frame) / outgoing))
        w *= t * t * (3 - 2 * t)
    return w


def final_base_contains_layer(base,layer,frame):
    """A complete evaluated base already contains its lower-priority layers.

    This marker affects composition only and never bypasses the raw-edit guard.
    A held pose outside the marked interval must not suppress active layers.
    """
    return bool(base and not base.get('mask') and
                (base.get('capture')=='evaluated_final_channels' or base.get('final_pose_layers') is True) and
                base['start_frame']<=frame<=base['end_frame'] and
                float(layer.get('priority',0))<float(base.get('priority',0)))


def _quat_mix(a, b, weight):
    # Shortest-path normalized slerp, usable in ordinary Python unit tests.
    dot = sum(x*y for x, y in zip(a, b))
    if dot < 0:
        b, dot = [-v for v in b], -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > .9995:
        q = [x + (y-x)*weight for x, y in zip(a, b)]
    else:
        angle = math.acos(dot)
        q = [(math.sin((1-weight)*angle)*x + math.sin(weight*angle)*y)/math.sin(angle)
             for x, y in zip(a, b)]
    length = math.sqrt(sum(v*v for v in q))
    if length < 1e-12:
        raise ValueError('zero quaternion in generated pose')
    return [v/length for v in q]


def blend_pose(base, incoming, weight=1.0, bones=None):
    result = json.loads(json.dumps(base))
    allowed = set(bones) if bones is not None else None
    for name, channels in incoming.items():
        if allowed is not None and name not in allowed:
            continue
        old = result.setdefault(name, {})
        for channel, values in channels.items():
            if channel not in ('location', 'rotation_quaternion', 'scale'):
                raise ValueError('unsupported pose channel ' + channel)
            size = 4 if channel == 'rotation_quaternion' else 3
            if len(values) != size:
                raise ValueError('invalid channel shape for ' + name + ':' + channel)
            previous = old.get(channel, [1,0,0,0] if size == 4 else ([1,1,1] if channel == 'scale' else [0,0,0]))
            old[channel] = (_quat_mix(previous, values, weight) if size == 4 else
                            [a + (b-a)*weight for a,b in zip(previous, values)])
    return result


def prepare_keyframes(keys,baseline=None):
    if not keys:
        return []
    keys = sorted(keys, key=lambda key: key['frame'])
    touched={}
    for key in keys:
        for name,channels in key.get('pose',{}).items():
            touched.setdefault(name,set()).update(channels)
    carry={name:{channel:value for channel,value in channels.items() if channel in touched.get(name,set())}
           for name,channels in (baseline or {}).items() if name in touched}
    prepared=[]
    for key in keys:
        carry=blend_pose(carry,key.get('pose',{}))
        prepared.append({**key,'pose':json.loads(json.dumps(carry))})
    return prepared


def sample_keyframes(keys, frame, baseline=None, _prepared=None):
    if not keys:return {}
    keys=prepare_keyframes(keys,baseline) if _prepared is None else _prepared
    if frame <= keys[0]['frame']:
        return keys[0]
    if frame >= keys[-1]['frame']:
        return keys[-1]
    left, right = next((a,b) for a,b in zip(keys, keys[1:]) if a['frame'] <= frame <= b['frame'])
    span = right['frame'] - left['frame']
    t = 0 if span == 0 else (frame-left['frame']) / span
    if left.get('interpolation', 'smooth') == 'smooth':
        t = t*t*(3-2*t)
    result = {'frame': frame, 'pose': blend_pose(left.get('pose', {}), right.get('pose', {}), t)}
    for field in ('root', 'root_scale'):
        if field in left and field in right:
            result[field] = [a+(b-a)*t for a,b in zip(left[field],right[field])]
        elif field in left:
            result[field] = left[field]
    if 'root_rotation_quaternion' in left and 'root_rotation_quaternion' in right:
        result['root_rotation_quaternion'] = _quat_mix(left['root_rotation_quaternion'], right['root_rotation_quaternion'],t)
    elif 'root_rotation_quaternion' in left:
        result['root_rotation_quaternion'] = left['root_rotation_quaternion']
    return result


def _actor(job_id, actor_id):
    from agent_character import resolve_actor
    return resolve_actor(job_id, actor_id)


def _read(root):
    return json.loads(root.get(STORE, '{"version":1,"events":[]}'))


def _write(root, store):
    root[STORE] = json.dumps(store, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _root_policy(event):
    masked=bool(event.get('mask'))
    requests=bool(event.get('root_path') or any(any(field in key for field in ('root','root_rotation_quaternion','root_scale')) for key in event.get('keyframes',[])) or event.get('root_strategy')!='hold')
    return {'event_id':event['event_id'],'root_owner':not masked,'root_requests_ignored':masked and requests,
            **({'diagnostic':'Masked overlay has root_owner=False; root position/orientation/scale requests do not change the actor root. Use a full-body base event for root motion.'} if masked and requests else {})}


def commit_bake_range(requested_range,store,events,animation_ranges=()):
    """Clearing an Action requires rebuilding its full persistent coverage."""
    ranges=[requested_range,*animation_ranges]
    previous=store.get('last_commit',{})
    if 'frame_start' in previous and 'frame_end' in previous:
        ranges.append((previous['frame_start'],previous['frame_end']))
    ranges.extend((event['start_frame'],event['end_frame']) for event in events)
    return (math.floor(min(pair[0] for pair in ranges)),math.ceil(max(pair[1] for pair in ranges)))


def _actual_animation_ranges(actor):
    from agent_motion_quality import _active_channelbags
    ranges=[]
    for obj in (actor['armature'],actor['root']):
        ad=obj.animation_data
        for bag in _active_channelbags(ad):
            for curve in bag.fcurves:
                if curve.keyframe_points:
                    ranges.append((curve.keyframe_points[0].co.x,curve.keyframe_points[-1].co.x))
        if ad:
            ranges.extend((strip.frame_start,strip.frame_end) for track in ad.nla_tracks for strip in track.strips)
    return ranges


def timeline_info(job_id, actor_id):
    actor = _actor(job_id, actor_id)
    return {'ok': True, 'job_id': job_id, 'actor_id': actor_id, **_read(actor['root'])}


def _require_registered_animation(actor,store):
    """Preserve actual bpy edits until they are explicitly captured as events."""
    from agent_motion_quality import animation_fingerprint
    commit=store.get('last_commit',{})
    for obj,field in ((actor['armature'],'pose_channel_sha256'),(actor['root'],'root_channel_sha256')):
        ad=obj.animation_data
        has_authored_motion=bool(ad and (ad.action or ad.nla_tracks or ad.drivers))
        if has_authored_motion and (not commit or ad.nla_tracks or ad.drivers or
                                   animation_fingerprint(obj)!=commit.get(field)):
            raise ValueError('Existing bpy/recipe animation has not been registered or changed after the last commit. Use motion_commit(capture=True, event_id=..., frame_start=..., frame_end=...) to register the actual evaluated motion before appending, replacing or recomposing events; current animation and timeline were preserved.')


def preserve_edit_priority(events,event):
    """An explicit new edit can take over an automatic preservation snapshot.

    Keep the complete snapshot for unedited bone regions. Never silently
    override a caller's explicit priority; report when that choice stays below
    the preserved base instead. A returned automatic priority can be refreshed
    when callers copy and repair the same complete event dictionary.
    """
    event=dict(event)
    if event.get('_auto_preserved_outside_capture'):
        return event,None
    overlaps=[old for old in events if old.get('_auto_preserved_outside_capture') and
              old['start_frame']<=event['end_frame'] and event['start_frame']<=old['end_frame']]
    if not overlaps:return event,None
    automatic_copy=event.get('_priority_from_auto_preservation') is True and event.get('priority')==event.get('_auto_preservation_priority_value')
    explicit='priority' in event and not automatic_copy
    requested=event.get('priority') if explicit else None
    if not explicit:
        event['priority']=max(float(old.get('priority',0)) for old in events)+1
        event['_priority_from_auto_preservation']=True
        event['_auto_preservation_priority_value']=event['priority']
    else:
        event.pop('_priority_from_auto_preservation',None)
        event.pop('_auto_preservation_priority_value',None)
    return event,{'event_id':event['event_id'],'requested_priority':requested,'effective_priority':event['priority'],
                 'priority_source':'explicit' if explicit else 'automatic_takeover_of_preserved_snapshot',
                 'auto_preservation_event_ids':[old['event_id'] for old in overlaps],
                 'shadowed_by_preserved_base':explicit and float(event['priority'])<=max(float(old.get('priority',0)) for old in overlaps)}


def register_event(job_id, actor_id, event_id, event):
    actor = _actor(job_id, actor_id)
    store = _read(actor['root'])
    event = validate_event(dict(event, event_id=event_id))
    if event.get('capture')!='evaluated_final_channels':
        _require_registered_animation(actor,store)
    event,priority_diagnostic=preserve_edit_priority(store['events'],event)
    store['events'] = upsert_event(store['events'], event)
    if 'baseline' not in store:
        from agent_motion_controls import read_pose
        store['baseline'] = read_pose(actor['armature'])
        store['root_baseline'] = {'location': list(actor['root'].location),
                                  'rotation_quaternion': list(actor['root'].matrix_basis.to_quaternion()),
                                  'scale': list(actor['root'].scale)}
    _write(actor['root'],store)
    return {'ok': True, 'actor_id': actor_id, 'event_id': event_id, 'events_count': len(store['events']),**_root_policy(next(e for e in store['events'] if e['event_id']==str(event_id))),
            'priority_diagnostic':priority_diagnostic,
            'status': 'registered_not_baked'}


def remove_event(job_id, actor_id, event_id):
    actor = _actor(job_id,actor_id)
    store = _read(actor['root'])
    store['events'] = [e for e in store['events'] if e['event_id'] != event_id]
    _write(actor['root'],store)
    return {'ok': True,'event_id':event_id,'events_count':len(store['events'])}


def _mask(armature, event):
    mask = event.get('mask')
    if not mask:
        return None
    names = set()
    for value in ([mask] if isinstance(mask,str) else mask):
        if value in REGIONS:
            for short in REGIONS[value]:
                names.update(b.name for b in armature.pose.bones
                             if b.name.split(':')[-1] == short or b.name.split(':')[-1].startswith(short+'Thumb')
                             or (short.endswith('Hand') and b.name.split(':')[-1].startswith(short)))
        elif value in armature.pose.bones:
            names.add(value)
        else:
            matches = [b.name for b in armature.pose.bones if b.name.split(':')[-1] == value]
            if not matches:
                raise ValueError('mask has no real target bone: ' + value)
            names.update(matches)
    return names


def _source_sample(armature, event, frame, fps, context=None,operation=None):
    result = {'pose':{}, 'root':[0,0,0]}
    if event['source'] in ('library','hybrid'):
        from agent_motion import sample_motion, prepare_motion
        info_cache=operation.setdefault('source_info',{}) if operation is not None else {}
        if event['motion_id'] not in info_cache:info_cache[event['motion_id']]=prepare_motion(armature,event['motion_id'])
        info=info_cache[event['motion_id']]
        duration = info.get('duration_s',info.get('duration_seconds',0))
        window = event.get('source_window')
        span = window[1]-window[0] if window else duration
        elapsed = (frame-event['start_frame']) / fps
        if event.get('time_scale') is not None:
            source_time = elapsed * float(event['time_scale'])
        elif event.get('fit', not event.get('loop',False)):
            source_time = (frame-event['start_frame']) / max(1,event['end_frame']-event['start_frame']) * span
        else:
            source_time = elapsed
        cycle=0
        if event.get('loop') and span > 0:
            cycle=int(source_time//span)
            source_time %= span
        offset=window[0] if window else 0
        result = sample_motion(armature,event['motion_id'],offset+source_time,
                               root_mode=event.get('root_mode','extract_planar'),source_window=window)
        # The sampler reports root displacement from *clip* start. A cropped
        # event must begin at its own origin, and loops retain accumulated travel.
        root_cache=operation.setdefault('source_roots',{}) if operation is not None else {}
        root_key=(event['motion_id'],event.get('root_mode','extract_planar'),tuple(window) if window else None)
        if root_key not in root_cache:
            root_cache[root_key]=sample_motion(armature,event['motion_id'],offset,
                             root_mode=event.get('root_mode','extract_planar'),source_window=window)['root']
        origin=root_cache[root_key]
        result['root']=[a-b for a,b in zip(result['root'],origin)]
        if cycle:
            end_key=root_key+('end',)
            if end_key not in root_cache:
                root_cache[end_key]=sample_motion(armature,event['motion_id'],offset+span,
                              root_mode=event.get('root_mode','extract_planar'),source_window=window)['root']
            end=root_cache[end_key]
            result['root']=[a+cycle*(b-c) for a,b,c in zip(result['root'],end,origin)]
    keys=event.get('keyframes',[])
    prepared=None
    if keys and operation is not None:
        key_cache=operation.setdefault('generated_keyframes',{})
        strategies=operation.setdefault('keyframe_strategies',{})
        if event['event_id'] not in strategies:
            first_pose=min(keys,key=lambda key:key['frame']).get('pose',{})
            strategies[event['event_id']]=all(channel in first_pose.get(name,{}) for key in keys
                        for name,channels in key.get('pose',{}).items() for channel in channels)
        independent=strategies[event['event_id']]
        # Full captured poses and explicit keyed overlays do not depend on the
        # changing library base. Sparse full-body procedural context is fixed
        # for that event; dynamic sparse masked/hybrid layers stay contextual.
        cacheable=independent or (event['source']=='procedural' and not event.get('mask') and context is not None)
        if cacheable:
            cache_key=(event['event_id'],'independent' if independent else 'fixed_base_context')
            if cache_key not in key_cache:key_cache[cache_key]=prepare_keyframes(keys,result.get('pose') or context)
            prepared=key_cache[cache_key]
    generated = sample_keyframes(keys,frame,baseline=result.get('pose') or context,_prepared=prepared)
    result['pose'] = blend_pose(result.get('pose',{}),generated.get('pose',{}))
    for key in ('root','root_scale','root_rotation_quaternion'):
        if key in generated:
            result[key] = generated[key]
    return result


def _capture_event(actor, frame_start, frame_end, event_id, mask=None):
    import bpy
    from agent_motion_controls import read_pose
    keys=[]
    original_frame=bpy.context.scene.frame_current
    for frame in range(frame_start,frame_end+1):
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        root = actor['root'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        keys.append({'frame':frame,'interpolation':'linear','pose':read_pose(actor['armature'],evaluated=True),
                     'root':list(root.location),'root_rotation_quaternion':list(root.matrix_basis.to_quaternion()),
                     'root_scale':list(root.scale)})
    bpy.context.scene.frame_set(original_frame)
    return {'event_id':event_id,'source':'procedural','start_frame':frame_start,'end_frame':frame_end,
            'keyframes':keys,'root_strategy':'trajectory',**({'mask':mask} if mask else {}),
            'capture':'evaluated_final_channels','priority':100}


def _root_delta(rotation,scale,root):
    from mathutils import Vector
    return rotation @ Vector([a*b for a,b in zip(scale,root)])


def contact_commit_order(actor_ids, dependencies):
    """Targets are baked before their dependants; cycles must not mutate a scene."""
    actor_ids = list(actor_ids)
    if not actor_ids or any(not isinstance(aid, str) or not aid.strip() for aid in actor_ids):
        raise ValueError('coordinated commit needs nonempty actor IDs')
    if len(set(actor_ids)) != len(actor_ids):
        raise ValueError('duplicate actor IDs in coordinated commit')
    pending = set(actor_ids)
    order = []
    while pending:
        ready = [aid for aid in actor_ids if aid in pending and
                 not (set(dependencies.get(aid, ())) - {aid}) & pending]
        if not ready:
            raise ValueError('Cyclic actor contact dependencies require a joint solver; '
                             'no animation was changed: ' + ', '.join(sorted(pending)))
        order.extend(ready)
        pending.difference_update(ready)
    return order


def active_contact_targets(events, frame):
    """Use the same layers and influence ramps for scheduling and execution."""
    from agent_contacts import sample_target
    base, _ = coverage_event(events, frame)
    active = []
    for event in sorted(events, key=lambda e: (e.get('priority', 0), e['start_frame'], _coverage_id(e))):
        weight = event_weight(event, frame)
        if not weight or final_base_contains_layer(base, event, frame):
            continue
        targets = {chain: spec for chain, spec in event.get('targets', {}).items()
                   if sample_target(spec, frame).get('influence', 1) * weight > 0}
        if targets:
            active.append((event, targets, weight))
    return active


def contact_dependency_schedule(actor_ids, events_by_actor, frame_start, frame_end, binding_owners):
    """Preflight each frame; opposing interactions at different times are valid."""
    from agent_contacts import sample_target
    frame_orders, frame_dependencies, combined = {}, {}, {aid: set() for aid in actor_ids}
    for frame in range(frame_start, frame_end + 1):
        dependencies = {aid: set() for aid in actor_ids}
        for aid in actor_ids:
            for event, targets, _ in active_contact_targets(events_by_actor[aid], frame):
                for spec in targets.values():
                    state = sample_target(spec, frame)
                    for field in ('binding', 'pole_binding'):
                        binding = state.get(field)
                        if binding:
                            dependencies[aid].update(binding_owners(binding) - {aid})
            combined[aid].update(dependencies[aid])
        try:
            frame_orders[frame] = contact_commit_order(actor_ids, dependencies)
        except ValueError as error:
            raise ValueError(f'Frame {frame}: {error}') from error
        frame_dependencies[frame] = {aid: sorted(targets) for aid, targets in dependencies.items() if targets}
    # Preserve the established order for one-way scenes, including frames where
    # influence is zero. Mixed-direction scenes use their actual frame orders.
    try:
        common_order = contact_commit_order(actor_ids, combined)
    except ValueError:
        common_order = None
    windows = []
    for frame, order in frame_orders.items():
        if common_order is not None:
            order = frame_orders[frame] = common_order
        dependencies = frame_dependencies[frame]
        if windows and windows[-1]['commit_order'] == order and windows[-1]['dependencies'] == dependencies:
            windows[-1]['frame_end'] = frame
        else:
            windows.append({'frame_start': frame, 'frame_end': frame,
                            'commit_order': order, 'dependencies': dependencies})
    return {'frame_orders': frame_orders, 'dependency_windows': windows,
            'commit_order': common_order, 'dynamic_commit_order': common_order is None}


def _binding_references(spec):
    bindings = [spec.get(field) for field in ('binding', 'pole_binding')]
    bindings.extend(key.get(field) for key in spec.get('keyframes', [])
                    for field in ('binding', 'pole_binding'))
    return [binding for binding in bindings if isinstance(binding, dict)]


def _bound_object_owners(obj, owners, visited=None):
    """Follow object parenting and constraint targets, including held props."""
    if obj is None:
        return set()
    visited = set() if visited is None else visited
    if obj.name in visited:
        return set()
    visited.add(obj.name)
    if obj.name in owners:
        return {owners[obj.name]}
    ad = obj.animation_data
    if ad is not None and ad.drivers:
        raise ValueError('Bake contact target drivers before coordinated commit: ' + obj.name)
    result = _bound_object_owners(obj.parent, owners, visited)
    for constraint in obj.constraints:
        # Influence can be animated later in the range. Preserve even currently
        # inactive dependencies rather than sampling a future target too early.
        for field in ('target', 'pole_target'):
            result.update(_bound_object_owners(getattr(constraint, field, None), owners, visited))
        for target in getattr(constraint, 'targets', ()):
            result.update(_bound_object_owners(getattr(target, 'target', None), owners, visited))
    for modifier in getattr(obj, 'modifiers', ()):
        result.update(_bound_object_owners(getattr(modifier, 'object', None), owners, visited))
    return result


def _write_baked_channels(job_id, actor_id, actor, baked):
    """Write evaluated FK/root channels consistently for single and group commits."""
    from agent_motion_controls import set_pose
    from agent_motion_quality import animation_fingerprint
    armature, root = actor['armature'], actor['root']
    baked_constraints=[]
    for bone in armature.pose.bones:
        for constraint in bone.constraints:
            baked_constraints.append({'bone':bone.name,'constraint':constraint.name,'type':constraint.type})
            constraint.mute=True
    armature.animation_data_clear()
    root.animation_data_clear()
    previous_quaternions={}
    previous_root_quaternion=None
    for frame,pose,location,rotation,scale in baked:
        # A matrix may decompose to either q or -q. Integer frames are identical,
        # but linear component curves across antipodes pass through zero at a
        # fractional frame. Keep one continuous quaternion hemisphere per bone.
        for name,channels in pose.items():
            quaternion=channels['rotation_quaternion']
            previous=previous_quaternions.get(name)
            if previous is not None and sum(a*b for a,b in zip(previous,quaternion))<0:
                quaternion=[-v for v in quaternion]
                channels['rotation_quaternion']=quaternion
            previous_quaternions[name]=quaternion
        if previous_root_quaternion is not None and sum(a*b for a,b in zip(previous_root_quaternion,rotation))<0:
            rotation=[-v for v in rotation]
        previous_root_quaternion=rotation
        set_pose(armature,pose)
        root.location=location; root.rotation_quaternion=rotation; root.scale=scale
        for bone in armature.pose.bones:
            for channel in ('location','rotation_quaternion','scale'):
                bone.keyframe_insert(channel,frame=frame,group=bone.name)
        for channel in ('location','rotation_quaternion','scale'):
            root.keyframe_insert(channel,frame=frame)
    for obj in (armature,root):
        action=obj.animation_data.action
        action.name=f'WM_{job_id}_{actor_id}_Baked_'+('Pose' if obj==armature else 'Root')
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        for point in curve.keyframe_points:
                            point.interpolation='LINEAR'
    return {'armature_action': armature.animation_data.action.name,
            'root_action': root.animation_data.action.name,
            'pose_channel_sha256': animation_fingerprint(armature),
            'root_channel_sha256': animation_fingerprint(root),
            'constraints_baked_and_muted': True, 'baked_constraints': baked_constraints}


def _motion_commit_group(job_id, actor_ids, frame_start=None, frame_end=None):
    """Restore all bases each frame, solve that frame's contacts, then bake."""
    import bpy
    from agent_character import scene_actor_facts
    from agent_contacts import resolve_binding
    from agent_motion_controls import (CONTROL_STORE, read_pose, set_pose, mute_controls,
                                       _controls_ensure_resolved, _apply_targets_resolved)
    scene = bpy.context.scene
    original_frame = scene.frame_current
    original_subframe = scene.frame_subframe
    original_objects = set(bpy.data.objects.keys())
    original_actions = set(bpy.data.actions.keys())
    seeds = list(actor_ids)
    contact_commit_order(seeds, {})
    actors = {item['actor_id']: _actor(job_id, item['actor_id'])
              for item in scene_actor_facts(job_id)['actors']}
    for aid in seeds:
        if aid not in actors:
            raise ValueError('character actor not registered: ' + aid)
    owners = {}
    for aid, actor in actors.items():
        for obj in [actor['root'], actor['armature'], *actor['meshes']]:
            owners[obj.name] = aid
    stores = {aid: _read(actor['root']) for aid, actor in actors.items()}
    selected = list(seeds)

    def binding_owners(binding):
        return ({binding['actor_id']} if 'actor_id' in binding else
                _bound_object_owners(bpy.data.objects.get(binding.get('object', '')), owners))

    # Include animated target ancestors so callers cannot accidentally use an
    # old target bake after editing its registered events.
    for aid in selected:
        stores[aid]['events'] = [validate_event(event) for event in stores[aid].get('events', [])]
        for event in stores[aid]['events']:
            for spec in event.get('targets', {}).values():
                for binding in _binding_references(spec):
                    for reference in binding_owners(binding):
                        if reference not in actors:
                            raise ValueError('contact target actor is not registered: ' + str(reference))
                        if reference != aid:
                            if stores[reference].get('events') and reference not in selected:
                                selected.append(reference)
                    # Missing objects, bones and bad vertex indices fail before
                    # any Action clear or timeline mutation.
                    resolve_binding(job_id, binding, actor_cache=actors)
    requested = (scene.frame_start if frame_start is None else int(frame_start),
                 scene.frame_end if frame_end is None else int(frame_end))
    if requested[1] < requested[0]:
        raise ValueError('inverted commit frame range')
    ranges = {}
    for aid in selected:
        actor = actors[aid]
        if not stores[aid].get('events'):
            raise ValueError('No motion events for coordinated actor: ' + aid)
        _require_registered_animation(actor, stores[aid])
        ranges[aid] = commit_bake_range(requested, stores[aid], stores[aid]['events'],
                                        _actual_animation_ranges(actor))
    first, last = min(pair[0] for pair in ranges.values()), max(pair[1] for pair in ranges.values())
    # This happens before the base pass clears any Action. A reverse interaction
    # later in time is allowed; only an actual same-frame loop is rejected.
    events_by_actor = {aid: stores[aid]['events'] for aid in selected}
    schedule = contact_dependency_schedule(selected, events_by_actor, first, last, binding_owners)
    backups = {}
    for aid in selected:
        actor = actors[aid]
        objects = []
        for obj in (actor['armature'], actor['root']):
            ad = obj.animation_data
            objects.append((obj, ad.action if ad else None,
                            getattr(ad, 'action_slot', None) if ad else None,
                            obj.matrix_basis.copy()))
        control_objects = {}
        for bone in actor['armature'].pose.bones:
            for constraint in bone.constraints:
                if constraint.name.startswith('WM_'):
                    for field in ('target', 'pole_target'):
                        obj = getattr(constraint, field, None)
                        if obj is not None:
                            control_objects[obj.name] = obj.matrix_basis.copy()
        backups[aid] = {'timeline': actor['root'].get(STORE), 'objects': objects,
                        'controls': actor['root'].get(CONTROL_STORE),
                        'control_objects': control_objects,
                        'pose': {bone.name: bone.matrix_basis.copy() for bone in actor['armature'].pose.bones},
                        'constraints': {constraint.as_pointer(): (constraint.mute, constraint.influence,
                                                                  getattr(constraint, 'pole_angle', None))
                                        for bone in actor['armature'].pose.bones for constraint in bone.constraints}}
    try:
        # Base pass is internal and cannot be published as an accepted result.
        bases = {}
        for aid in selected:
            print(f'WM_MOTION_BASE actor={aid} frames={ranges[aid][0]}..{ranges[aid][1]}', flush=True)
            collected = _motion_commit_single(job_id, aid, frame_start, frame_end, _base_only=True)
            bases[aid] = {row[0]: row[1:] for row in collected['base_samples']}
        baked, diagnostics, controls = ({aid: [] for aid in selected},
                                        {aid: [] for aid in selected}, {})
        print(f'WM_MOTION_SOLVE actors={len(selected)} frames={first}..{last}', flush=True)
        for frame in range(first, last + 1):
            scene.frame_set(frame)
            # No preceding IK result or old Action may become this frame's base.
            for aid in selected:
                actor = actors[aid]
                base_frame = max(ranges[aid][0], min(frame, ranges[aid][1]))
                pose, location, rotation, scale = bases[aid][base_frame]
                mute_controls(actor, True)
                set_pose(actor['armature'], pose)
                actor['root'].location = location
                actor['root'].rotation_quaternion = rotation
                actor['root'].scale = scale
            bpy.context.view_layer.update()
            for aid in schedule['frame_orders'][frame]:
                active = active_contact_targets(events_by_actor[aid], frame)
                if active and aid not in controls:
                    controls[aid] = _controls_ensure_resolved(job_id, aid, actors[aid])
                for event, targets, weight in active:
                    applied = _apply_targets_resolved(actors[aid], controls[aid], targets, frame, weight,
                                                      actor_cache=actors)
                    diagnostics[aid].append({'frame': frame, 'event_id': event['event_id'], **applied})
                bpy.context.view_layer.update()
            for aid in selected:
                if ranges[aid][0] <= frame <= ranges[aid][1]:
                    arm, root = actors[aid]['armature'], actors[aid]['root']
                    baked[aid].append((frame, read_pose(arm, evaluated=True), list(root.location),
                                       list(root.rotation_quaternion), list(root.scale)))
        results = {}
        for aid in selected:
            print(f'WM_MOTION_BAKE actor={aid} frames={len(baked[aid])}', flush=True)
            actor = actors[aid]
            channel_facts = _write_baked_channels(job_id, aid, actor, baked[aid])
            store = _read(actor['root'])
            store['last_commit'].update(channel_facts, status='baked_unreviewed',
                                         target_diagnostics=diagnostics[aid],
                                         unreachable_targets=[{'frame': row['frame'], 'event_id': row['event_id'], **target}
                                                              for row in diagnostics[aid] for target in row['targets']
                                                              if not target['reachable'] and target['influence'] > 0])
            _write(actor['root'], store)
            results[aid] = {'ok': True, 'job_id': job_id, 'actor_id': aid, **store['last_commit']}
        return {'ok': True, 'job_id': job_id, 'actor_ids': selected, 'requested_actor_ids': seeds,
                'commit_order': schedule['commit_order'], 'actors': results,
                'dynamic_commit_order': schedule['dynamic_commit_order'],
                'dependency_windows': schedule['dependency_windows'],
                'coordination': 'same_frame_base_reset_then_target_first_IK',
                'cyclic_contacts_supported': False, 'naturalness_verified': False}
    except Exception:
        # Do not strand a task with a partially completed two-pass bake.
        for aid, backup in backups.items():
            actor = actors[aid]
            for obj, action, slot, basis in backup['objects']:
                obj.animation_data_clear()
                if action is not None:
                    ad = obj.animation_data_create()
                    ad.action = action
                    if slot is not None:
                        ad.action_slot = slot
                obj.matrix_basis = basis
            for bone in actor['armature'].pose.bones:
                bone.matrix_basis = backup['pose'][bone.name]
                for constraint in list(bone.constraints):
                    state = backup['constraints'].get(constraint.as_pointer())
                    if state is not None:
                        constraint.mute, constraint.influence = state[:2]
                        if state[2] is not None:
                            constraint.pole_angle = state[2]
                    elif constraint.name.startswith('WM_'):
                        bone.constraints.remove(constraint)
            for name, matrix in backup['control_objects'].items():
                if name in bpy.data.objects:
                    bpy.data.objects[name].matrix_basis = matrix
            if backup['timeline'] is None:
                actor['root'].pop(STORE, None)
            else:
                actor['root'][STORE] = backup['timeline']
            if backup['controls'] is None:
                actor['root'].pop(CONTROL_STORE, None)
            else:
                actor['root'][CONTROL_STORE] = backup['controls']
            prefix = 'WM_' + str(job_id) + '_' + aid + '_'
            for obj in list(bpy.data.objects):
                if (obj.name not in original_objects and obj.name.startswith(prefix)
                        and any(chain + suffix in obj.name
                                for chain in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot')
                                for suffix in ('_Goal', '_Pole'))):
                    bpy.data.objects.remove(obj, do_unlink=True)
        for action in list(bpy.data.actions):
            if action.name not in original_actions and action.users == 0:
                bpy.data.actions.remove(action)
        raise
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)
        bpy.context.view_layer.update()


def motion_commit(job_id, actor_id, frame_start=None, frame_end=None, events=None,
                  capture=False, event_id=None, mask=None):
    """Commit one actor, or coordinate a list of existing actors and their targets."""
    if isinstance(actor_id, (list, tuple)):
        if capture or events is not None or event_id is not None or mask is not None:
            raise ValueError('Register each actor event first, then pass actor_id=[...] '
                             'for a coordinated commit; capture remains per-actor.')
        return _motion_commit_group(job_id, actor_id, frame_start, frame_end)
    return _motion_commit_single(job_id, actor_id, frame_start, frame_end, events,
                                 capture, event_id, mask)


def _motion_commit_single(job_id, actor_id, frame_start=None, frame_end=None, events=None,
                          capture=False, event_id=None, mask=None, _base_only=False):
    """Compose or capture and bake *all* actual channels; never rebuild actors.

    Direct bpy users pass capture=True to bake the evaluated existing animation
    (including constraints).  Reuse event_id to replace a local captured segment.
    root_path keyframes use root-parent coordinates (world metres for our rigs).
    Library root displacement is actor-space and follows each event's starting
    root orientation/scale, inherited from the preceding event unless specified.
    """
    import bpy
    from mathutils import Quaternion, Vector
    from agent_motion_controls import (read_pose,set_pose,mute_controls,
                                       _controls_ensure_resolved,_apply_targets_resolved)
    actor = _actor(job_id,actor_id)
    actor_cache = {actor_id: actor}
    armature, root = actor['armature'],actor['root']
    scene=bpy.context.scene
    frame_start = scene.frame_start if frame_start is None else int(frame_start)
    frame_end = scene.frame_end if frame_end is None else int(frame_end)
    if frame_end < frame_start:
        raise ValueError('inverted commit frame range')
    requested_range=(frame_start,frame_end)
    # Diagnose execution-field mistakes before capture or any event mutation.
    events=[validate_event(event) for event in (events or [])]
    store=_read(root)
    store['events']=[validate_event(event) for event in store.get('events',[])]
    if not capture:
        # Before any event mutation, source import, channel clear or frame change.
        # Ordinary recomposition must never silently erase direct bpy editing.
        _require_registered_animation(actor,store)
    animation_ranges=_actual_animation_ranges(actor)
    preserved_existing_ranges=[]
    if capture:
        # Outside a local edit, preserve what was actually evaluated, including
        # prior IK/pole solutions. Replaying their controller plans can change
        # the old result even when every keyed source channel is unchanged.
        existing_ranges=list(animation_ranges)
        last=store.get('last_commit',{})
        if (armature.animation_data or root.animation_data) and 'frame_start' in last and 'frame_end' in last:
            existing_ranges.append((last['frame_start'],last['frame_end']))
        from agent_motion_quality import animation_fingerprint
        preserved_provenance={'pose_channel_sha256':animation_fingerprint(armature),
                              'root_channel_sha256':animation_fingerprint(root),
                              'original_event_ids':[event['event_id'] for event in store.get('events',[])],
                              'original_source_records':last.get('sources',[])}
        old=next((event for event in store.get('events',[]) if event['event_id']==(event_id or 'direct_bpy')),None)
        if old and (old['start_frame']<frame_start or old['end_frame']>frame_end):
            kept=dict(old,event_id=old['event_id']+'__preserved_previous_'+str(old['start_frame'])+'_'+str(old['end_frame']),
                      _preserved_event_order_id=_coverage_id(old),
                      preservation_reason='Same event_id captured a narrower window; retain the previous event outside that window')
            store['events']=upsert_event(store['events'],kept)
            _write(root,store)
        if existing_ranges:
            old_start=math.floor(min(pair[0] for pair in existing_ranges))
            old_end=math.ceil(max(pair[1] for pair in existing_ranges))
            preservation_priority=max(99,max((float(e.get('priority',0)) for e in store.get('events',[])),default=0))+1
            for suffix,left,right in (('before',old_start,min(old_end,frame_start-1)),
                                      ('after',max(old_start,frame_end+1),old_end)):
                if right>=left:
                    kept=_capture_event(actor,left,right,(event_id or 'direct_bpy')+'__preserved_'+suffix)
                    kept['priority']=preservation_priority
                    kept['_auto_preserved_outside_capture']=True
                    kept['preservation_reason']='Actual evaluated animation outside the requested capture window; original FK/IK/pole result is preserved without controller recomputation'
                    kept['preservation_provenance']=preserved_provenance
                    register_event(job_id,actor_id,kept['event_id'],kept)
                    preserved_existing_ranges.append([left,right])
            store=_read(root)
        captured=_capture_event(actor,frame_start,frame_end,event_id or 'direct_bpy',mask)
        # Capturing later edits must win overlapping older captures regardless
        # of the caller's event-id spelling; identifiers are not priorities.
        captured['priority']=max(99,max((float(e.get('priority',0)) for e in store.get('events',[])),default=0))+1
        register_event(job_id,actor_id,captured['event_id'],captured)
    for event in events or []:
        register_event(job_id,actor_id,event['event_id'],event)
    store=_read(root)
    all_events=store['events']
    if not all_events:
        raise ValueError('No motion events; use capture=True for directly authored bpy animation')
    frame_start,frame_end=commit_bake_range(requested_range,store,all_events,animation_ranges)
    baseline=store.get('baseline',read_pose(armature))
    root_base=store.get('root_baseline',{'location':list(root.location),'rotation_quaternion':list(root.matrix_basis.to_quaternion()),'scale':list(root.scale)})
    fps=scene.render.fps/scene.render.fps_base
    operation={}
    # Sample sources before clearing target channels: prepared source rigs are separate.
    from agent_motion import prepare_motion
    for event in all_events:
        if event['source'] in ('library','hybrid'):
            operation.setdefault('source_info',{})[event['motion_id']]=prepare_motion(armature,event['motion_id'])
    source_root_origin = {}
    source_root_rotation={}
    source_root_scale={}
    source_pose_context = {}
    processed=[]
    for event in sorted([e for e in all_events if not e.get('mask')],key=lambda e:(e['start_frame'],e.get('priority',0),_coverage_id(e))):
        cursor=Vector(root_base['location']);cursor_pose=baseline
        cursor_rotation=Quaternion(root_base['rotation_quaternion']);cursor_scale=Vector(root_base['scale'])
        # Nested local captures do not become the predecessor of every later
        # event: ask which earlier event actually covers this start time.
        previous_event,previous_frame=coverage_event(processed,event['start_frame'])
        if previous_event:
            previous_id=previous_event['event_id']
            previous_context=source_pose_context[previous_id]
            at_start=_source_sample(armature,previous_event,previous_frame,fps,context=previous_context,operation=operation)
            cursor_pose=blend_pose(previous_context,at_start['pose'])
            origin=source_root_origin[previous_id]
            previous_rotation=source_root_rotation[previous_id]
            previous_scale=source_root_scale[previous_id]
            cursor=origin.copy()
            if previous_event.get('root_path'):
                cursor=Vector(sample_keyframes(previous_event['root_path'],previous_frame).get('root',origin))
            elif previous_event['root_strategy']=='source':
                cursor=origin+_root_delta(previous_rotation,previous_scale,at_start.get('root',(0,0,0)))
            elif previous_event['root_strategy']=='trajectory':
                cursor=Vector(at_start.get('root',origin))
            cursor_rotation=Quaternion(at_start.get('root_rotation_quaternion',previous_rotation))
            cursor_scale=Vector(at_start.get('root_scale',previous_scale))
            if previous_event.get('root_path'):
                path_state=sample_keyframes(previous_event['root_path'],previous_frame)
                cursor_rotation=Quaternion(path_state.get('root_rotation_quaternion',cursor_rotation))
                cursor_scale=Vector(path_state.get('root_scale',cursor_scale))
        source_root_origin[event['event_id']]=cursor.copy()
        start_sample=_source_sample(armature,event,event['start_frame'],fps,context=cursor_pose,operation=operation)
        source_root_rotation[event['event_id']]=Quaternion(start_sample.get('root_rotation_quaternion',cursor_rotation))
        source_root_scale[event['event_id']]=Vector(start_sample.get('root_scale',cursor_scale))
        source_pose_context[event['event_id']]=cursor_pose
        processed.append(event)
    armature.animation_data_clear()
    root.animation_data_clear()
    mute_controls(actor,True)
    if capture:
        for bone in armature.pose.bones:
            for constraint in bone.constraints:
                constraint.mute=True
    for bone in armature.pose.bones:
        bone.rotation_mode='QUATERNION'
    root.rotation_mode='QUATERNION'
    baked=[]
    provenance=[]
    target_diagnostics=[]
    previous_base=None
    control_info=None
    for frame in range(frame_start,frame_end+1):
        scene.frame_set(frame)
        base,sample_frame=coverage_event(all_events,frame)
        composed=json.loads(json.dumps(baseline))
        root_position=Vector(root_base['location'])
        root_rotation=Quaternion(root_base['rotation_quaternion'])
        root_scale=Vector(root_base['scale'])
        if base:
            composed=json.loads(json.dumps(source_pose_context.get(base['event_id'],baseline)))
            source=_source_sample(armature,base,sample_frame,fps,context=composed,operation=operation)
            incoming=blend_pose(composed,source['pose'])
            # New source ramps from outgoing end pose, never a rest-pose fade.
            previous=[e for e in all_events if not e.get('mask') and e['event_id']!=base['event_id'] and e['start_frame']<base['start_frame']]
            blend=int(base.get('blend_in_frames',0))
            if previous and blend and sample_frame-base['start_frame']<blend:
                outgoing,outgoing_frame=coverage_event(previous,sample_frame)
                outgoing_context=source_pose_context.get(outgoing['event_id'],baseline)
                outgoing_sample=_source_sample(armature,outgoing,outgoing_frame,fps,context=outgoing_context,operation=operation)
                old=blend_pose(outgoing_context,outgoing_sample['pose'])
                composed=blend_pose(old,incoming,event_weight(base,sample_frame))
            else:
                composed=incoming
            root_position=source_root_origin.get(base['event_id'],root_position).copy()
            root_rotation=source_root_rotation[base['event_id']].copy()
            root_scale=source_root_scale[base['event_id']].copy()
            if base.get('root_path'):
                root_position=Vector(sample_keyframes(base['root_path'],sample_frame).get('root',root_position))
            elif base['root_strategy']=='source':
                root_position += _root_delta(source_root_rotation[base['event_id']],source_root_scale[base['event_id']],source.get('root',(0,0,0)))
            elif base['root_strategy']=='trajectory' and 'root' in source:
                root_position=Vector(source['root'])
            if 'root_rotation_quaternion' in source:
                root_rotation=Quaternion(source['root_rotation_quaternion'])
            if 'root_scale' in source:
                root_scale=Vector(source['root_scale'])
            if base.get('root_path'):
                path_state=sample_keyframes(base['root_path'],sample_frame)
                root_rotation=Quaternion(path_state.get('root_rotation_quaternion',root_rotation))
                root_scale=Vector(path_state.get('root_scale',root_scale))
            if base['event_id']!=previous_base:
                provenance.append({'event_id':base['event_id'],'source':base['source'],'provenance':source.get('provenance',{}),
                                   'root_strategy':base['root_strategy'],'root_owner':True})
                previous_base=base['event_id']
        for overlay in sorted([e for e in all_events if e.get('mask')],key=lambda e:(e.get('priority',0),e['start_frame'])):
            if final_base_contains_layer(base,overlay,frame):
                continue
            weight=event_weight(overlay,frame)
            if weight:
                source=_source_sample(armature,overlay,frame,fps,context=composed,operation=operation)
                composed=blend_pose(composed,source['pose'],weight,_mask(armature,overlay))
        set_pose(armature,composed)
        root.location=root_position
        root.rotation_quaternion=root_rotation
        root.scale=root_scale
        # FK/IK targets are applied after pose layers and before evaluated baking.
        mute_controls(actor,True)
        active=sorted([e for e in all_events if e['start_frame']<=frame<=e['end_frame'] and e.get('targets') and
                       not final_base_contains_layer(base,e,frame)],
                      key=lambda e:(e.get('priority',0),e['start_frame'],_coverage_id(e)))
        if active and not _base_only:
            if control_info is None:control_info=_controls_ensure_resolved(job_id,actor_id,actor)
            for event in active:
                applied=_apply_targets_resolved(actor,control_info,event['targets'],frame,event_weight(event,frame),
                                                actor_cache=actor_cache)
                target_diagnostics.append({'frame':frame,'event_id':event['event_id'],**applied})
        bpy.context.view_layer.update()
        baked.append((frame,read_pose(armature,evaluated=True),list(root.location),list(root.rotation_quaternion),list(root.scale)))
    mute_controls(actor,True)
    # Validate immutable source provenance again before producing final Action
    # keys. A filesystem edit during a long operation cannot silently mix clips.
    for motion_id,expected in operation.get('source_info',{}).items():
        actual=prepare_motion(armature,motion_id)
        if actual['source_sha256']!=expected['source_sha256'] or actual['source_frame_range']!=expected['source_frame_range']:
            raise ValueError('Motion source changed during this commit; re-plan this event: '+motion_id)
    if _base_only:
        # The group consumes these evaluated samples directly. Avoid an entire
        # intermediate Action write/read pass before same-frame IK solving.
        channel_facts={}
        for bone in armature.pose.bones:
            for constraint in bone.constraints:
                constraint.mute=True
    else:
        channel_facts=_write_baked_channels(job_id,actor_id,actor,baked)
    store['last_commit']={'frame_start':frame_start,'frame_end':frame_end,'frame_count':len(baked),'fps':fps,
                          'requested_range':list(requested_range),'captured_range':list(requested_range) if capture else None,
                          'actual_bake_range':[frame_start,frame_end],'preserved_existing_ranges':preserved_existing_ranges,
                          'root_policy_diagnostics':[_root_policy(event) for event in all_events],
                          'bone_channels':['location','rotation_quaternion','scale'],
                          'root_channels':['location','rotation_quaternion','scale'],
                          **channel_facts,
                          'sources':provenance,'status':'internal_base_only' if _base_only else 'baked_unreviewed',
                          'target_diagnostics':target_diagnostics,
                          'unreachable_targets':[{'frame':record['frame'],'event_id':record['event_id'],**target}
                                                 for record in target_diagnostics for target in record['targets']
                                                 if not target['reachable'] and target['influence']>0],
                          'naturalness_verified':False}
    _write(root,store)
    scene.frame_set(frame_start)
    return {'ok':True,'job_id':job_id,'actor_id':actor_id,**store['last_commit'],
            **({'base_samples':baked} if _base_only else {})}
