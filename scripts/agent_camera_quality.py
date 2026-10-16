"""Persistent camera intent and sampled visibility of evaluated scene meshes.

Projection checks use evaluated vertices. Occlusion checks sample surfaces;
they do not certify all rendered pixels, lighting, or action naturalness.
"""
import copy
import hashlib
import json
import math

STORE = 'wm_camera_visibility_v1'
REGIONS = ('head', 'trunk', 'left_arm', 'right_arm', 'left_hand', 'right_hand',
           'left_foot', 'right_foot')
DEFAULT_REGIONS = ('head', 'trunk', 'left_foot', 'right_foot')
SCOPE = ('Every applicable frame in the requested check interval and actual marker camera; evaluated subject vertices '
         'for projection and representative surface rays against render-enabled opaque '
         'scene meshes. Sampled visibility, not all pixels, physics or naturalness.')


def _actor_framing_sample(actor_id, camera, framing, regions, frame, uv, render_size):
    """Projected vertex bounds, not silhouette pixels or an image-size gate."""
    width = float(uv[:, 0].max()-uv[:, 0].min())
    height = float(uv[:, 1].max()-uv[:, 1].min())
    return {'actor_id': actor_id, 'camera': camera, 'framing': framing,
            'regions': list(regions) if framing == 'regions' else [],
            'sample_count': 1, 'frame_range': [frame, frame], 'render_size_pixels': list(render_size),
            'height_fraction_range': [height, height],
            'height_pixels_range': [height*render_size[1], height*render_size[1]],
            'width_fraction_range': [width, width],
            'projected_bbox_area_fraction_range': [width*height, width*height]}


def merge_actor_framing(target, entries):
    """Combine actual measurements while keeping cameras and regions distinct."""
    identity = ('actor_id', 'camera', 'framing', 'regions', 'render_size_pixels')
    ranges = ('height_fraction_range', 'height_pixels_range', 'width_fraction_range',
              'projected_bbox_area_fraction_range')
    for entry in entries:
        previous = next((item for item in target if all(item[key] == entry[key] for key in identity)), None)
        if previous is None:
            target.append(copy.deepcopy(entry))
            continue
        previous['sample_count'] += entry['sample_count']
        previous['frame_range'] = [min(previous['frame_range'][0], entry['frame_range'][0]),
                                   max(previous['frame_range'][1], entry['frame_range'][1])]
        for key in ranges:
            previous[key] = [min(previous[key][0], entry[key][0]), max(previous[key][1], entry[key][1])]
    return target


def actor_framing_feedback(entries):
    return ['%s在%s的投影高度为画面%.1f–%.1f%%（约%.0f–%.0f像素，%s）；'
            '主体入框不代表动作细节清楚，请结合原提示词判断主体大小。此尺寸不作为失败门槛，'
            '面积字段是投影包围框面积，不是人物轮廓像素面积。' % (
                item['actor_id'], item['camera'], *(100*v for v in item['height_fraction_range']),
                *item['height_pixels_range'], '所选身体区域' if item['framing'] == 'regions' else '全身')
            for item in entries]


def _finite_vector(value, size, name):
    if not isinstance(value, (list, tuple)) or len(value) != size or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError(name + ' requires finite coordinates')
    return [float(v) for v in value]


def normalize_requirements(requirements, job_id, first, last):
    """Validate a camera contract, without any action/person/duration whitelist."""
    if not isinstance(requirements, dict) or type(requirements.get('version')) is not int or requirements.get('version') != 1:
        raise ValueError('camera requirements version must be 1')
    if requirements.get('job_id', job_id) != job_id:
        raise ValueError('camera requirements belong to another job')
    result = copy.deepcopy(requirements)
    json.dumps(result, allow_nan=False)
    result.update(version=1, job_id=job_id)
    shots = result.get('shots')
    if not isinstance(shots, list) or not shots:
        raise ValueError('camera requirements need actual named shots')
    for shot in shots:
        if not isinstance(shot, dict) or not isinstance(shot.get('camera'), str) or not shot['camera']:
            raise ValueError('shot.camera must be an actual camera name')
        shot.setdefault('frame_start', first); shot.setdefault('frame_end', last)
        if (type(shot['frame_start']) is not int or type(shot['frame_end']) is not int
                or not first <= shot['frame_start'] <= shot['frame_end'] <= last):
            raise ValueError('shot frame interval must lie in the saved scene')
        for key in ('actor_ids', 'focus_objects'):
            shot.setdefault(key, [])
            if not isinstance(shot[key], list) or any(not isinstance(v, str) or not v for v in shot[key]):
                raise ValueError(key + ' must contain actual stable names')
            shot[key] = list(dict.fromkeys(shot[key]))
        groups=shot.get('focus_groups',{})
        if not isinstance(groups,dict) or any(not isinstance(k,str) or not k or not isinstance(v,list)
                or not v or any(not isinstance(n,str) or not n for n in v) for k,v in groups.items()):
            raise ValueError('focus_groups maps logical object IDs to actual mesh names')
        if 'focus_groups' in shot:
            shot['focus_groups']={k:list(dict.fromkeys(v)) for k,v in groups.items()}
        if not shot['actor_ids'] and not shot['focus_objects'] and not groups:
            raise ValueError('camera shot needs at least one actor or focus object')
        shot.setdefault('framing', 'full_body')
        if shot['framing'] not in ('full_body', 'regions'):
            raise ValueError('framing must be full_body or regions')
        shot.setdefault('regions', list(DEFAULT_REGIONS) if shot['framing'] == 'full_body' else [])
        if (not isinstance(shot['regions'], list) or any(v not in REGIONS for v in shot['regions'])
                or (shot['actor_ids'] and not shot['regions'])):
            raise ValueError('regions must name existing supported body regions')
        margin = shot.setdefault('margin', .04)
        if isinstance(margin, bool) or not isinstance(margin, (float, int)) or not 0 <= margin < .5:
            raise ValueError('margin must be finite and between 0 and .5')
        shot['margin'] = float(margin)
        shot.setdefault('fixed_transform', False)
        if type(shot['fixed_transform']) is not bool:
            raise ValueError('fixed_transform must be a bool')
        for key in ('expected_position', 'look_at'):
            if key in shot: shot[key] = _finite_vector(shot[key], 3, key)
        if any(key in shot for key in ('expected_azimuth_deg','expected_elevation_deg')) and 'look_at' not in shot:
            raise ValueError('expected camera angles require an explicit world look_at reference')
        for key in ('expected_azimuth_deg', 'expected_elevation_deg', 'reference_yaw_deg', 'expected_fov_deg',
                    'expected_lens', 'expected_ortho_scale'):
            if key in shot and (isinstance(shot[key], bool) or not isinstance(shot[key], (float, int))
                                or not math.isfinite(shot[key])):
                raise ValueError(key + ' must be finite')
        shot.setdefault('reference_yaw_deg', 0.)
        if 'expected_matrix_world' in shot:
            shot['expected_matrix_world'] = _finite_vector(shot['expected_matrix_world'], 16, 'expected_matrix_world')
        if 'expected_projection' in shot:
            projection = {'perspective': 'PERSP', 'orthographic': 'ORTHO', 'PERSP': 'PERSP', 'ORTHO': 'ORTHO'}.get(shot['expected_projection'])
            if projection is None: raise ValueError('expected_projection must be perspective or orthographic')
            shot['expected_projection'] = projection
    return result


def _scene(job_id):
    import bpy
    from agent_scene import validate_job
    validate_job(job_id)
    scene = bpy.context.scene
    owner = scene.get('wm_actor_job_id')
    if owner is not None and owner != job_id:
        raise ValueError('current actors belong to another job')
    return scene


def camera_requirements(job_id, requirements=None):
    import bpy
    scene = _scene(job_id)
    if requirements is None:
        raw = scene.get(STORE)
        if raw is None:
            return {'ok': False, 'job_id': job_id, 'status': 'missing', 'requirements': None}
        document = normalize_requirements(json.loads(raw), job_id, scene.frame_start, scene.frame_end)
        return {'ok': True, 'job_id': job_id, 'status': 'ready', 'requirements': document}
    document = normalize_requirements(requirements, job_id, scene.frame_start, scene.frame_end)
    saved = scene.frame_current, scene.frame_subframe, scene.camera
    try:
        for shot in document['shots']:
            camera = bpy.data.objects.get(shot['camera'])
            if camera is None or camera.type != 'CAMERA' or camera.name not in scene.objects:
                raise ValueError('shot camera is missing from current scene: ' + shot['camera'])
            focus_names=shot['focus_objects']+[n for names in shot.get('focus_groups',{}).values() for n in names]
            for name in dict.fromkeys(focus_names):
                obj = bpy.data.objects.get(name)
                if obj is None or obj.name not in scene.objects or obj.type != 'MESH':
                    raise ValueError('focus mesh is missing from current scene: ' + name)
            if shot['fixed_transform'] and any(key not in shot for key in
                    ('expected_matrix_world','expected_lens','expected_ortho_scale')):
                scene.frame_set(shot['frame_start']); bpy.context.view_layer.update()
                evaluated = camera.evaluated_get(bpy.context.evaluated_depsgraph_get())
                shot.setdefault('expected_matrix_world',[float(v) for row in evaluated.matrix_world for v in row])
                shot.setdefault('expected_lens',float(evaluated.data.lens))
                shot.setdefault('expected_ortho_scale',float(evaluated.data.ortho_scale))
        scene[STORE] = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    finally:
        scene.frame_set(saved[0], subframe=saved[1]); scene.camera = saved[2]
    return {'ok': True, 'job_id': job_id, 'status': 'registered', 'requirements': document}


def _active_camera(scene, frame, default):
    markers = [m for m in scene.timeline_markers if m.camera is not None and m.frame <= frame]
    return max(markers, key=lambda m: m.frame).camera if markers else default


def _enabled(scene):
    enabled = set()
    def visit(layer, allowed=True):
        allowed = allowed and not layer.exclude and not layer.collection.hide_render and not layer.holdout
        if allowed:
            enabled.update(obj.as_pointer() for obj in layer.collection.objects if not obj.hide_render)
        for child in layer.children: visit(child, allowed)
    for view in scene.view_layers:
        if view.use: visit(view.layer_collection)
    return enabled


def _material_opaque(mat, scene, obj):
    if mat is None: return True
    if scene.render.engine == 'BLENDER_WORKBENCH':
        shading=scene.display.shading
        alpha=float(obj.color[3] if shading.color_type=='OBJECT' else mat.diffuse_color[3])
        if shading.show_xray:alpha*=shading.xray_alpha
        return alpha >= .8
    if mat.use_nodes and mat.node_tree:
        output = next((n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
        links = list(output.inputs['Surface'].links) if output else []
        shader = links[0].from_node if links else None
        if shader is None or shader.type != 'BSDF_PRINCIPLED' or shader.inputs['Alpha'].is_linked: return True
        return float(shader.inputs['Alpha'].default_value) >= .8
    return float(mat.diffuse_color[3]) >= .8


def _mesh_record(obj, deps, cache, scene):
    import numpy as np
    from mathutils.bvhtree import BVHTree
    evaluated = obj.evaluated_get(deps); mesh = evaluated.to_mesh()
    try:
        co = np.empty(len(mesh.vertices) * 3, dtype=np.float32); mesh.vertices.foreach_get('co', co)
        starts = np.empty(len(mesh.polygons), dtype=np.int32); mesh.polygons.foreach_get('loop_start', starts)
        totals = np.empty(len(mesh.polygons), dtype=np.int32); mesh.polygons.foreach_get('loop_total', totals)
        loops = np.empty(len(mesh.loops), dtype=np.int32); mesh.loops.foreach_get('vertex_index', loops)
        materials = np.empty(len(mesh.polygons), dtype=np.int32); mesh.polygons.foreach_get('material_index', materials)
        # Render-only vertex-group masks (including original-skin POV masks)
        # are absent from the viewport depsgraph. Apply their face selection
        # without changing any live modifier or the source topology.
        masks=[m for m in obj.modifiers if m.type=='MASK' and m.mode=='VERTEX_GROUP'
               and m.show_render and not m.show_viewport]
        keep=np.ones(len(mesh.vertices),dtype=bool)
        if masks:
            if len(mesh.vertices)!=len(obj.data.vertices):
                raise ValueError('Cannot map render-only mask after topology changes: '+obj.name)
            for modifier in masks:
                group=obj.vertex_groups.get(modifier.vertex_group)
                if group is None:continue
                weights=np.asarray([sum(g.weight for g in v.groups if g.group==group.index)
                                    for v in obj.data.vertices])
                selected=weights>max(1e-8,float(modifier.threshold))
                keep &= ~selected if modifier.invert_vertex_group else selected
        topology_signature=hashlib.sha256(starts.tobytes()+totals.tobytes()+loops.tobytes()+keep.tobytes()).digest()
        signature = hashlib.sha256(co.tobytes()+topology_signature).digest()
        previous = cache.get(obj.name)
        if previous is None or previous['topology_signature'] != topology_signature:
            face_indices=[i for i,(s,n) in enumerate(zip(starts,totals)) if all(keep[loops[s:s+n]])]
            faces = [tuple(int(v) for v in loops[starts[i]:starts[i]+totals[i]]) for i in face_indices]
            previous={'topology_signature':topology_signature,'faces':faces,'face_indices':face_indices,'signature':None}
            cache[obj.name]=previous
        if previous['signature'] != signature:
            # The world tree handles opaque targets as well as obstacles.
            # Local target trees are built lazily only for transparent cases.
            previous['tree']=None
            previous['signature']=signature
        matrix = np.asarray(evaluated.matrix_world, dtype=np.float64)
        world = np.einsum('ij,nj->ni',matrix[:3,:3],co.reshape((-1,3)).astype(np.float64),optimize=False) + matrix[:3,3]
        opaque = [_material_opaque(mat, scene, obj) for mat in evaluated.data.materials]
        return {'name': obj.name, 'object': obj, 'world': world, 'matrix': evaluated.matrix_world.copy(),
                'inverse': evaluated.matrix_world.inverted(), 'faces': previous['faces'], 'tree': previous['tree'],
                'kept_vertices':keep,'geometry_signature':signature,'topology_signature':topology_signature,
                'local_co':co.reshape((-1,3)),'cache_record':previous,
                'face_opaque': [opaque[int(materials[i])] if int(materials[i]) < len(opaque) else True
                                for i in previous['face_indices']]}
    finally: evaluated.to_mesh_clear()


def _ray_hit(record, origin, end, opaque_only=False):
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    if not record['faces']:return None
    if record['tree'] is None:
        record['tree']=record['cache_record'].get('tree')
        if record['tree'] is None:
            record['tree']=BVHTree.FromPolygons(record['local_co'].tolist(),record['faces'])
            record['cache_record']['tree']=record['tree']
    local_origin = record['inverse'] @ origin; local_end = record['inverse'] @ end
    delta = local_end - local_origin
    if delta.length <= 1e-8: return None
    direction = delta.normalized(); remaining = delta.length
    for _ in range(len(record['faces']) + 1):
        hit, _, index, _ = record['tree'].ray_cast(local_origin, direction, remaining)
        if hit is None: return None
        world_hit = record['matrix'] @ hit
        if not opaque_only or record['face_opaque'][index]: return world_hit
        advance = (hit-local_origin).length + 1e-5
        remaining -= advance
        if remaining <= 0: return None
        local_origin = hit + direction * 1e-5
    return None


def _origin(camera, point):
    if camera.data.type != 'ORTHO': return camera.matrix_world.translation.copy()
    backward = camera.matrix_world.to_3x3().col[2].normalized()
    return point + backward * (camera.matrix_world.translation-point).dot(backward)


def _opaque_world_tree(records, cache):
    """One world ray query replaces repeated transforms/rays per obstacle."""
    import numpy as np
    from mathutils.bvhtree import BVHTree
    ordered=list(records.values());signature=hashlib.sha256();topology=hashlib.sha256()
    for r in ordered:
        signature.update(r['name'].encode());signature.update(r['geometry_signature']);signature.update(np.asarray(r['matrix'],dtype=np.float64).tobytes())
        signature.update(bytes(r['face_opaque']));topology.update(r['name'].encode());topology.update(r['topology_signature'])
        topology.update(bytes(r['face_opaque']))
    geometry_key=signature.digest();topology_key=topology.digest()
    if cache.get('geometry_signature')==geometry_key:return cache
    if cache.get('topology_signature')!=topology_key:
        faces=[];owners=[];offset=0
        for r in ordered:
            for face,opaque in zip(r['faces'],r['face_opaque']):
                if opaque:
                    faces.append(tuple(index+offset for index in face));owners.append(r['name'])
            offset+=len(r['world'])
        cache.update(topology_signature=topology_key,faces=faces,owners=owners)
    vertices=np.concatenate([r['world'] for r in ordered]) if ordered else np.empty((0,3))
    cache['tree']=BVHTree.FromPolygons(vertices.tolist(),cache['faces']) if cache['faces'] else None
    cache['geometry_signature']=geometry_key
    return cache


def _blocker(point, camera, target_records, records, aggregate):
    from mathutils import Vector
    origin = _origin(camera, Vector(point)); delta = Vector(point)-origin
    if delta.length < 1e-8: return None
    end = Vector(point) + delta.normalized() * .01
    if aggregate['tree'] is None:return None
    hit,_,face_index,distance=aggregate['tree'].ray_cast(origin,delta.normalized(),delta.length+.01)
    if hit is None:return None
    owner=aggregate['owners'][face_index]
    if owner in {r['name'] for r in target_records}:return None
    if all(all(r['face_opaque']) for r in target_records):
        # The nearest opaque scene hit already includes every opaque target
        # face. Only a hit genuinely before the target can be an obstacle.
        return owner if distance < delta.length-.001 else None
    own_hits = [_ray_hit(r, origin, end) for r in target_records]
    own_hits = [p for p in own_hits if p is not None]
    # Surface truncation prevents a door center behind its own front face or
    # tiny skin-floor contact from being misclassified as a wall/floor block.
    limit = min(((p-origin).length for p in own_hits), default=delta.length) - .001
    return owner if distance < limit else None


def _region_indices(obj, region):
    names = {g.index:g.name.split(':')[-1] for g in obj.vertex_groups}
    def matches(name):
        if region == 'head': return name == 'Head'
        if region == 'trunk': return name in ('Hips','Spine','Spine1','Spine2')
        side = 'Left' if region.startswith('left_') else 'Right'; part = region.split('_',1)[1]
        if part == 'arm': return name in (side+'Arm',side+'ForeArm')
        if part == 'hand': return name.startswith(side+'Hand')
        return name in (side+'Foot', side+'ToeBase')
    return [v.index for v in obj.data.vertices if sum(g.weight for g in v.groups if matches(names.get(g.group,''))) > .3]


def _representatives(points, count=9):
    import numpy as np
    if len(points) <= count: return points
    # Actual vertices near several extrema; no invented center behind a wall.
    mean = points.mean(axis=0); selected = {int(np.argmin(((points-mean)**2).sum(axis=1)))}
    for axis in range(3):
        selected.add(int(points[:,axis].argmin())); selected.add(int(points[:,axis].argmax()))
    selected.update(int(i) for i in np.linspace(0,len(points)-1,count,dtype=int))
    return points[sorted(selected)]


def _surface_representatives(records, camera, count=15):
    """Area-weighted front-face interior samples, never box-edge corners."""
    import numpy as np
    from mathutils import Vector
    candidates=[];fallback=[]
    backward=camera.matrix_world.to_3x3().col[2].normalized()
    for record in records:
        for face in record['faces']:
            if len(face)<3:continue
            vertices=record['world'][list(face)];center=vertices.mean(axis=0)
            area_vector=np.cross(vertices[1]-vertices[0],vertices[2]-vertices[0])
            for index in range(2,len(vertices)-1):
                area_vector+=np.cross(vertices[index]-vertices[0],vertices[index+1]-vertices[0])
            view=backward if camera.data.type=='ORTHO' else (camera.matrix_world.translation-Vector(center)).normalized()
            projected=float(np.dot(area_vector,np.asarray(view)))*.5
            if projected>1e-10:candidates.append((projected,center,vertices))
            fallback.append((float(np.linalg.norm(area_vector))*.5,center,vertices))
    if not candidates:candidates=[item for item in fallback if item[0]>1e-10]
    if not candidates:return np.empty((0,3))
    weights=np.cumsum([item[0] for item in candidates]);samples=[];repeats={}
    for value in np.linspace(weights[-1]/(2*count),weights[-1]*(1-1/(2*count)),count):
        index=min(int(np.searchsorted(weights,value)),len(candidates)-1)
        _,center,vertices=candidates[index];repeat=repeats.get(index,0);repeats[index]=repeat+1
        # Keep repeated samples inside the face, away from BVH edge precision.
        samples.append(center if repeat==0 else center*.75+vertices[(repeat-1)%len(vertices)]*.25)
    return np.asarray(samples)


def _camera_constraints(camera, shot):
    from mathutils import Vector
    problems=[];matrix=[float(v) for row in camera.matrix_world for v in row]
    if shot['fixed_transform']:
        missing=[key for key in ('expected_matrix_world','expected_lens','expected_ortho_scale') if key not in shot]
        if missing:problems.append({'code':'camera_fixed_baseline_missing','fields':missing})
        if 'expected_matrix_world' not in shot:
            pass
        elif max(abs(a-b) for a,b in zip(matrix,shot['expected_matrix_world']))>.0001:
            problems.append({'code':'camera_fixed_transform_changed'})
        for key,actual in (('expected_lens',camera.data.lens),('expected_ortho_scale',camera.data.ortho_scale)):
            if key in shot and abs(actual-shot[key])>.0001:problems.append({'code':'camera_fixed_optics_changed','field':key})
    position=camera.matrix_world.translation
    if 'expected_position' in shot and (position-Vector(shot['expected_position'])).length>.001:
        problems.append({'code':'camera_expected_position','actual':list(position),'expected':shot['expected_position']})
    if 'expected_projection' in shot and camera.data.type != shot['expected_projection']:
        problems.append({'code':'camera_expected_projection','actual':camera.data.type,'expected':shot['expected_projection']})
    if 'expected_fov_deg' in shot and abs(math.degrees(camera.data.angle_x)-shot['expected_fov_deg'])>.1:
        problems.append({'code':'camera_expected_fov','actual':math.degrees(camera.data.angle_x),'expected':shot['expected_fov_deg']})
    if 'look_at' in shot:
        offset=position-Vector(shot['look_at'])
        if offset.length < 1e-6:problems.append({'code':'camera_look_at_coincident'})
        else:
            gaze=-camera.matrix_world.to_3x3().col[2].normalized()
            if math.degrees(gaze.angle(-offset))>.5:problems.append({'code':'camera_expected_gaze'})
            azimuth=math.degrees(math.atan2(offset.x,-offset.y))-shot['reference_yaw_deg']
            elevation=math.degrees(math.atan2(offset.z,math.hypot(offset.x,offset.y)))
            for key,actual in (('expected_azimuth_deg',azimuth),('expected_elevation_deg',elevation)):
                if key in shot and abs((actual-shot[key]+180)%360-180)>.1:
                    problems.append({'code':key.replace('expected_','camera_expected_'),'actual':actual,'expected':shot[key]})
    return problems


def check_camera_visibility(job_id, requirements=None, frame_start=None, frame_end=None, detail=False):
    import bpy
    import numpy as np
    from mathutils import Vector
    from agent_character import resolve_actor
    from agent_motion_quality import camera_projection_context, project_world_vertices
    scene=_scene(job_id);saved=scene.frame_current,scene.frame_subframe,scene.camera
    result={'ok':False,'passed':False,'status':'failed','job_id':job_id,'tested_frames':0,
            'failure_count':0,'failures':[],'requirements':None,'scope':SCOPE,
            'actor_framing': [], 'framing_feedback': [],
            'framing_scope': 'Projected bounds of checked full-body or named-region vertices on the camera canvas, before render-border cropping; not visible silhouette area.'}
    def fail(code,**fields):
        result['failure_count']+=1
        if len(result['failures'])<60:result['failures'].append({'code':code,**fields})
    try:
        contract=camera_requirements(job_id,requirements)
        result['requirements']=contract['requirements']
        if contract['status']=='missing':
            fail('camera_requirements_missing');result['status']='missing';return result
        shots=contract['requirements']['shots'];actors={};indices={};cache={};world_cache={};frames=[]
        for shot in shots:
            for aid in shot['actor_ids']:
                if aid not in actors:actors[aid]=resolve_actor(job_id,aid)
        first=scene.frame_start if frame_start is None else frame_start;last=scene.frame_end if frame_end is None else frame_end
        if type(first) is not int or type(last) is not int or not scene.frame_start<=first<=last<=scene.frame_end:
            raise ValueError('check frame interval must lie in saved scene')
        result['checked_frame_range']=[first,last]
        result['frame_sampling']='every_frame_in_requested_interval'
        render_size=[int(value*scene.render.resolution_percentage/100)
                     for value in (scene.render.resolution_x,scene.render.resolution_y)]
        for frame in range(first,last+1):
            scene.frame_set(frame);bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
            camera_obj=_active_camera(scene,frame,saved[2]);result['tested_frames']+=1
            if camera_obj is None or camera_obj.type!='CAMERA':fail('camera_missing',frame=frame);continue
            camera=camera_obj.evaluated_get(deps)
            applicable=[s for s in shots if s['camera']==camera_obj.name and s['frame_start']<=frame<=s['frame_end']]
            if not applicable:fail('camera_requirements_uncovered',frame=frame,camera=camera_obj.name);continue
            enabled=_enabled(scene);records={}
            for obj in scene.objects:
                if obj.type=='MESH' and obj.as_pointer() in enabled:
                    records[obj.name]=_mesh_record(obj,deps,cache,scene)
            aggregate=_opaque_world_tree(records,world_cache)
            projection=camera_projection_context(scene,camera);frame_detail={'frame':frame,'camera':camera_obj.name,'subjects':[]}
            for shot_index,shot in enumerate(applicable):
                for problem in _camera_constraints(camera,shot):
                    fail(problem.pop('code'),frame=frame,camera=camera_obj.name,**problem)
                subjects=[('actor',aid,[m.name for m in actors[aid]['meshes']]) for aid in shot['actor_ids']]
                subjects += [('object',name,[name]) for name in shot['focus_objects']]
                subjects += [('object',ident,names) for ident,names in shot.get('focus_groups',{}).items()]
                for kind,ident,names in subjects:
                    target=[records[n] for n in names if n in records and len(records[n]['world'])]
                    if len(target)!=len(names):
                        fail('subject_not_render_enabled',frame=frame,camera=camera_obj.name,kind=kind,subject=ident,missing=[n for n in names if n not in records]);continue
                    if kind=='actor' and shot['framing']=='regions':
                        all_points=[]
                        for region in shot['regions']:
                            for r in target:
                                key=(r['name'],region)
                                if key not in indices:indices[key]=_region_indices(r['object'],region)
                                ids=[i for i in indices[key] if i<len(r['world']) and r['kept_vertices'][i]]
                                if ids:all_points.append(r['world'][ids])
                        points=np.concatenate(all_points) if all_points else np.empty((0,3))
                    else:points=np.concatenate([r['world'][r['kept_vertices']] for r in target])
                    if not len(points):fail('subject_region_missing',frame=frame,kind=kind,subject=ident);continue
                    uv=project_world_vertices(points,projection);margin=float(np.minimum.reduce([uv[:,0],uv[:,1],1-uv[:,0],1-uv[:,1]]).min())
                    if kind=='actor' and np.isfinite(uv).all() and np.all(uv[:,2]>0):
                        merge_actor_framing(result['actor_framing'], [_actor_framing_sample(
                            ident,camera_obj.name,shot['framing'],shot['regions'],frame,uv,render_size)])
                    if not np.isfinite(uv).all() or margin<shot['margin']-1e-4 or np.any(uv[:,2]<camera.data.clip_start) or np.any(uv[:,2]>camera.data.clip_end):
                        fail('subject_out_of_frame',frame=frame,camera=camera_obj.name,kind=kind,subject=ident,margin=margin)
                    regions=shot['regions'] if kind=='actor' else ['surface']
                    for region in regions:
                        parts=[]
                        for r in target:
                            if kind=='object':parts.append(r['world'][r['kept_vertices']]);continue
                            key=(r['name'],region)
                            if key not in indices:indices[key]=_region_indices(r['object'],region)
                            ids=[i for i in indices[key] if i<len(r['world']) and r['kept_vertices'][i]]
                            if ids:parts.append(r['world'][ids])
                        if not parts:fail('subject_region_missing',frame=frame,kind=kind,subject=ident,region=region);continue
                        representatives=(_surface_representatives(target,camera) if kind=='object'
                                         else _representatives(np.concatenate(parts)))
                        if not len(representatives):
                            fail('subject_surface_missing',frame=frame,kind=kind,subject=ident);continue
                        blocked=[_blocker(p,camera,target,records,aggregate) for p in representatives]
                        hidden=sum(v is not None for v in blocked)
                        if hidden>=math.ceil(len(blocked)*.6):
                            fail('subject_occluded',frame=frame,camera=camera_obj.name,kind=kind,subject=ident,region=region,
                                 blocked_samples=hidden,sample_count=len(blocked),blockers=sorted({v for v in blocked if v}))
                        if detail:frame_detail['subjects'].append({'kind':kind,'subject':ident,'region':region,'margin':margin,'blocked_samples':hidden,'sample_count':len(blocked)})
            if detail:frames.append(frame_detail)
        result.update(ok=result['failure_count']==0,passed=result['failure_count']==0,status='passed' if not result['failure_count'] else 'failed')
        if detail:result['frames']=frames
    except Exception as error:
        fail('camera_visibility_error',type=type(error).__name__,message=str(error))
    finally:
        scene.frame_set(saved[0],subframe=saved[1]);scene.camera=saved[2]
        result['framing_feedback']=actor_framing_feedback(result['actor_framing'])
    return result
