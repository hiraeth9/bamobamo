"""Measured identity, animation, boundary, contact and frame checks.

Reports have a bounded scope.  Neither finite transforms nor decoded media
certify semantics, naturalness, contact dynamics, or an arbitrary fight.
"""
from __future__ import annotations
import json
import math
import hashlib


def registered_contact_report(job_id, frame_start=None, frame_end=None, actor_id=None,
                              detail=False):
    """Check registered geometry and turn runtime errors into useful feedback."""
    try:
        from agent_contacts import check_contacts
        result = check_contacts(job_id, frame_start=frame_start, frame_end=frame_end,
                                actor_id=actor_id, detail=detail)
    except Exception as error:
        result = {'ok': False, 'passed': False, 'version': 1, 'status': 'error', 'failure_count': 1,
                  'failures': [{'code': 'contact_check_error', 'type': type(error).__name__,
                                'message': str(error)}], 'contacts': []}
    result['coverage'] = 'registered_geometry_only; natural_language_coverage_unverified'
    return result


def rotation_delta_degrees(first, second):
    """Physical rotation distance; q and -q represent the same orientation."""
    a=math.sqrt(sum(v*v for v in first));b=math.sqrt(sum(v*v for v in second))
    if a<1e-12 or b<1e-12:raise ValueError('zero quaternion in rotation measurement')
    dot=abs(sum(x*y for x,y in zip(first,second))/(a*b))
    return math.degrees(2*math.acos(min(1.0,max(0.0,dot))))


def _active_channelbags(animation_data):
    if not animation_data or not animation_data.action or not animation_data.action_slot:
        return []
    bags=[]
    for layer in animation_data.action.layers:
        for strip in layer.strips:
            bag=strip.channelbag(animation_data.action_slot)
            if bag is not None:bags.append(bag)
    return bags


def animation_fingerprint(obj):
    """Actual keyed values, independent of object/action names and save time."""
    ad=obj.animation_data
    if not ad or not ad.action:return None
    curves=[]
    for bag in _active_channelbags(ad):
        for curve in bag.fcurves:
            curves.append({'path':curve.data_path,'index':curve.array_index,
                           'keys':[[round(float(p.co.x),7),round(float(p.co.y),7),p.interpolation]
                                   for p in curve.keyframe_points]})
    curves.sort(key=lambda c:(c['path'],c['index']))
    return hashlib.sha256(json.dumps(curves,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _action_facts(obj):
    ad=obj.animation_data
    action=ad.action if ad else None
    curves=[]
    for bag in _active_channelbags(ad):
        for curve in bag.fcurves:
            curves.append({'data_path':curve.data_path,'array_index':curve.array_index,
                           'keyframe_count':len(curve.keyframe_points),
                           'first_frame':float(curve.keyframe_points[0].co.x) if curve.keyframe_points else None,
                           'last_frame':float(curve.keyframe_points[-1].co.x) if curve.keyframe_points else None})
    pose_curves=[c for c in curves if c['data_path'].startswith('pose.bones[')]
    return {'object':obj.name,'action':action.name if action else None,
            'action_slot':ad.action_slot.identifier if action and ad.action_slot else None,
            'frame_range':list(action.frame_range) if action else None,
            'curve_count':len(curves),'pose_curve_count':len(pose_curves),
            'keyframe_count':sum(c['keyframe_count'] for c in curves),
            'channels':curves,'nla_track_count':len(ad.nla_tracks) if ad else 0,
            'channel_values_sha256':animation_fingerprint(obj)}


def scene_motion_facts(job_id):
    """Stable JSON from persisted records + actual actions, with no timestamp."""
    from agent_character import scene_actor_facts,resolve_actor
    from agent_motion_timeline import STORE
    actor_facts=scene_actor_facts(job_id)
    actors=[]; errors=[]; frames=set()
    for info in actor_facts['actors']:
        actor_id=info.get('actor_id')
        if not actor_id: continue
        try:
            actor=resolve_actor(job_id,actor_id)
            store=json.loads(actor['root'].get(STORE,'{"version":1,"events":[]}'))
            for event in store['events']:
                frames.update((event['start_frame'],event['end_frame']))
                frames.update(int(key['frame']) for key in event.get('keyframes',[]))
                for spec in event.get('targets',{}).values():
                    frames.update(int(key['frame']) for key in spec.get('keyframes',[]))
            pose=_action_facts(actor['armature']);root=_action_facts(actor['root'])
            commit=store.get('last_commit')
            if commit and commit.get('status')=='internal_base_only':
                errors.append({'actor_id':actor_id,
                               'error':'coordinated motion is only an internal base pass; finish the contact commit before submission'})
            if commit and (pose['action']!=commit.get('armature_action') or root['action']!=commit.get('root_action')):
                errors.append({'actor_id':actor_id,'error':'actual actions differ from committed motion'})
            if commit and (pose['channel_values_sha256']!=commit.get('pose_channel_sha256') or
                           root['channel_values_sha256']!=commit.get('root_channel_sha256')):
                errors.append({'actor_id':actor_id,'error':'actual keyed channel values differ from committed motion evidence'})
            if commit and (not pose['curve_count'] or not root['curve_count']):
                errors.append({'actor_id':actor_id,'error':'committed motion has no actual persistent pose/root channels'})
            actors.append({'actor_id':actor_id,'timeline':store,'pose_animation':pose,
                           'root_animation':root,'has_actual_pose_animation':bool(pose['pose_curve_count']),
                           'has_actual_root_animation':bool(root['curve_count'])})
        except Exception as exc:
            errors.append({'actor_id':actor_id,'error':str(exc)})
    return {'version':1,'job_id':job_id,'actors':actors,'errors':errors,
            'event_sample_frames':sorted(frames),'scope':'persisted motion record and actual animation channel evidence',
            'naturalness_verified':False}


def _mesh_bounds(meshes,depsgraph):
    from mathutils import Vector
    low=Vector((float('inf'),)*3);high=Vector((float('-inf'),)*3)
    count=0
    for obj in meshes:
        evaluated=obj.evaluated_get(depsgraph)
        geometry=evaluated.to_mesh()
        try:
            for vertex in geometry.vertices:
                p=evaluated.matrix_world @ vertex.co
                if not all(math.isfinite(v) for v in p):raise ValueError('nonfinite deformed vertex')
                for i in range(3):low[i]=min(low[i],p[i]);high[i]=max(high[i],p[i])
                count+=1
        finally:evaluated.to_mesh_clear()
    return {'min':list(low),'max':list(high),'vertex_count':count}


def camera_projection_context(scene,camera):
    """Once per evaluated frame, preserving Blender's normalized camera axes."""
    import numpy as np
    frame=camera.data.view_frame(scene=scene)
    return {'inverse':np.asarray(camera.matrix_world.normalized().inverted(),dtype=np.float64),
            'frame':np.asarray([list(v) for v in frame[:3]],dtype=np.float64),
            'orthographic':camera.data.type=='ORTHO'}


def project_world_vertices(world,context):
    """All vertices; no sampling, bounding-box shortcut, or frame skipping."""
    import numpy as np
    inverse=context['inverse'];frame=context['frame']
    # Tiny 3-column transforms use a single C loop; avoid BLAS thread-pool
    # startup/oversubscription on a CPU-only Blender process.
    local=np.einsum('ij,kj->ik',world,inverse[:3,:3],optimize=False)+inverse[:3,3]
    depth=-local[:,2]
    if context['orthographic']:
        min_x,max_x=frame[2,0],frame[1,0]
        min_y,max_y=frame[1,1],frame[0,1]
    else:
        min_x,max_x=-frame[2,0]/frame[2,2]*depth,-frame[1,0]/frame[1,2]*depth
        min_y,max_y=-frame[1,1]/frame[1,2]*depth,-frame[0,1]/frame[0,2]*depth
    with np.errstate(divide='ignore',invalid='ignore'):
        x=(local[:,0]-min_x)/(max_x-min_x)
        y=(local[:,1]-min_y)/(max_y-min_y)
    if not context['orthographic']:
        x=np.where(depth==0,.5,x);y=np.where(depth==0,.5,y)
    return np.column_stack((x,y,depth))


def evaluated_geometry_facts(meshes,depsgraph,scene=None,camera=None,projection=None):
    """One to_mesh and one world transform per mesh/frame for bounds+framing.

    Float64 bulk arithmetic improves accumulated precision. At the camera/depth
    decision boundaries, use Blender's original scalar projection to preserve
    its float32 classification exactly rather than introducing an edge tolerance.
    """
    import numpy as np
    from bpy_extras.object_utils import world_to_camera_view
    low=np.full(3,np.inf);high=np.full(3,-np.inf);count=0;outside=0
    if camera is not None and projection is None:projection=camera_projection_context(scene,camera)
    for obj in meshes:
        evaluated=obj.evaluated_get(depsgraph);geometry=evaluated.to_mesh()
        try:
            size=len(geometry.vertices)
            if not size:continue
            coordinates=np.empty(size*3,dtype=np.float32)
            geometry.vertices.foreach_get('co',coordinates)
            coordinates=coordinates.reshape((-1,3)).astype(np.float64)
            matrix=np.asarray(evaluated.matrix_world,dtype=np.float64)
            world=np.einsum('ij,kj->ik',coordinates,matrix[:3,:3],optimize=False)+matrix[:3,3]
            if not np.isfinite(world).all():raise ValueError('nonfinite deformed vertex')
            low=np.minimum(low,world.min(axis=0));high=np.maximum(high,world.max(axis=0));count+=size
            if camera is not None:
                points=project_world_vertices(world,projection)
                # Account for scalar float32 rounding only on points that could
                # change the old exact 0..1/positive-depth decision.
                edge=np.any(np.minimum(np.abs(points[:,:2]),np.abs(points[:,:2]-1))<1e-5,axis=1)|(np.abs(points[:,2])<1e-5)
                for index in np.flatnonzero(edge):
                    points[index]=world_to_camera_view(scene,camera,evaluated.matrix_world @ geometry.vertices[int(index)].co)
                outside+=int(np.count_nonzero((points[:,2]<=0)|(points[:,0]<0)|(points[:,0]>1)|(points[:,1]<0)|(points[:,1]>1)))
        finally:evaluated.to_mesh_clear()
    return {'bounds':{'min':low.tolist() if count else None,'max':high.tolist() if count else None,'vertex_count':count},
            'outside_vertex_count':outside}


def motion_check(job_id,actor_id=None,frame_start=None,frame_end=None,contacts=None,
                 check_camera=False,max_root_step_m=None,max_boundary_angle_deg=None):
    """Contacts explicitly select frames and real bone/target geometry.

    {actor_id,bone,frame(s),target_object,target_point/point,tolerance_m} measures
    bone-head to an explicit world point or the evaluated mesh surface.  This is
    a geometric proximity check; finger/palm surface and force are separate QA.
    Optional thresholds are task-specific, never global prompt restrictions.
    """
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from bpy_extras.object_utils import world_to_camera_view
    from agent_character import actor_info,scene_actor_facts,resolve_actor
    from agent_motion_timeline import STORE
    scene=bpy.context.scene
    start=scene.frame_start if frame_start is None else int(frame_start)
    end=scene.frame_end if frame_end is None else int(frame_end)
    if end<start:raise ValueError('inverted quality frame range')
    identities=scene_actor_facts(job_id)
    infos=[item for item in identities['actors'] if item['actor_id']==actor_id] if actor_id else identities['actors']
    if actor_id and not infos:raise ValueError('character actor not registered: '+actor_id)
    old_frame=scene.frame_current
    reports=[];failures=[{'check':'character_identity','error':error} for error in identities.get('errors',[])];contact_reports=[]
    try:
        for info in infos:
            aid=info['actor_id'];actor=resolve_actor(job_id,aid)
            store=json.loads(actor['root'].get(STORE,'{"events":[]}'))
            boundaries={e['start_frame'] for e in store['events']}|{e['end_frame'] for e in store['events']}
            snapshots=[];root_previous=None;pose_previous=None;max_root_step=0;max_angle=0
            out_of_frame=[];boundary_reports=[];visible_mesh_verified=True
            for frame in range(start,end+1):
                scene.frame_set(frame)
                depsgraph=bpy.context.evaluated_depsgraph_get()
                arm=actor['armature'].evaluated_get(depsgraph)
                root=actor['root'].evaluated_get(depsgraph)
                root_position=root.matrix_world.translation.copy()
                current_pose={b.name:b.matrix.to_quaternion() for b in arm.pose.bones}
                if not all(math.isfinite(v) for bone in arm.pose.bones for row in bone.matrix for v in row):
                    failures.append({'actor_id':aid,'frame':frame,'check':'finite_pose'})
                step=(root_position-root_previous).length if root_previous is not None else 0
                angles={name:rotation_delta_degrees(old,current_pose[name])
                        for name,old in (pose_previous or {}).items()}
                angle=max(angles.values(),default=0)
                max_root_step=max(max_root_step,step);max_angle=max(max_angle,angle)
                if frame in boundaries or frame-1 in boundaries:
                    boundary_reports.append({'frame':frame,'root_step_m':step,'max_bone_angle_delta_deg':angle})
                    if max_boundary_angle_deg is not None and angle>max_boundary_angle_deg:
                        failures.append({'actor_id':aid,'frame':frame,'check':'boundary_angle','measured_deg':angle,'threshold_deg':max_boundary_angle_deg})
                if max_root_step_m is not None and step>max_root_step_m:
                    failures.append({'actor_id':aid,'frame':frame,'check':'root_step','measured_m':step,'threshold_m':max_root_step_m})
                if check_camera and not scene.camera:raise ValueError('camera required for framing check')
                measured=evaluated_geometry_facts(actor['meshes'],depsgraph,scene,scene.camera if check_camera else None)
                bound=measured['bounds']
                if not bound['vertex_count']:visible_mesh_verified=False
                if check_camera:
                    if measured['outside_vertex_count']:out_of_frame.append({'frame':frame,'outside_vertex_count':measured['outside_vertex_count']})
                if frame in (start,(start+end)//2,end):
                    snapshots.append({'frame':frame,'root':list(root_position),'deformed_bounds':bound,
                                      'bone_heads':{b.name:list(arm.matrix_world @ b.head) for b in arm.pose.bones}})
                root_previous=root_position;pose_previous=current_pose
            if out_of_frame:failures.append({'actor_id':aid,'check':'full_body_framing','failed_frames':len(out_of_frame)})
            reports.append({'actor_id':aid,'character_id':info.get('character_id'),'binding_verified':info.get('binding_verified'),
                            'sampled_frames':end-start+1,'visible_mesh_vertices_verified':visible_mesh_verified,
                            'max_root_step_m':max_root_step,'max_bone_angle_delta_deg':max_angle,
                            'event_boundaries':boundary_reports,'out_of_frame':out_of_frame,'snapshots':snapshots,
                            'actual_pose_animation':_action_facts(actor['armature']),
                            'actual_root_animation':_action_facts(actor['root'])})
        for contact in contacts or []:
            actor=resolve_actor(job_id,contact.get('actor_id',actor_id))
            frames=contact.get('frames',[contact.get('frame',start)])
            for frame in frames:
                scene.frame_set(int(frame));depsgraph=bpy.context.evaluated_depsgraph_get()
                arm=actor['armature'].evaluated_get(depsgraph)
                name=contact['bone']
                if name not in arm.pose.bones:
                    names=[b.name for b in arm.pose.bones if b.name.split(':')[-1]==name]
                    if len(names)!=1:raise ValueError('contact bone missing '+name)
                    name=names[0]
                point=arm.matrix_world @ arm.pose.bones[name].head
                target_name=contact.get('target_object')
                if target_name:
                    obj=bpy.data.objects.get(target_name)
                    if obj is None:raise ValueError('contact target missing '+target_name)
                    target=obj.evaluated_get(depsgraph)
                    if target.type=='MESH':
                        mesh=target.to_mesh()
                        try:
                            tree=BVHTree.FromPolygons([target.matrix_world @ v.co for v in mesh.vertices],
                                                     [tuple(p.vertices) for p in mesh.polygons])
                            nearest=tree.find_nearest(point)
                            distance=nearest[3] if nearest else float('inf')
                        finally:target.to_mesh_clear()
                    else:distance=(point-target.matrix_world.translation).length
                else:distance=(point-Vector(contact.get('target_point',contact.get('point')))).length
                threshold=float(contact.get('tolerance_m',.03))
                measured={'actor_id':contact.get('actor_id',actor_id),'bone':name,'frame':int(frame),
                          'distance_m':distance,'tolerance_m':threshold,'passed':distance<=threshold,
                          'scope':'bone_head_to_evaluated_surface_or_explicit_world_point'}
                contact_reports.append(measured)
                if not measured['passed']:failures.append({'check':'contact',**measured})
    finally:scene.frame_set(old_frame)
    registered_contacts=registered_contact_report(job_id,frame_start=start,frame_end=end,actor_id=actor_id)
    if not registered_contacts['passed']:
        failures.append({'check':'registered_contacts','status':registered_contacts['status'],
                         'failure_count':registered_contacts['failure_count'],
                         'failures':registered_contacts['failures']})
    return {'ok':True,'passed':not failures,'scope':'specified identity, finite geometry, animation, boundaries, registered contacts and optional explicit contacts/framing',
            'job_id':job_id,'frame_start':start,'frame_end':end,'actors':reports,'contacts':contact_reports,'failures':failures,
            'registered_contacts':registered_contacts,
            'naturalness_verified':False,'semantics_verified':False,'scene_reload_verified':False,
            'visual_review_required':True}
