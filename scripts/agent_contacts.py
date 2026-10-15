"""Persistent geometric contact requirements derived from open motion events.

Object local points and pose-bone local points use the actual Blender object's
or bone's local units, including its scale. Resolved positions are world metres.
Mesh vertex indices address evaluated geometry. These are proximity checks, not
proof of collision response, force, balance, or natural-looking movement.
"""
from __future__ import annotations

import json
import math

VERSION = 1
TIMELINE_STORE = 'wm_motion_timeline_v1'


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be a finite number')
    return float(value)


def _integer(value, label):
    number = _number(value, label)
    if int(number) != number:
        raise ValueError(label + ' must be an integer')
    return int(number)


def _vector(value, size, label):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(label + ' must contain ' + str(size) + ' coordinates')
    values = [_number(x, label) for x in value]
    if size == 4:
        length = math.sqrt(sum(x*x for x in values))
        if length < 1e-12:
            raise ValueError(label + ' quaternion must be nonzero')
        values = [x/length for x in values]
    return values


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty name')
    return value


def validate_binding(binding):
    if not isinstance(binding, dict):
        raise ValueError('contact binding must be a dictionary')
    result = json.loads(json.dumps(binding, allow_nan=False))
    actor, obj = 'actor_id' in result, 'object' in result
    if actor == obj:
        raise ValueError('contact binding needs exactly one actor_id or object')
    if actor:
        _name(result['actor_id'], 'binding.actor_id')
        _name(result.get('bone'), 'binding.bone')
        if 'vertex_index' in result:
            raise ValueError('vertex binding needs an actual object mesh, not actor_id')
    else:
        _name(result['object'], 'binding.object')
        if 'bone' in result:
            raise ValueError('bone binding needs actor_id and bone')
    if 'vertex_index' in result:
        if 'local_point' in result:
            raise ValueError('vertex_index and local_point cannot address the same binding')
        result['vertex_index'] = _integer(result['vertex_index'], 'binding.vertex_index')
        if result['vertex_index'] < 0:
            raise ValueError('binding.vertex_index must be nonnegative')
    else:
        result['local_point'] = _vector(result.get('local_point', [0, 0, 0]), 3, 'binding.local_point')
    return result


def _validate_state(state):
    for field in ('position', 'pole', 'effector_point'):
        if field in state:
            state[field] = _vector(state[field], 3, field)
    if 'rotation_quaternion' in state:
        state['rotation_quaternion'] = _vector(state['rotation_quaternion'], 4, 'rotation_quaternion')
    if 'influence' in state:
        state['influence'] = _number(state['influence'], 'influence')
        if not 0 <= state['influence'] <= 1:
            raise ValueError('target influence must be in [0,1]')
    if 'binding' in state:
        state['binding'] = validate_binding(state['binding'])
    if 'effector' in state:
        state['effector'] = validate_binding(state['effector'])
        if 'object' not in state['effector'] or 'vertex_index' not in state['effector']:
            raise ValueError('effector needs an actual actor mesh object and vertex_index')
        if 'effector_point' in state:
            raise ValueError('choose effector mesh vertex or effector_point, not both')
    if state.get('interpolation', 'smooth') not in ('smooth', 'linear'):
        raise ValueError('target interpolation must be smooth or linear')
    if 'binding' in state and 'position' in state:
        raise ValueError('target uses binding or absolute position, not both')
    return state


def validate_targets(targets, event_start=None, event_end=None):
    """Validate execution fields, retaining arbitrary contact intent metadata."""
    if not isinstance(targets, dict):
        raise ValueError('targets must be a dictionary')
    result = json.loads(json.dumps(targets, allow_nan=False))
    for chain, spec in result.items():
        _name(chain, 'target chain')
        if not isinstance(spec, dict):
            raise ValueError('target specification must be a dictionary: ' + chain)
        _validate_state(spec)
        keys = spec.get('keyframes')
        if keys is not None:
            if not isinstance(keys, list) or not keys:
                raise ValueError('target keyframes must be a nonempty list')
            seen = set()
            carry = {k: v for k, v in spec.items() if k not in ('keyframes', 'contact')}
            for key in keys:
                if not isinstance(key, dict) or 'frame' not in key:
                    raise ValueError('target keyframe needs an integer frame')
                key['frame'] = _integer(key['frame'], 'target keyframe.frame')
                if key['frame'] in seen:
                    raise ValueError('duplicate target keyframe frame')
                seen.add(key['frame'])
                if 'contact' in key:
                    raise ValueError('contact window belongs to target specification, not a keyframe')
                _validate_state(key)
            keys.sort(key=lambda key: key['frame'])
            for key in keys:
                carry.update(key)
                _validate_state(carry)
                if 'position' not in carry and 'binding' not in carry:
                    raise ValueError('target keyframes need a position or binding')
        elif 'position' not in spec and 'binding' not in spec:
            raise ValueError('target needs a position or binding')
        if 'contact' in spec and spec['contact'] is not False:
            contact = spec['contact']
            if not isinstance(contact, dict):
                raise ValueError('contact must be a dictionary with a frame window')
            for name, fallback in (('start_frame', event_start), ('end_frame', event_end)):
                value = contact.get(name, fallback)
                if value is None:
                    raise ValueError('contact.' + name + ' is required outside an event')
                contact[name] = _integer(value, 'contact.' + name)
            if contact['end_frame'] < contact['start_frame']:
                raise ValueError('contact has an empty or inverted frame window')
            if ((event_start is not None and contact['start_frame'] < event_start) or
                    (event_end is not None and contact['end_frame'] > event_end)):
                raise ValueError('contact window must be within its motion event')
            tolerance = _number(contact.get('tolerance_m', .03), 'contact.tolerance_m')
            if tolerance <= 0:
                raise ValueError('contact.tolerance_m must be positive')
            contact['tolerance_m'] = tolerance
            for name in ('id', 'kind'):
                if name in contact:
                    _name(contact[name], 'contact.'+name)
    return result


def _quaternion_mix(first, second, t):
    a, b = _vector(first, 4, 'quaternion'), _vector(second, 4, 'quaternion')
    dot = sum(x*y for x, y in zip(a, b))
    if dot < 0:
        b = [-x for x in b]; dot = -dot
    if dot > .9995:
        return _vector([x+(y-x)*t for x, y in zip(a, b)], 4, 'quaternion')
    angle = math.acos(max(-1, min(1, dot)))
    return [(math.sin((1-t)*angle)*x+math.sin(t*angle)*y)/math.sin(angle) for x, y in zip(a, b)]


def sample_target(spec, frame):
    """Merge top-level settings with held/interpolated sparse target keys."""
    state = {k: v for k, v in spec.items() if k != 'keyframes'}
    keys = sorted(spec.get('keyframes', []), key=lambda key: key['frame'])
    if not keys:
        return json.loads(json.dumps(state))
    prepared = []
    carry = dict(state)
    for key in keys:
        carry = {**carry, **key}
        prepared.append(dict(carry))
    if frame <= prepared[0]['frame']:
        return json.loads(json.dumps(prepared[0]))
    if frame >= prepared[-1]['frame']:
        return json.loads(json.dumps(prepared[-1]))
    left, right = next((a, b) for a, b in zip(prepared, prepared[1:]) if a['frame'] <= frame <= b['frame'])
    t = (frame-left['frame'])/(right['frame']-left['frame'])
    if left.get('interpolation', 'smooth') == 'smooth':
        t = t*t*(3-2*t)
    state = dict(left)
    for field in ('position', 'pole', 'effector_point'):
        if field in left and field in right:
            state[field] = [a+(b-a)*t for a, b in zip(left[field], right[field])]
    state['influence'] = left.get('influence', 1)+(right.get('influence', 1)-left.get('influence', 1))*t
    if 'rotation_quaternion' in left and 'rotation_quaternion' in right:
        state['rotation_quaternion'] = _quaternion_mix(left['rotation_quaternion'], right['rotation_quaternion'], t)
    if 'binding' in left and 'binding' in right:
        a, b = left['binding'], right['binding']
        identity = lambda value: {k: v for k, v in value.items() if k != 'local_point'}
        if identity(a) == identity(b) and 'local_point' in a and 'local_point' in b:
            state['binding'] = {**a, 'local_point': [x+(y-x)*t for x, y in zip(a['local_point'], b['local_point'])]}
    return json.loads(json.dumps(state))


def _resolve_bone(armature, name):
    bone = armature.pose.bones.get(name)
    if bone is None:
        matches = [b for b in armature.pose.bones if b.name.split(':')[-1] == name]
        if len(matches) != 1:
            raise ValueError('contact bone missing or ambiguous: ' + str(name))
        bone = matches[0]
    return bone


def resolve_binding(job_id, binding):
    """Resolve a target against the current evaluated scene at its current frame."""
    import bpy
    from mathutils import Vector
    binding = validate_binding(binding)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    if 'actor_id' in binding:
        from agent_character import resolve_actor
        actor = resolve_actor(job_id, binding['actor_id'])
        obj = actor['armature'].evaluated_get(depsgraph)
        bone = _resolve_bone(obj, binding['bone'])
        matrix = obj.matrix_world @ bone.matrix
        position = matrix @ Vector(binding['local_point'])
        scope = 'evaluated_pose_bone_local_point'
    else:
        original = bpy.data.objects.get(binding['object'])
        if original is None or original.name not in bpy.context.scene.objects:
            raise ValueError('contact target object is missing from current scene: ' + binding['object'])
        obj = original.evaluated_get(depsgraph)
        matrix = obj.matrix_world
        if 'vertex_index' in binding:
            if obj.type != 'MESH':
                raise ValueError('contact vertex target is not a mesh: ' + obj.name)
            mesh = obj.to_mesh()
            try:
                index = binding['vertex_index']
                if index >= len(mesh.vertices):
                    raise ValueError('contact vertex_index outside evaluated mesh: ' + str(index))
                position = matrix @ mesh.vertices[index].co
            finally:
                obj.to_mesh_clear()
            scope = 'evaluated_mesh_vertex'
        else:
            position = matrix @ Vector(binding['local_point'])
            scope = 'evaluated_object_local_point'
    if not all(math.isfinite(x) for x in position):
        raise ValueError('contact target has nonfinite evaluated coordinates')
    return {'position': list(position), 'rotation_quaternion': list(matrix.to_quaternion()), 'scope': scope}


def resolve_effector(job_id, actor, chain, state):
    """Measure one actual skin vertex or a point in the end bone's local units."""
    import bpy
    from mathutils import Vector
    if 'effector' in state:
        binding = state['effector']
        if binding.get('object') not in {mesh.name for mesh in actor['meshes']}:
            raise ValueError('contact effector mesh must belong to the acting character')
        result = resolve_binding(job_id, binding)
        result['scope'] = 'evaluated_actor_skin_vertex'
        return result
    armature = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    bone = _resolve_bone(armature, chain)
    matrix = armature.matrix_world @ bone.matrix
    point = matrix @ Vector(state.get('effector_point', [0, 0, 0]))
    if not all(math.isfinite(x) for x in point):
        raise ValueError('contact effector has nonfinite evaluated coordinates')
    return {'position': list(point), 'rotation_quaternion': list(matrix.to_quaternion()),
            'scope': 'evaluated_bone_local_point' if 'effector_point' in state else 'evaluated_bone_origin'}


def _requirements_for_events(actor_id, events):
    if not isinstance(events, list):
        raise ValueError('registered contact timeline events must be a list')
    contacts = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('registered contact timeline event must be a dictionary')
        if 'targets' not in event:
            continue
        start = _integer(event.get('start_frame'), 'event.start_frame')
        end = _integer(event.get('end_frame'), 'event.end_frame')
        if end < start:
            raise ValueError('registered contact event has inverted frame window')
        weight = _number(event.get('weight', 1), 'event.weight')
        if not 0 <= weight <= 1:
            raise ValueError('registered contact event weight must be in [0,1]')
        for field in ('blend_in_frames', 'blend_out_frames'):
            if _number(event.get(field, 0), 'event.'+field) < 0:
                raise ValueError('registered contact event blend frames must be nonnegative')
        event_id = _name(event.get('event_id'), 'event_id')
        targets = validate_targets(event['targets'], start, end)
        for chain, spec in sorted(targets.items()):
            if spec.get('contact') is False:
                continue
            has_binding = 'binding' in spec or any('binding' in key for key in spec.get('keyframes', []))
            if not has_binding and 'contact' not in spec:
                continue
            contact = spec.get('contact', {})
            contacts.append({**contact, 'id': str(contact.get('id', actor_id+':'+event_id+':'+chain)),
                             'actor_id': actor_id, 'event_id': event_id, 'chain': chain,
                             'frame_start': contact.get('start_frame', start),
                             'frame_end': contact.get('end_frame', end),
                             'tolerance_m': contact.get('tolerance_m', .03),
                             'kind': contact.get('kind', 'contact'), 'target': spec,
                             'event_weight': {key: event[key] for key in ('start_frame', 'end_frame', 'weight',
                                                                        'blend_in_frames', 'blend_out_frames') if key in event}})
    return contacts


def contact_requirements(job_id):
    """Derive requirements without introducing another mutable scene registry."""
    from agent_character import actor_list, resolve_actor
    contacts = []
    for item in actor_list(job_id)['actors']:
        actor = resolve_actor(job_id, item['actor_id'])
        store = json.loads(actor['root'].get(TIMELINE_STORE, '{"version":1,"events":[]}'))
        if (not isinstance(store, dict) or not isinstance(store.get('events', []), list) or
                type(store.get('version', 1)) is not int or store.get('version', 1) != VERSION):
            raise ValueError('registered motion timeline is malformed')
        contacts.extend(_requirements_for_events(item['actor_id'], store.get('events', [])))
    ids = [entry['id'] for entry in contacts]
    if len(set(ids)) != len(ids):
        raise ValueError('contact requirement ids must be unique across the task')
    return {'version': VERSION, 'status': 'registered' if contacts else 'empty',
            'requirements': {'version': VERSION, 'contacts': contacts}}


def check_contacts(job_id, frame_start=None, frame_end=None, actor_id=None, detail=False):
    """Check all registered contact windows after baking, including muted IK.

    Explicit ranges report partial coverage. Failed measurements and missing
    targets cannot be silently converted into an empty/successful contact set.
    """
    import bpy
    from agent_character import resolve_actor
    from agent_motion_timeline import event_weight
    scene = bpy.context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    failures, summaries, measurements = [], [], []
    current = {}
    try:
        registration = contact_requirements(job_id)
        contacts = registration['requirements']['contacts']
        if actor_id is not None:
            resolve_actor(job_id, actor_id)
            contacts = [entry for entry in contacts if entry['actor_id'] == actor_id]
        begin = scene.frame_start if frame_start is None else _integer(frame_start, 'frame_start')
        finish = scene.frame_end if frame_end is None else _integer(frame_end, 'frame_end')
        if finish < begin:
            raise ValueError('contact check has inverted frame range')
        for entry in contacts:
            current = {key: entry[key] for key in ('id', 'actor_id', 'event_id', 'chain')}
            if entry['frame_start'] < scene.frame_start or entry['frame_end'] > scene.frame_end:
                raise ValueError('contact window is outside the saved scene frame range: '+entry['id'])
            start, end = max(begin, entry['frame_start']), min(finish, entry['frame_end'])
            expected = entry['frame_end']-entry['frame_start']+1
            summary = {key: entry[key] for key in ('id', 'actor_id', 'event_id', 'chain', 'kind', 'frame_start', 'frame_end', 'tolerance_m')}
            summary.update(expected_frames=expected, tested_frames=max(0, end-start+1),
                           coverage='full' if (start, end) == (entry['frame_start'], entry['frame_end']) else 'partial',
                           max_distance_m=None, failure_count=0, passed=True)
            if end < start:
                summary.update(passed=False, failure_count=1)
                failures.append({'check': 'contact_window_not_covered', 'id': entry['id'], 'actor_id': entry['actor_id'],
                                 'event_id': entry['event_id'], 'chain': entry['chain']})
            actor = resolve_actor(job_id, entry['actor_id'])
            for frame in range(start, end+1):
                current['frame'] = frame
                scene.frame_set(frame); bpy.context.view_layer.update()
                state = sample_target(entry['target'], frame)
                target = resolve_binding(job_id, state['binding']) if 'binding' in state else {
                    'position': state['position'], 'scope': 'explicit_world_point'}
                effector = resolve_effector(job_id, actor, entry['chain'], state)
                distance = math.dist(target['position'], effector['position'])
                if not math.isfinite(distance):
                    raise ValueError('nonfinite measured contact distance')
                summary['max_distance_m'] = max(summary['max_distance_m'] or 0, distance)
                summary['target_scope'], summary['effector_scope'] = target['scope'], effector['scope']
                measured = {'id': entry['id'], 'actor_id': entry['actor_id'], 'event_id': entry['event_id'],
                            'chain': entry['chain'], 'frame': frame, 'distance_m': distance,
                            'tolerance_m': entry['tolerance_m'], 'passed': distance <= entry['tolerance_m'],
                            'planned_influence': state.get('influence', 1)*event_weight(entry['event_weight'], frame),
                            'target_scope': target['scope'], 'effector_scope': effector['scope']}
                if detail:
                    measurements.append(measured)
                if not measured['passed']:
                    summary['passed'] = False; summary['failure_count'] += 1
                    failures.append({'check': 'contact_distance', **measured})
            summaries.append(summary)
        return {'ok': True, 'passed': not failures, 'status': 'empty' if not contacts else ('failed' if failures else 'passed'),
                'version': VERSION, 'job_id': job_id, 'failure_count': len(failures), 'failures': failures if detail else failures[:60],
                'contacts': summaries, 'tested_frames': sum(entry['tested_frames'] for entry in summaries),
                'requirement_count': len(contacts),
                'scope': 'registered same-frame geometric contact windows; bone/local-point/skin-vertex scope reported per contact',
                'naturalness_verified': False, 'dynamics_verified': False,
                **({'frames': measurements, 'requirements': registration['requirements']} if detail else {})}
    except Exception as exc:
        return {'ok': False, 'passed': False, 'status': 'error', 'version': VERSION, 'job_id': job_id,
                'failure_count': len(failures)+1, 'failures': (failures+[{'check': 'contact_contract_error', **current, 'error': str(exc)}])[:60],
                'contacts': summaries, 'scope': 'contact validation or evaluated-target resolution failed',
                'naturalness_verified': False, 'dynamics_verified': False}
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
