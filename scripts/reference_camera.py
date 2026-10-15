"""Fixed reference cameras with explicit coordinate frames and visibility QA.

Angles describe the camera's position around its look-at point: front is -Y,
right is +X, and positive elevation is above the subject.  No angle fallback
is allowed.  Display transparency never changes physical collision solids.
"""
import copy
import json
import math

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from camera_plan import validate_camera


FRAME_MARGIN = .05
FIT_MARGIN = .08
OPAQUE_ALPHA = .8


def _field(actor, name, default=None):
    return actor.get(name, default) if isinstance(actor, dict) else getattr(actor, name, default)


def _meshes(actor):
    return list(_field(actor, 'meshes', []))


def _corners(meshes, deps):
    return [obj.matrix_world @ Vector(p) for mesh in meshes
            if not mesh.hide_render for obj in [mesh.evaluated_get(deps)] for p in obj.bound_box]


def _opaque(obj):
    """Inspect the surface shader, rather than just viewport diffuse alpha."""
    if obj.hide_render:
        return False
    if not obj.data.materials:
        return True
    for mat in obj.data.materials:
        if mat is None:
            return True
        alpha = float(mat.diffuse_color[3])
        if mat.use_nodes and mat.node_tree:
            outputs = [n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output]
            output = outputs[0] if outputs else None
            links = list(output.inputs['Surface'].links) if output else []
            if not links:
                return True
            shader = links[0].from_node
            if shader.type != 'BSDF_PRINCIPLED' or shader.inputs['Alpha'].is_linked:
                # Unknown/mixed materials are conservatively opaque.
                return True
            alpha = float(shader.inputs['Alpha'].default_value)
        if alpha >= OPAQUE_ALPHA:
            return True
    return False


def _transparent_architecture(props):
    if props is None:
        return {'policy': 'transparent_architecture', 'objects': [], 'collision_solids_retained': True}
    architecture = {obj.name: (obj, .04) for obj in props.wall_meshes}
    for ident, entry in props.objects.items():
        if entry['spec']['type'] not in ('door', 'window'):
            continue
        for obj in entry['meshes']:
            suffix = obj.name.removeprefix(ident+'_')
            if suffix.startswith(('frame_', 'sash_')) or suffix == 'sill':
                architecture[obj.name] = (obj, .04)
            elif suffix in ('leaf', 'panel', 'glass', 'pane'):
                architecture[obj.name] = (obj, .12)
    changed = []
    for obj, alpha in architecture.values():
        if obj.hide_render:
            continue
        for index, original in enumerate(list(obj.data.materials)):
            if original is None:
                continue
            mat = original.copy(); mat.name = obj.name+'_ReferenceCamera'
            mat.use_nodes = True
            mat.diffuse_color = (*mat.diffuse_color[:3], alpha)
            shader = mat.node_tree.nodes.get('Principled BSDF')
            if shader is None:
                raise ValueError('参考透明材质需要明确的Principled表面：'+obj.name)
            shader.inputs['Alpha'].default_value = alpha
            mat.surface_render_method = 'DITHERED'
            obj.data.materials[index] = mat
        obj['reference_camera_alpha'] = alpha
        changed.append({'name': obj.name, 'alpha': alpha,
                        'collision_solid_retained': obj in props.solids})
    return {'policy': 'transparent_architecture', 'objects': changed,
            'structure_alpha': .04, 'panel_alpha': .12, 'collision_solids_retained': True}


def _reference_yaw(spec, actors, props):
    reference = spec.get('reference', 'world')
    if reference == 'world':
        return 0.
    target = spec.get('target')
    if reference == 'object':
        if props is None or target not in props.objects:
            raise ValueError('相机参照物不存在：'+str(target))
        root = props.objects[target]['root']
    elif reference == 'actor':
        if target not in actors:
            raise ValueError('相机参照人物不存在：'+str(target))
        root = _field(actors[target], 'root') or _field(actors[target], 'arm')
        if root is None:
            raise ValueError('相机参照人物缺少初始朝向：'+str(target))
    else:
        raise ValueError('未知相机参考系：'+str(reference))
    return root.matrix_world.to_euler().z


def setup_reference_camera(meshes, camera_spec, actors, props=None):
    """Create one fixed camera and preserve all explicit direction/FOV facts.

    ``actors`` maps IDs to Actor-like objects (or dicts) with meshes/root/arm.
    Return serializable bounds and requested/applied facts; the created camera
    is ``bpy.context.scene.camera``. A fixed position that cannot show the
    complete trajectory is rejected, never moved or aimed somewhere else.
    """
    scene = bpy.context.scene; saved_frame = scene.frame_current
    spec = validate_camera(copy.deepcopy(camera_spec))
    scene.frame_set(scene.frame_start); bpy.context.view_layer.update()
    reference_yaw = _reference_yaw(spec, actors, props)
    unique = list({obj.name: obj for obj in meshes}.values())
    if not unique:
        raise ValueError('相机没有可构图的人物或物体')
    points = []
    for frame in range(scene.frame_start, scene.frame_end+1):
        scene.frame_set(frame)
        points.extend(_corners(unique, bpy.context.evaluated_depsgraph_get()))
    if not points:
        raise ValueError('相机没有可见的构图对象')
    minimum = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    maximum = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    look_at = Vector(spec.get('look_at', list((minimum+maximum)*.5)))
    fixed_position = 'position' in spec
    azimuth = math.radians(spec.get('azimuth_deg', 0.)) + reference_yaw
    elevation = math.radians(spec.get('elevation_deg', 15.))
    direction = Vector((math.sin(azimuth)*math.cos(elevation),
                        -math.cos(azimuth)*math.cos(elevation), math.sin(elevation)))
    camera_data = bpy.data.cameras.new('Duel_FullCamera')
    camera = bpy.data.objects.new('Duel_FullCamera', camera_data)
    scene.collection.objects.link(camera); scene.camera = camera
    camera_data.type = 'ORTHO' if spec.get('projection', 'orthographic') == 'orthographic' else 'PERSP'
    camera_data.sensor_fit = 'HORIZONTAL'; camera_data.sensor_width = 36.
    fov = float(spec.get('fov_deg', 50.))
    camera_data.lens = 36./(2*math.tan(math.radians(fov)/2))
    camera_data.clip_start = .01
    camera.location = Vector(spec['position']) if fixed_position else look_at+direction*10.
    gaze = look_at-camera.location
    if gaze.length < 1e-6:
        raise ValueError('相机position与look_at不能重合')
    camera.rotation_euler = gaze.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()
    basis = camera.matrix_world.to_3x3(); right, up, backward = (basis.col[i] for i in range(3))
    offsets = [p-look_at for p in points]
    aspect = (scene.render.resolution_x*scene.render.pixel_aspect_x)/(
        scene.render.resolution_y*scene.render.pixel_aspect_y)
    usable = 1-2*FIT_MARGIN
    if camera_data.type == 'ORTHO':
        camera_data.ortho_scale = max(.1, 2*max(max(abs(right.dot(p)) for p in offsets),
                                                    aspect*max(abs(up.dot(p)) for p in offsets))/usable)
        if not fixed_position:
            camera.location = look_at+direction*max(7., max(backward.dot(p) for p in offsets)+3.)
    elif not fixed_position:
        tan_horizontal = math.tan(math.radians(fov)/2)
        distance = max(backward.dot(p)+max(abs(right.dot(p))/(tan_horizontal*usable),
                           abs(up.dot(p))*aspect/(tan_horizontal*usable)) for p in offsets)
        camera.location = look_at+direction*max(distance+.1, .2)
    if camera.location.z <= .01:
        raise ValueError('相机位于地面下或贴地，舞台地面会遮挡人物；请明确提高position或elevation，执行器不会偷偷抬高机位')
    camera_data.clip_end = max(100., max((p-camera.location).length for p in points)+10.)
    bpy.context.view_layer.update()
    if fixed_position:
        failures = [p for p in points if (lambda uv: uv.z < camera_data.clip_start or
                    min(uv.x, uv.y, 1-uv.x, 1-uv.y)<FRAME_MARGIN-1e-4)(
                    world_to_camera_view(scene, camera, p))]
        if failures:
            raise ValueError('固定相机position/look_at/fov无法完整显示人物和关注物体；请调整明确的位置、朝向或视场')
    presentation = (_transparent_architecture(props) if spec.get('occlusion_policy', 'transparent_architecture')
                    == 'transparent_architecture' else {'policy': 'strict', 'objects': [],
                                                        'collision_solids_retained': True})
    actual_offset = camera.location-look_at
    actual_azimuth = math.degrees(math.atan2(actual_offset.x, -actual_offset.y)-reference_yaw)
    actual_azimuth = (actual_azimuth+180)%360-180
    actual_elevation = math.degrees(math.atan2(actual_offset.z, actual_offset.xy.length))
    facts = {'min': list(minimum), 'max': list(maximum), 'requested': spec,
             'applied': {'reference': spec.get('reference', 'world'), 'target': spec.get('target'),
                         'reference_yaw_deg': math.degrees(reference_yaw),
                         'azimuth_deg': actual_azimuth,
                         'elevation_deg': actual_elevation,
                         'projection': spec.get('projection', 'orthographic'), 'fov_deg': fov,
                         'framing': spec['framing'], 'occlusion_policy': spec['occlusion_policy'],
                         'position': list(camera.location), 'look_at': list(look_at),
                         'ortho_scale': camera_data.ortho_scale if camera_data.type == 'ORTHO' else None,
                         'fixed_position': fixed_position, 'automatic_distance': not fixed_position,
                         'camera_name': camera.name}, 'reference_presentation': presentation}
    camera['reference_camera_facts_json'] = json.dumps(facts)
    scene['reference_camera_facts_json'] = json.dumps(facts)
    scene.frame_set(saved_frame)
    return facts


def _region_indices(mesh):
    names = {g.index: g.name.split(':')[-1] for g in mesh.vertex_groups}
    groups = {'head': {'Head', 'Neck'}, 'trunk': {'Hips', 'Spine', 'Spine1', 'Spine2'},
              'left_foot': {'LeftFoot', 'LeftToeBase'}, 'right_foot': {'RightFoot', 'RightToeBase'}}
    return {key: [v.index for v in mesh.data.vertices if any(
        names.get(g.group) in allowed and g.weight >= .25 for g in v.groups)] for key, allowed in groups.items()}


def _body_points(actor, deps, masks):
    all_points = []; regions = {key: [] for key in ('head', 'trunk', 'left_foot', 'right_foot')}
    for mesh in _meshes(actor):
        if mesh.hide_render:
            continue
        obj = mesh.evaluated_get(deps)
        local = np.empty((len(obj.data.vertices), 3), dtype=float)
        obj.data.vertices.foreach_get('co', local.ravel())
        matrix = np.asarray(obj.matrix_world, dtype=float)
        world = local@matrix[:3, :3].T+matrix[:3, 3]
        all_points.append(world)
        for key, indices in masks[mesh.name].items():
            usable = [i for i in indices if i < len(world)]
            if usable:
                regions[key].append(world[usable])
    if not all_points:
        return {}, []
    merged = np.concatenate(all_points); low, high = merged.min(axis=0), merged.max(axis=0)
    body = {}
    for key, arrays in regions.items():
        if arrays:
            values = np.concatenate(arrays); lo, hi = values.min(axis=0), values.max(axis=0)
        else:
            # Simple meshes used by plain renderers/tests may have no rig groups.
            lo, hi = low.copy(), high.copy(); height = high[2]-low[2]
            if key == 'head': lo[2], hi[2] = low[2]+height*.80, high[2]
            elif key == 'trunk': lo[2], hi[2] = low[2]+height*.35, low[2]+height*.75
            else: hi[2] = low[2]+height*.12
        center = (lo+hi)*.5
        # Five representatives avoid rejecting an entire body region for one
        # thin opaque handle; broad obstructions still block most samples.
        body[key] = [Vector(center), Vector(center+(hi-lo)*np.asarray((.22, 0, 0))),
                     Vector(center-(hi-lo)*np.asarray((.22, 0, 0))),
                     Vector(center+(hi-lo)*np.asarray((0, 0, .22))),
                     Vector(center-(hi-lo)*np.asarray((0, 0, .22)))]
    return body, [Vector(low), Vector(high)]


def _local_bvh(obj, deps):
    evaluated = obj.evaluated_get(deps)
    vertices = [v.co.copy() for v in evaluated.data.vertices]
    faces = [tuple(p.vertices) for p in evaluated.data.polygons]
    return BVHTree.FromPolygons(vertices, faces, all_triangles=False)


def _obstruction(point, camera, solids, trees):
    backward = camera.matrix_world.to_3x3().col[2]
    if camera.data.type == 'ORTHO':
        depth = (camera.location-point).dot(backward)
        origin = point+backward*depth
    else:
        origin = camera.location.copy()
    delta = point-origin
    if delta.length <= .001:
        return None
    nearest = None; nearest_distance = delta.length-.004
    for obj in solids:
        inverse = obj.matrix_world.inverted(); local_origin = inverse@origin
        local_end = inverse@point; local_delta = local_end-local_origin
        if local_delta.length <= .001:
            continue
        hit, _, _, _ = trees[obj.name].ray_cast(local_origin, local_delta.normalized(), local_delta.length)
        if hit is not None:
            distance = (obj.matrix_world@hit-origin).length
            if distance < nearest_distance:
                nearest, nearest_distance = obj.name, distance
    return nearest


def verify_reference_camera(meshes, camera_spec, actors, props=None, camera=None, frame_count=None):
    """Check every frame's actual full-body projection and opaque obstruction.

    Ray tests ignore actor skin and transparent architecture, but retain real
    opaque props. At least three of five samples hidden in any head/trunk/foot
    region is a clear visibility failure. This is sampled visibility, not an
    all-pixel proof or an assessment of performance naturalness.
    """
    scene = bpy.context.scene; camera = camera or scene.camera
    saved_frame = scene.frame_current
    end = frame_count or scene.frame_end
    actor_fail = []; focus_fail = []; occlusions = []; frames = []
    minimum_margin = 1.; total_occlusion_failures = 0
    masks = {mesh.name: _region_indices(mesh) for actor in actors.values() for mesh in _meshes(actor)}
    props_solids = list(props.solids) if props is not None else []
    # An orthographic low shot can put the foot's parallel ray origin under
    # the floor even when the camera object itself is above ground. Render
    # floors therefore participate in visibility without becoming physics
    # solids or transparent architectural panels.
    render_floors = [obj for obj in scene.objects if obj.type == 'MESH' and
                     obj.name.split('.')[0] in ('Stage_Floor', 'Room_Floor', 'Floor')]
    props_solids = list({obj.name: obj for obj in props_solids+render_floors}.values())
    scene.frame_set(scene.frame_start)
    deps = bpy.context.evaluated_depsgraph_get()
    trees = {obj.name: _local_bvh(obj, deps) for obj in props_solids if _opaque(obj)}
    for frame in range(scene.frame_start, end+1):
        scene.frame_set(frame); deps = bpy.context.evaluated_depsgraph_get()
        solids = [obj for obj in props_solids if _opaque(obj) and obj.name in trees]
        sample = {'frame': frame, 'actor_margins': {}, 'occluded_regions': []}
        for ident, actor in actors.items():
            corners = _corners(_meshes(actor), deps)
            uv = [world_to_camera_view(scene, camera, point) for point in corners]
            margin = min((min(p.x, p.y, 1-p.x, 1-p.y) for p in uv), default=-1.)
            minimum_margin = min(minimum_margin, margin); sample['actor_margins'][ident] = margin
            if margin < FRAME_MARGIN-1e-4 or any(p.z < camera.data.clip_start for p in uv):
                actor_fail.append({'frame': frame, 'actor': ident, 'margin': margin})
            regions, _ = _body_points(actor, deps, masks)
            for region, points in regions.items():
                blocked = [_obstruction(point, camera, solids, trees) for point in points]
                if sum(name is not None for name in blocked) >= 3:
                    total_occlusion_failures += 1
                    entry = {'frame': frame, 'actor': ident, 'region': region,
                             'occluded_samples': sum(name is not None for name in blocked),
                             'sample_count': len(points), 'opaque_parts': sorted({n for n in blocked if n})}
                    sample['occluded_regions'].append(entry)
                    if len(occlusions)<60: occlusions.append(entry)
        if props is not None:
            for ident, entry in props.objects.items():
                if not entry['spec'].get('focus'):
                    continue
                uv = [world_to_camera_view(scene, camera, p) for p in _corners(entry['meshes'], deps)]
                margin = min((min(p.x, p.y, 1-p.x, 1-p.y) for p in uv), default=1.)
                if margin < FRAME_MARGIN-1e-4 or any(p.z < camera.data.clip_start for p in uv):
                    focus_fail.append({'frame': frame, 'object': ident, 'margin': margin})
        frames.append(sample)
    scene.frame_set(saved_frame)
    result = {'passed': not actor_fail and not focus_fail and total_occlusion_failures == 0,
              'actor_frame_failures': actor_fail[:60], 'focus_frame_failures': focus_fail[:60],
              'occlusion_failures': occlusions, 'occlusion_failure_count': total_occlusion_failures,
              'minimum_body_margin': minimum_margin, 'tested_frames': len(frames), 'frames': frames,
              'scope': 'Every-frame evaluated full-body/required-prop projection and five head/trunk/each-foot representatives against renderable opaque physical props plus render-stage/room floors. Ignores actor skin and transparent architecture; not an all-pixel visibility or motion-naturalness certification.'}
    scene['reference_camera_quality_json'] = json.dumps(result)
    return result
