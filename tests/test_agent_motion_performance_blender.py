"""Full-vertex/frame projection equivalence and actual CPU performance evidence."""
import bpy
import argparse
import hashlib
import json
import math
import sys
import time
import uuid
from pathlib import Path
import numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))
from agent_character import resolve_actor
from agent_motion_quality import (evaluated_geometry_facts,camera_projection_context,
                                  project_world_vertices,motion_check)


def reference_facts(meshes,depsgraph,scene,camera):
    low=Vector((float('inf'),)*3);high=Vector((float('-inf'),)*3);count=0;outside=0
    for obj in meshes:
        evaluated=obj.evaluated_get(depsgraph);mesh=evaluated.to_mesh()
        try:
            for vertex in mesh.vertices:
                point=evaluated.matrix_world @ vertex.co
                for i in range(3):low[i]=min(low[i],point[i]);high[i]=max(high[i],point[i])
                count+=1
        finally:evaluated.to_mesh_clear()
    for obj in meshes:
        evaluated=obj.evaluated_get(depsgraph);mesh=evaluated.to_mesh()
        try:
            for vertex in mesh.vertices:
                point=world_to_camera_view(scene,camera,evaluated.matrix_world @ vertex.co)
                outside+=int(point.z<=0 or point.x<0 or point.x>1 or point.y<0 or point.y>1)
        finally:evaluated.to_mesh_clear()
    return {'bounds':{'min':list(low),'max':list(high),'vertex_count':count},'outside_vertex_count':outside}


def time_call(function):
    start=time.perf_counter();result=function()
    return result,time.perf_counter()-start


def direction_and_capture_proof():
    from agent_character import actor_ensure
    from agent_motion import sample_motion,prepare_motion
    from agent_motion_timeline import register_event,motion_commit
    from agent_motion_controls import read_pose
    from agent_motion_quality import rotation_delta_degrees,scene_motion_facts
    from mathutils import Quaternion
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=180;scene.render.fps=30
    job=uuid.uuid4().hex[:16];folder=PROJECT/'output'/'jobs'/job;folder.mkdir(parents=True)
    (folder/'request.json').write_text(json.dumps({'prompt':'验证库走路→同机器人转90度→朝新方向继续走；不改变既有角色或帧数。',
        'origin':'robot_direction_performance_test','character_policy':'library_beta'},ensure_ascii=False),encoding='utf-8')
    actor_ensure(job,'A');actor=resolve_actor(job,'A');arm=actor['armature'];root=actor['root']
    motion='mixamo.basic.walk';duration=prepare_motion(arm,motion)['duration_s']
    raw=Vector(sample_motion(arm,motion,duration)['root']);yaw=list(Quaternion((0,0,1),math.pi/2))
    register_event(job,'A','walk_before',{'source':'library','motion_id':motion,'start_frame':1,'end_frame':30,'root_strategy':'source'})
    register_event(job,'A','turn',{'source':'procedural','start_frame':31,'end_frame':60,'root_strategy':'hold','keyframes':[
        {'frame':31,'root_rotation_quaternion':[1,0,0,0],'root_scale':[1,1,1]},
        {'frame':60,'root_rotation_quaternion':yaw,'root_scale':[.8,.8,.8]}]})
    register_event(job,'A','walk_after',{'source':'library','motion_id':motion,'start_frame':61,'end_frame':90,'root_strategy':'source'})
    register_event(job,'A','masked_root_cannot_steal',{'source':'procedural','start_frame':20,'end_frame':80,'mask':['right_arm'],
        'keyframes':[{'frame':20,'root_rotation_quaternion':[0,0,0,1],'root_scale':[.2,.2,.2]},
                     {'frame':80,'root_rotation_quaternion':[0,0,0,1],'root_scale':[.2,.2,.2]}]})
    _,commit_seconds=time_call(lambda:motion_commit(job,'A',1,180))
    scene.frame_set(30);first=root.location.copy()
    scene.frame_set(60);turn_position=root.location.copy();turn_rotation=root.rotation_quaternion.copy()
    scene.frame_set(61);next_position=root.location.copy();next_rotation=root.rotation_quaternion.copy()
    scene.frame_set(90);last=root.location.copy();last_rotation=root.rotation_quaternion.copy();last_scale=root.scale.copy()
    expected=raw+Quaternion(yaw) @ (raw*.8)
    checks=[{'check':'library_walk_after_turn_uses_inherited_90_degree_heading','passed':(last-expected).length<2e-5,'position_error_m':(last-expected).length},
            {'check':'no_root_yaw_or_position_reset_at_next_library_start','passed':(next_position-turn_position).length<2e-5 and rotation_delta_degrees(turn_rotation,next_rotation)<1e-3},
            {'check':'root_scale_and_yaw_inherited_and_masked_overlay_not_owner','passed':rotation_delta_degrees(last_rotation,yaw)<1e-3 and (last_scale-Vector((.8,.8,.8))).length<1e-6}]
    snapshots={}
    for frame in range(1,181):
        scene.frame_set(frame);snapshots[frame]=read_pose(arm,evaluated=True)
    _,capture_seconds=time_call(lambda:motion_commit(job,'A',1,180,capture=True,event_id='captured_whole_180'))
    max_error=0
    for frame in range(1,181):
        scene.frame_set(frame);actual=read_pose(arm,evaluated=True)
        for name,channels in snapshots[frame].items():
            for key,values in channels.items():
                distances=[abs(a-b) for a,b in zip(values,actual[name][key])]
                if key=='rotation_quaternion':distances=min(distances,[abs(a+b) for a,b in zip(values,actual[name][key])],key=max)
                max_error=max(max_error,max(distances))
    checks.append({'check':'all_180_captured_pose_channels_preserved','passed':max_error<1e-4,'max_local_channel_error':max_error})
    checks.append({'check':'actual_final_action_provenance_fingerprints','passed':not scene_motion_facts(job)['errors']})
    target=PROJECT/'reports'/'robot_hybrid_20261014'/('direction_capture_'+job+'.blend')
    bpy.ops.wm.save_as_mainfile(filepath=str(target))
    bpy.ops.wm.open_mainfile(filepath=str(target),use_scripts=False)
    actor=resolve_actor(job,'A');bpy.context.scene.frame_set(90)
    checks.append({'check':'turned_heading_pose_and_direction_reload','passed':
        (actor['root'].location-last).length<2e-5 and rotation_delta_degrees(actor['root'].rotation_quaternion,yaw)<1e-3})
    return {'passed':all(c['passed'] for c in checks),'checks':checks,'library_procedural_library_commit_180_seconds':commit_seconds,
            'full_capture_180_seconds':capture_seconds,'scene':str(target.relative_to(PROJECT)),
            'scene_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'naturalness_verified':False}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--frames',type=int,default=180)
    options=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    source=PROJECT/'reports/robot_hybrid_20261014/edae7137ecd341e3/three_original_robot_hybrid.blend'
    bpy.ops.wm.open_mainfile(filepath=str(source),use_scripts=False)
    scene=bpy.context.scene;job='edae7137ecd341e3';actor=resolve_actor(job,'A')
    camera=scene.camera;scene.frame_set(45)
    depsgraph=bpy.context.evaluated_depsgraph_get()
    old,old_seconds=time_call(lambda:reference_facts(actor['meshes'],depsgraph,scene,camera))
    fast,fast_seconds=time_call(lambda:evaluated_geometry_facts(actor['meshes'],depsgraph,scene,camera))
    checks=[];fixtures=[]
    for camera_type,shift_x,shift_y,fit,scale in [('ORTHO',0,0,'AUTO',(1,1,1)),('PERSP',0,0,'AUTO',(1,1,1)),
            ('PERSP',.13,-.08,'VERTICAL',(.9,1.1,1.2)),('ORTHO',-.1,.15,'HORIZONTAL',(1.1,.8,1.2))]:
        camera.data.type=camera_type;camera.data.shift_x=shift_x;camera.data.shift_y=shift_y
        camera.data.sensor_fit=fit;camera.scale=scale;bpy.context.view_layer.update()
        context=camera_projection_context(scene,camera);max_error=0;sampled=0
        for obj in actor['meshes']:
            evaluated=obj.evaluated_get(depsgraph);mesh=evaluated.to_mesh()
            try:
                world=np.array([list(evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices])
                actual=project_world_vertices(world,context)
                expected=np.array([list(world_to_camera_view(scene,camera,Vector(point))) for point in world])
                max_error=max(max_error,float(np.max(np.abs(actual-expected))));sampled+=len(world)
            finally:evaluated.to_mesh_clear()
        reference=reference_facts(actor['meshes'],depsgraph,scene,camera)
        measured=evaluated_geometry_facts(actor['meshes'],depsgraph,scene,camera)
        frame=camera.data.view_frame(scene=scene)
        points=[camera.matrix_world.normalized() @ Vector((v.x,v.y,v.z)) for v in frame]
        points+=[camera.matrix_world.translation.copy(),camera.matrix_world.normalized() @ Vector((0,0,1))]
        mesh=bpy.data.meshes.new('ProjectionBoundaryProbe');mesh.from_pydata(points,[],[])
        probe=bpy.data.objects.new('ProjectionBoundaryProbe',mesh);scene.collection.objects.link(probe)
        bpy.context.view_layer.update();depsgraph=bpy.context.evaluated_depsgraph_get()
        edge_old=reference_facts([probe],depsgraph,scene,camera)
        edge_new=evaluated_geometry_facts([probe],depsgraph,scene,camera)
        fixtures.append({'camera_type':camera_type,'shift':[shift_x,shift_y],'sensor_fit':fit,'scale':scale,
                         'all_skin_vertices':sampled,'max_ndc_or_depth_error':max_error,
                         'outside_vertex_count_reference':reference['outside_vertex_count'],
                         'outside_vertex_count_fast':measured['outside_vertex_count'],
                         'edge_reference_count':edge_old['outside_vertex_count'],'edge_fast_count':edge_new['outside_vertex_count']})
        checks.append({'check':'all_points_projection_and_boundary_classification','fixture':len(fixtures),
                       'passed':max_error<2e-5 and reference['outside_vertex_count']==measured['outside_vertex_count'] and edge_old['outside_vertex_count']==edge_new['outside_vertex_count']})
        bpy.data.objects.remove(probe,do_unlink=True);bpy.data.meshes.remove(mesh)
    camera.data.type='ORTHO';camera.data.shift_x=0;camera.data.shift_y=0;camera.data.sensor_fit='AUTO';camera.scale=(1,1,1)
    bpy.context.view_layer.update()
    full={}
    for label,function in [('reference',reference_facts),('fast',evaluated_geometry_facts)]:
        begin=time.perf_counter();counts=[];bounds=[]
        for frame in range(1,options.frames+1):
            scene.frame_set(frame);depsgraph=bpy.context.evaluated_depsgraph_get()
            result=function(actor['meshes'],depsgraph,scene,camera)
            counts.append(result['outside_vertex_count']);bounds.append(result['bounds'])
            if frame%45==0:print('PERF_PROGRESS',label,frame,round(time.perf_counter()-begin,3),flush=True)
        full[label]={'seconds':time.perf_counter()-begin,'outside_counts':counts,'bounds':bounds}
    bound_error=max(abs(a-b) for first,second in zip(full['reference']['bounds'],full['fast']['bounds'])
                    for side in ('min','max') for a,b in zip(first[side],second[side]))
    checks.append({'check':'all_180_actual_frames_all_vertices_equivalent','passed':
                   full['reference']['outside_counts']==full['fast']['outside_counts'] and bound_error<2e-5,
                   'world_bounds_max_error_m':bound_error})
    checked,check_seconds=time_call(lambda:motion_check(job,'A',frame_start=1,frame_end=options.frames,check_camera=True))
    checks.append({'check':'full_quality_original_skin_and_all_frames','passed':checked['passed']})
    direction=direction_and_capture_proof()
    result={'passed':all(c['passed'] for c in checks) and direction['passed'],'checks':checks,'fixtures':fixtures,'frames':options.frames,
            'vertices_per_frame':old['bounds']['vertex_count'],'projected_skin_vertices_per_algorithm':old['bounds']['vertex_count']*options.frames,
            'single_frame_reference_seconds':old_seconds,'single_frame_fast_seconds':fast_seconds,
            'full_reference_seconds':full['reference']['seconds'],'full_fast_seconds':full['fast']['seconds'],
            'geometry_speedup':full['reference']['seconds']/full['fast']['seconds'],'full_motion_check_seconds':check_seconds,
            'direction_and_capture':direction,
            'source_scene':str(source.relative_to(PROJECT)),'source_scene_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'scope':'all actual deformed skin vertices and all output frames, projection equivalence and CPU benchmark; no quality threshold relaxed',
            'naturalness_verified':False}
    path=PROJECT/'reports'/'robot_hybrid_20261014'/('performance_'+uuid.uuid4().hex[:16]+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PERFORMANCE_REPORT',path,flush=True)
    if not result['passed']:raise RuntimeError('Projection/performance equivalence failed')


if __name__=='__main__':main()
