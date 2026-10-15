"""Persistent attacker POV using original registered skins and bone eye points.

No actor geometry is copied, split or relabelled.  The owner's original Head
and Neck groups can be masked only in render evaluation during the POV window.
"""
from __future__ import annotations

import json
import math
import hashlib

STORE = 'wm_pov_shots_v1'


def camera_check(job_id, requirements=None, frame_start=None, frame_end=None, detail=False):
    """Register optional shot requirements and inspect actual saved geometry."""
    from agent_camera_quality import check_camera_visibility
    return check_camera_visibility(job_id, requirements=requirements,
                                   frame_start=frame_start, frame_end=frame_end, detail=detail)


def _full_visibility_shot(camera, scene, actor_ids, focus_objects, spec, facts):
    applied = facts['applied']
    shot = {'camera': camera.name, 'frame_start': scene.frame_start,
            'frame_end': scene.frame_end, 'actor_ids': list(actor_ids),
            'focus_objects': [name for name, entry in focus_objects.items() if not entry.get('logical_group')],
            'focus_groups': {name: [mesh.name for mesh in entry['meshes']]
                             for name, entry in focus_objects.items() if entry.get('logical_group')},
            'framing': 'full_body',
            'margin': .04, 'fixed_transform': True, 'look_at': applied['look_at'],
            'expected_projection': applied['projection'],
            'reference_yaw_deg': applied.get('reference_yaw_deg', 0.)}
    for source, destination in (('position', 'expected_position'),
                                ('azimuth_deg', 'expected_azimuth_deg'),
                                ('elevation_deg', 'expected_elevation_deg'),
                                ('fov_deg', 'expected_fov_deg')):
        if source in spec:
            shot[destination] = spec[source]
    return shot


def _closer_camera_candidates(camera, facts, spec, scene):
    """Keep declared angles; a default lens may widen to avoid a room wall.

    Candidates are merely proposals. Every accepted result still passes the
    actual per-frame camera check. Explicit positions and FOVs are preserved.
    """
    from itertools import product
    from mathutils import Vector
    if 'position' in spec:
        return []
    look_at = Vector(facts['applied']['look_at'])
    offset = camera.location - look_at
    distance = offset.length
    if distance < .01:
        return []
    direction = offset.normalized()
    bounds = [Vector(p) for p in product(*zip(facts['min'], facts['max']))]
    aspect = (scene.render.resolution_x * scene.render.pixel_aspect_x) / (
        scene.render.resolution_y * scene.render.pixel_aspect_y)
    basis = camera.matrix_world.to_3x3()
    right, up, backward = (basis.col[i] for i in range(3))
    candidates = []
    for fraction in (.85, .70, .55, .45, .35):
        proposed_distance = distance * fraction
        location = look_at + direction * proposed_distance
        if location.z <= .01:
            continue
        fov = facts['applied']['fov_deg']
        if camera.data.type == 'PERSP' and 'fov_deg' not in spec:
            tangents = []
            for point in bounds:
                relative = point - look_at
                depth = proposed_distance - backward.dot(relative)
                if depth <= camera.data.clip_start:
                    break
                tangents.append(max(abs(right.dot(relative)),
                                    abs(up.dot(relative)) * aspect) / (depth * .84))
            else:
                fov = max(fov, math.degrees(2 * math.atan(max(tangents) * 1.02)))
                if fov <= 90:
                    candidates.append((location, fov))
                continue
            continue
        candidates.append((location, fov))
    return candidates


def camera_full(job_id, camera_spec, actor_ids=None, focus_objects=None):
    """Adapt the tested fixed-reference camera fitter to registered skins.

    This optional component's supported spec is not a natural-language gate;
    Hermes can author other camera behavior directly in bpy.  No mesh or actor
    animation is changed, and focus objects are actual scene mesh names.
    """
    import bpy
    from types import SimpleNamespace
    from agent_character import scene_actor_facts, resolve_actor
    from agent_camera_quality import STORE as VISIBILITY_STORE, camera_requirements
    from reference_camera import setup_reference_camera
    scene = bpy.context.scene
    identities = scene_actor_facts(job_id)
    if identities['errors']:
        raise ValueError('Fixed camera needs verified registered skin: ' + '; '.join(identities['errors']))
    selected = [actor['actor_id'] for actor in identities['actors']] if actor_ids is None else actor_ids
    if isinstance(selected, str):
        selected = [selected]
    if not isinstance(selected, (list, tuple)) or not selected:
        raise ValueError('actor_ids must identify existing registered actors')
    actors, meshes = {}, []
    for actor_id in dict.fromkeys(selected):
        actor = resolve_actor(job_id, actor_id)
        actors[actor_id] = {'root': actor['root'], 'arm': actor['armature'], 'meshes': actor['meshes']}
        meshes.extend(actor['meshes'])
    names = [focus_objects] if isinstance(focus_objects, (str, dict)) else (focus_objects or [])
    if not isinstance(names, (list, tuple)):
        raise ValueError('focus_objects must contain actual mesh names or logical mesh groups')
    props = {}
    for item in names:
        grouped = isinstance(item, dict)
        name = item.get('id') if grouped else item
        members = item.get('objects') if grouped else [item]
        if (not isinstance(name, str) or not name.strip() or not isinstance(members, (list, tuple))
                or not members or any(not isinstance(member, str) for member in members)):
            raise ValueError('A focus group needs a text id and actual mesh objects')
        if name in props:
            if [obj.name for obj in props[name]['meshes']] != list(dict.fromkeys(members)):
                raise ValueError('Conflicting focus target id: ' + name)
            continue
        objects = []
        for member in dict.fromkeys(members):
            obj = bpy.data.objects.get(member)
            if obj is None or obj.name not in scene.objects or obj.type != 'MESH':
                raise ValueError('Focus mesh is missing from current scene: ' + member)
            objects.append(obj)
        meshes.extend(objects)
        props[name] = {'root': objects[0], 'meshes': objects, 'logical_group': grouped,
                       'spec': {'type': 'focus_mesh', 'focus': True}}
    wrapper = SimpleNamespace(objects=props, wall_meshes=[], solids=[]) if props else None
    frame, subframe, old_camera = scene.frame_current, scene.frame_subframe, scene.camera
    previous_visibility = scene.get(VISIBILITY_STORE)
    previous_reference = scene.get('reference_camera_facts_json')
    previous_marker_cameras = {marker.name: marker.camera for marker in scene.timeline_markers}
    previous_pov = scene.get(STORE)
    original_objects, original_cameras = set(bpy.data.objects), set(bpy.data.cameras)
    pov_registry = json.loads(scene.get(STORE, '{"version":1,"shots":[]}'))
    managed_full_names = {obj.name for obj in scene.objects if obj.type == 'CAMERA' and obj.get('wm_full_job_id') == job_id}
    managed_full_names.update(shot['return_camera'] for shot in pov_registry['shots'])
    managed_full_names.update(marker.camera.name for marker in scene.timeline_markers
                              if marker.name.startswith('WM_POV_InitialCamera') and marker.camera)
    try:
        # Unmarked scene walls remain opaque. The visibility checker discovers
        # them from renderable geometry, independently of old recipe labels.
        fit_spec = dict(camera_spec)
        fit_spec.setdefault('occlusion_policy', 'strict')
        facts = setup_reference_camera(meshes, fit_spec, actors, props=wrapper)
        camera = bpy.data.objects[facts['applied']['camera_name']]
        camera['wm_full_job_id'] = job_id
        rebound = []
        for marker in scene.timeline_markers:
            if marker.camera and marker.camera.name in managed_full_names:
                marker.camera = camera
                rebound.append(marker.name)
        for shot in pov_registry['shots']:
            if shot['return_camera'] in managed_full_names:
                shot['return_camera'] = camera.name
            for record in shot.get('previous_camera_bindings', []):
                if record['prior_camera'] in managed_full_names:
                    record['prior_camera'] = camera.name
        if pov_registry['shots']:
            scene[STORE] = json.dumps(pov_registry, ensure_ascii=False, allow_nan=False)
        existing = camera_requirements(job_id).get('requirements') or {'version': 1, 'shots': []}
        retained = [shot for shot in existing['shots']
                    if shot['camera'] not in managed_full_names and shot['camera'] != camera.name]

        def register():
            full = _full_visibility_shot(camera, scene, actors, props, camera_spec, facts)
            camera_requirements(job_id, {'version': 1, 'shots': retained + [full]})

        sample_frames = sorted({scene.frame_start, scene.frame_end, *(
            round(scene.frame_start + (scene.frame_end - scene.frame_start) * i / 11)
            for i in range(1, 11))})

        def sample_visibility():
            result = None
            tested = []
            for sample in sample_frames:
                result = camera_check(job_id, frame_start=sample, frame_end=sample)
                tested.append(sample)
                if not result['passed']:
                    break
            result['helper_sample_frames'] = tested
            result['helper_sample_scope'] = 'Representative fit checks; independent backend checks every frame before publishing.'
            return result

        register()
        visibility = sample_visibility()
        adjustment = None
        if not visibility['passed']:
            original_location = camera.location.copy()
            original_fov = facts['applied']['fov_deg']
            for location, fov in _closer_camera_candidates(camera, facts, camera_spec, scene):
                camera.location = location
                if camera.data.type == 'PERSP':
                    camera.data.lens = 36 / (2 * math.tan(math.radians(fov) / 2))
                bpy.context.view_layer.update()
                facts['applied']['position'] = list(camera.location)
                facts['applied']['fov_deg'] = fov
                register()
                visibility = sample_visibility()
                if visibility['passed']:
                    adjustment = {'reason': 'opaque_scene_geometry_occluded_initial_camera',
                                  'previous_position': list(original_location),
                                  'previous_fov_deg': original_fov,
                                  'position': list(camera.location), 'fov_deg': fov,
                                  'declared_angles_preserved': True,
                                  'actor_or_prop_animation_modified': False}
                    break
            if not visibility['passed']:
                raise ValueError('相机可见性检查失败；保留用户明确位置/角度和原动作，'
                                 '请根据实际遮挡物选择满足原文的机位后重新检查。' +
                                 json.dumps({'failure_count': visibility.get('failure_count'),
                                             'failures': visibility.get('failures', [])[:8]},
                                            ensure_ascii=False))
        camera['reference_camera_facts_json'] = json.dumps(facts)
        scene['reference_camera_facts_json'] = json.dumps(facts)
        return {'ok': True, 'job_id': job_id, 'camera': camera.name, 'facts': facts,
                'actor_ids': list(actors), 'focus_objects': list(props),
                'focus_groups': {name: [obj.name for obj in entry['meshes']]
                                 for name, entry in props.items() if entry['logical_group']},
                'geometry_copied': False, 'actor_animation_modified': False,
                'managed_full_markers_updated': rebound,
                'angle_convention': 'world front=-Y, right=+X; +elevation is above subject',
                'visibility': visibility, 'automatic_visibility_adjustment': adjustment,
                'backend_full_frame_visibility_required': True,
                'status': 'fixed_reference_camera_ready_render_review_required'}
    except Exception:
        # The old fitter may create its camera before rejecting an explicit
        # position. Remove only objects/data introduced by this failed call.
        for obj in set(bpy.data.objects) - original_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in set(bpy.data.cameras) - original_cameras:
            bpy.data.cameras.remove(data, do_unlink=True)
        scene.camera = old_camera
        for marker in scene.timeline_markers:
            if marker.name in previous_marker_cameras:
                marker.camera = previous_marker_cameras[marker.name]
        for key, previous in ((VISIBILITY_STORE, previous_visibility),
                              ('reference_camera_facts_json', previous_reference),
                              (STORE, previous_pov)):
            if previous is None:
                if key in scene:
                    del scene[key]
            else:
                scene[key] = previous
        raise
    finally:
        scene.frame_set(frame, subframe=subframe)


def _frame(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or int(value) != value):
        raise ValueError(name + ' must be an integer frame')
    return int(value)


def _camera_at(scene, frame, default):
    markers = sorted((marker for marker in scene.timeline_markers if marker.camera and marker.frame <= frame),
                     key=lambda marker: marker.frame)
    return markers[-1].camera if markers else default


def _mask_animation_paths(obj):
    return {modifier.path_from_id('show_render') for modifier in obj.modifiers
            if modifier.type == 'MASK' and modifier.name in ('WM_POV_RenderOnly_Head', 'WM_POV_RenderOnly_Neck')}


def _remove_mask_curves(obj):
    ad = obj.animation_data
    if not ad or not ad.action:
        return
    if ad.action.users > 1:
        identifier = ad.action_slot.identifier if ad.action_slot else None
        ad.action = ad.action.copy()
        if identifier:
            slot = next((slot for slot in ad.action.slots if slot.identifier == identifier), None)
            if slot is not None:
                ad.action_slot = slot
    paths = _mask_animation_paths(obj)
    for layer in ad.action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in list(bag.fcurves):
                    if curve.data_path in paths:
                        bag.fcurves.remove(curve)


def _constant_boolean_curves(obj):
    ad = obj.animation_data
    if not ad or not ad.action:
        return
    for layer in ad.action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    if curve.data_path in _mask_animation_paths(obj):
                        for key in curve.keyframe_points:
                            key.interpolation = 'CONSTANT'


def _render_masks(actor, windows, scene):
    """Reuse existing group weights; viewport deformation stays unmasked."""
    records = []
    for mesh in actor['meshes']:
        _remove_mask_curves(mesh)
        for short in ('Head', 'Neck'):
            group = next((group for group in mesh.vertex_groups if group.name.split(':')[-1] == short), None)
            if group is None:
                continue
            # Do not overwrite a caller's independent mask modifier.
            name = 'WM_POV_RenderOnly_' + short
            modifier = mesh.modifiers.get(name)
            if modifier is not None and modifier.type != 'MASK':
                raise ValueError('POV mask name is occupied by another modifier')
            if modifier is None:
                modifier = mesh.modifiers.new(name, 'MASK')
            modifier.mode = 'VERTEX_GROUP'
            modifier.vertex_group = group.name
            modifier.invert_vertex_group = True
            modifier.use_smooth = False
            modifier.threshold = .15
            modifier.show_viewport = False
            points = {scene.frame_start: False, scene.frame_end + 1: False}
            for first, last in windows:
                points[first - 1] = False
                points[first] = True
                points[last + 1] = False
            # Resolve touching/overlapping windows using the union, not order.
            for time in sorted(points):
                modifier.show_render = any(first <= time <= last for first, last in windows)
                modifier.keyframe_insert('show_render', frame=time)
            _constant_boolean_curves(mesh)
            records.append({'mesh': mesh.name, 'modifier': modifier.name, 'group': group.name,
                            'render_only': True, 'new_vertex_group': False, 'threshold': modifier.threshold})
    return records


def camera_pov(job_id, actor_id, target_actor_id, start_frame, end_frame, fov=75, return_camera=None,
               shot_id=None):
    """Bind an inclusive POV interval and return to the specified/prior camera.

    ``actor_id`` owns the eyes/arms; ``target_actor_id`` is the observed actor.
    fov is horizontal degrees.  Original skins/weights remain unchanged.
    """
    import bpy
    from mathutils import Vector
    from agent_character import resolve_actor, binding_facts
    first, last = _frame(start_frame, 'start_frame'), _frame(end_frame, 'end_frame')
    if last < first:
        raise ValueError('POV interval is inverted')
    if isinstance(fov, bool) or not isinstance(fov, (int, float)) or not math.isfinite(fov) or not 0 < fov < 179:
        raise ValueError('fov must be finite horizontal degrees between 0 and 179')
    if actor_id == target_actor_id:
        raise ValueError('POV target must be a different existing actor')
    if shot_id is not None and (not isinstance(shot_id, str) or not shot_id.strip()):
        raise ValueError('shot_id must be nonempty stable text')
    shot_id = shot_id.strip() if shot_id is not None else str(actor_id) + '_' + str(first) + '_' + str(last)
    owner, observed = resolve_actor(job_id, actor_id), resolve_actor(job_id, target_actor_id)
    scene = bpy.context.scene
    registry = json.loads(scene.get(STORE, '{"version":1,"shots":[]}'))
    existing = next((shot for shot in registry['shots'] if shot['shot_id'] == shot_id), None)
    for shot in registry['shots']:
        if shot['shot_id'] != shot_id and max(first, shot['start_frame']) <= min(last, shot['end_frame']):
            raise ValueError('POV camera window overlaps existing shot ' + shot['shot_id'] +
                             '; update that shot_id or use nonoverlapping camera intervals')
    previous_owner = resolve_actor(job_id, existing['actor_id']) if existing and existing['actor_id'] != actor_id else None
    old_frame, old_subframe, old_camera = scene.frame_current, scene.frame_subframe, scene.camera
    if return_camera is None:
        full = bpy.data.objects.get(existing['return_camera']) if existing else _camera_at(scene, first - 1, scene.camera)
    else:
        full = bpy.data.objects.get(return_camera) if isinstance(return_camera, str) else return_camera
    if full is None or full.type != 'CAMERA' or full.name not in scene.objects:
        raise ValueError('Return camera must be an existing camera in this scene')
    camera = bpy.data.objects.get(existing['camera']) if existing else None
    token = hashlib.sha256(shot_id.encode('utf-8')).hexdigest()[:12]
    if existing:
        for marker_name in existing.get('marker_names', [f'WM_POV_{shot_id}_Start', f'WM_POV_{shot_id}_Return']):
            marker = scene.timeline_markers.get(marker_name)
            if marker is not None:
                scene.timeline_markers.remove(marker)
        for record in existing.get('previous_camera_bindings', []):
            marker = scene.timeline_markers.get(record['marker'])
            previous = bpy.data.objects.get(record['prior_camera'])
            if marker and previous and marker.camera == camera:
                marker.camera = previous
    if camera is None:
        data = bpy.data.cameras.new(f'WM_{job_id}_{token}_POV')
        camera = bpy.data.objects.new(data.name, data)
        scene.collection.objects.link(camera)
    if camera.type != 'CAMERA':
        raise ValueError('Registered POV object is not a camera')
    camera.data.type = 'PERSP'
    camera.data.sensor_fit = 'HORIZONTAL'
    camera.data.sensor_width = 36
    camera.data.lens = 36 / (2 * math.tan(math.radians(fov) / 2))
    camera.data.clip_start = .02
    camera.data.clip_end = max(100, full.data.clip_end)
    camera.rotation_mode = 'QUATERNION'
    camera['wm_pov_job_id'], camera['wm_pov_owner'], camera['wm_pov_target'] = job_id, actor_id, target_actor_id
    previous_action = camera.animation_data.action if camera.animation_data else None
    camera.animation_data_clear()
    if previous_action is not None and previous_action.users == 0:
        bpy.data.actions.remove(previous_action)
    before = binding_facts(owner['meshes'], owner['armature'])['signature']
    points = []
    previous_quaternion = None
    try:
        for frame in range(first, last + 1):
            scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            arm = owner['armature'].evaluated_get(depsgraph)
            head = arm.pose.bones[owner['info']['bone_roles']['head']]
            head_world = arm.matrix_world @ head.matrix
            forward = (head_world.to_3x3() @ Vector((0, 0, 1))).normalized()
            eye = arm.matrix_world @ head.head.lerp(head.tail, .48) + forward * .065 + Vector((0, 0, .02))
            other = observed['armature'].evaluated_get(depsgraph)
            roles = observed['info']['bone_roles']
            other_head = other.pose.bones[roles['head']]
            torso = other.pose.bones[roles['spine2']]
            aim = (other.matrix_world @ other_head.head.lerp(other_head.tail, .48)).lerp(
                other.matrix_world @ torso.head.lerp(torso.tail, .4), .72)
            if (aim - eye).length <= 1e-6:
                raise ValueError('POV eye and observed target coincide')
            camera.location = eye
            rotation = (aim - eye).to_track_quat('-Z', 'Y')
            if previous_quaternion is not None and rotation.dot(previous_quaternion) < 0:
                rotation.negate()
            previous_quaternion = rotation.copy()
            camera.rotation_quaternion = rotation
            camera.keyframe_insert('location', frame=frame)
            camera.keyframe_insert('rotation_quaternion', frame=frame)
            points.append({'frame': frame, 'eye': list(eye), 'target': list(aim)})
        for layer in camera.animation_data.action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        for key in curve.keyframe_points:
                            key.interpolation = 'LINEAR'
        shot = {'shot_id': shot_id, 'actor_id': actor_id, 'target_actor_id': target_actor_id,
                'start_frame': first, 'end_frame': last, 'camera': camera.name, 'return_camera': full.name,
                'fov_horizontal_deg': float(fov), 'eye_method': 'evaluated_Head_48pct_forward_65mm_world_up_20mm',
                'gaze_method': 'evaluated_target_head_28pct_torso_72pct',
                'head_pose_is_bone_based': True, 'eye_anatomy_exact': False}
        registry['shots'] = [old for old in registry['shots'] if old['shot_id'] != shot_id] + [shot]
        windows = [(item['start_frame'], item['end_frame']) for item in registry['shots'] if item['actor_id'] == actor_id]
        masks = _render_masks(owner, windows, scene)
        if previous_owner:
            old_windows = [(item['start_frame'], item['end_frame']) for item in registry['shots']
                           if item['actor_id'] == existing['actor_id']]
            _render_masks(previous_owner, old_windows, scene)
        shot['render_masks'] = masks
        shot['self_head_masked_for_render'] = bool(masks)
        replacements = []
        # The requested interval owns camera cuts within it.  Keep all marker
        # names/frames and record prior cameras instead of deleting user data.
        for marker in scene.timeline_markers:
            if first <= marker.frame <= last and marker.camera and marker.camera != camera:
                replacements.append({'marker': marker.name, 'frame': marker.frame, 'prior_camera': marker.camera.name})
                marker.camera = camera
            elif marker.frame == last + 1 and marker.camera and marker.camera != full:
                replacements.append({'marker': marker.name, 'frame': marker.frame, 'prior_camera': marker.camera.name})
                marker.camera = full
        names = [f'WM_POV_{token}_Start', f'WM_POV_{token}_Return']
        for name, frame, bound in ((names[0], first, camera), (names[1], last + 1, full)):
            marker = scene.timeline_markers.get(name) or scene.timeline_markers.new(name, frame=frame)
            marker.frame, marker.camera = frame, bound
        if not any(marker.camera and marker.frame < first for marker in scene.timeline_markers):
            marker = scene.timeline_markers.new('WM_POV_InitialCamera', frame=min(scene.frame_start, first - 1))
            marker.camera = full
        shot['marker_names'], shot['previous_camera_bindings'] = names, replacements
        scene[STORE] = json.dumps(registry, ensure_ascii=False, allow_nan=False)
        after = binding_facts(owner['meshes'], owner['armature'])['signature']
        if after != before:
            raise RuntimeError('POV changed original mesh/weights identity')
        from agent_camera_quality import camera_requirements
        requirements = camera_requirements(job_id).get('requirements') or {'version': 1, 'shots': []}
        requirements['shots'] = [item for item in requirements['shots'] if item['camera'] != camera.name]
        requirements['shots'].append({'camera': camera.name, 'frame_start': first,
                                     'frame_end': last, 'actor_ids': [target_actor_id],
                                     'focus_objects': [], 'framing': 'regions',
                                     'regions': ['trunk'], 'margin': .02,
                                     'fixed_transform': False,
                                     'expected_projection': 'perspective',
                                     'expected_fov_deg': float(fov)})
        camera_requirements(job_id, requirements)
        return {'ok': True, 'job_id': job_id, **shot, 'sample_count': len(points),
                'first_eye_sample': points[0], 'last_eye_sample': points[-1],
                'original_skin_identity_preserved': True, 'geometry_copied': False,
                'viewport_masking': False, 'prior_camera_bindings_inside_window': replacements,
                'status': 'camera_baked_render_review_required',
                'limitations': ['Head bone supplies approximate eye location; preview must verify own arms and target visibility.',
                                'Camera is baked from current motion; rebuild after changing either actor motion.']}
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
        scene.camera = _camera_at(scene, old_frame, old_camera)
