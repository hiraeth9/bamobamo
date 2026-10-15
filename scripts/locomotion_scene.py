"""Bake target-driven flat-ground movement with a continuous gait and foot IK.

Natural-language interpretation belongs to Hermes. The saved movement plan
owns destinations and timing; geometry owns the reachable route. Original
clips provide body motion rather than being replayed as equal-length actions.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0,str(Path(__file__).resolve().parent))
from duel_plan import PROJECT,atomic_json
from single_plan import validate_single
from scene_plan import load_scene
from scene_objects import SceneObjects,scene_quality
from duel_scene import Actor,PREFIX,point,setup_stage,setup_camera,action_fcurves
from duel_motion_sources import MotionSources
from locomotion_path import compile_timeline
from locomotion_gait import LocomotionGait
from reference_camera import setup_reference_camera,verify_reference_camera

ENGINE_FILES=('locomotion_scene.py','locomotion_path.py','locomotion_gait.py',
              'locomotion_plan.py','single_plan.py','scene_objects.py',
              'duel_scene.py','duel_motion_sources.py','camera_plan.py','reference_camera.py')
SOURCE_HASHES={name:hashlib.sha256((PROJECT/'scripts'/name).read_bytes()).hexdigest()
               for name in ENGINE_FILES}


def skin_vertices(actor):
    arrays=[];graph=bpy.context.evaluated_depsgraph_get()
    for mesh in actor.meshes:
        evaluated=mesh.evaluated_get(graph)
        local=np.empty((len(evaluated.data.vertices),3),dtype=np.float64)
        evaluated.data.vertices.foreach_get('co',local.ravel())
        matrix=np.array(evaluated.matrix_world,dtype=np.float64)
        arrays.append(local@matrix[:3,:3].T+matrix[:3,3])
    return np.concatenate(arrays)


def collision_parts(world,props):
    failures=[]
    for solid in props.solids:
        inverse=np.array(solid.matrix_world.inverted(),dtype=np.float64)
        local=world@inverse[:3,:3].T+inverse[:3,3]
        bounds=np.array(solid.bound_box,dtype=np.float64)
        margin=min(.004,float((bounds.max(axis=0)-bounds.min(axis=0)).min())*.2)
        mask=np.all((local>bounds.min(axis=0)+margin)&(local<bounds.max(axis=0)-margin),axis=1)
        if np.any(mask):failures.append({'part':solid.name,'vertex_count':int(np.count_nonzero(mask))})
    return failures


def bake_feet(actor,frame,feet):
    for key in ('LeftFoot','RightFoot'):
        goal=actor.goals[key]
        goal.location=feet['desired'][key]
        goal.rotation_quaternion=feet['rotations'][key]
        goal.keyframe_insert('location',frame=frame)
        goal.keyframe_insert('rotation_quaternion',frame=frame)
        side=key.removesuffix('Foot');pole=actor.poles[key]
        pole.location=actor.pole_location(side,'Leg','UpLeg',goal.location)
        pole.keyframe_insert('location',frame=frame)
        influence=feet['influences'][key]
        for constraint in (actor.constraints[key],actor.rotation_constraints[key]):
            constraint.influence=influence;constraint.keyframe_insert('influence',frame=frame)
    actor.bake_pose(frame)


def angular_distance(a,b):
    return abs(math.atan2(math.sin(a-b),math.cos(a-b)))


def build(single,folder):
    folder=Path(folder);movement=single.get('locomotion')
    if not movement:raise ValueError('移动执行器需要single_plan.locomotion路线')
    environment=load_scene(folder,single['job_id'],1,single['duration_seconds'])
    if environment and environment['interactions']:
        raise ValueError('通用移动本轮兼容静态场景，不与物品操作或翻窗混编')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version=0
    sources=MotionSources({'guard':{'asset':'mixamo_fbx/basic/Idle.fbx'},
        'walk':{'asset':'mixamo_fbx/basic/Walk.fbx','extract_root':True},
        'run':{'asset':'mixamo_fbx/basic/Run.fbx','extract_root':True}})
    plan=environment or {'room':{'enabled':False,'size':[12,12,3]},'objects':[],'interactions':[]}
    props=SceneObjects(plan)
    bounds=None
    if plan['room']['enabled']:
        width,depth,_=plan['room']['size'];bounds=(-width/2+.4,width/2-.4,-depth/2+.4,depth/2-.4)
    timeline=compile_timeline(movement,single['duration_seconds'],
        blocked=lambda xy:props.floor_blocked(xy,padding=.4),bounds=bounds,fps=30)
    if len(timeline['frames'])!=single['frame_count']:
        raise RuntimeError('路线采样帧数与视频计划不一致')
    first=timeline['frames'][0]
    actor=Actor('A',sources,first['position'][0],first['yaw'],.82)
    actor.root.location=Vector(first['position'])
    actor.set_pose(sources.sample('guard',1));bpy.context.view_layer.update();actor.add_ik()
    gait=LocomotionGait(actor,sources,fps=30)
    for obj in sources.meshes+[sources.template]:bpy.data.objects.remove(obj,do_unlink=True)
    scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=single['frame_count'];scene.render.fps=30
    records=[];previous_distance=first.get('distance',0);previous_turn=first.get('turn_distance',0)
    for sample in timeline['frames']:
        frame=sample['frame'];scene.frame_set(frame)
        for constraint in (*actor.constraints.values(),*actor.rotation_constraints.values()):constraint.influence=0
        delta_distance=max(0,sample['distance']-previous_distance)
        delta_turn=max(0,sample.get('turn_distance',0)-previous_turn)
        pose=gait.pose(sample['speed'],sample['gait'],delta_distance,delta_turn,dt=1/30)
        actor.set_pose(pose);actor.root.location=Vector(sample['position'])
        actor.root.rotation_euler.z=sample['yaw']
        actor.forward=Vector((math.sin(sample['yaw']),-math.cos(sample['yaw']),0))
        actor.lateral=Vector((math.cos(sample['yaw']),math.sin(sample['yaw']),0))
        bpy.context.view_layer.update()
        record={**sample,'root':list(actor.root.location),'stance':{},'swing':{},'toe_stance':{}}
        feet=gait.feet(record)
        bake_feet(actor,frame,feet);records.append(record)
        previous_distance=sample['distance'];previous_turn=sample.get('turn_distance',0)
    for action in bpy.data.actions:
        if action.users:
            for fcurve in action_fcurves(action):
                for key in fcurve.keyframe_points:key.interpolation='LINEAR'
    setup_stage()
    camera_meshes=actor.meshes+props.focus_meshes()
    if single.get('camera'):
        camera_bounds=setup_reference_camera(camera_meshes,single['camera'],{'A':actor},props)
        camera_quality=verify_reference_camera(camera_meshes,single['camera'],{'A':actor},props)
    else:
        camera_bounds=setup_camera(camera_meshes,{'camera':{'azimuth_deg':-55}},{'A':actor})
        camera_quality=None
    quality=scene_quality(props,{'A':actor.arm},single['frame_count'])
    if camera_quality is not None:
        quality.update(camera_facts=camera_bounds,camera_quality=camera_quality)
        quality['passed']=bool(quality['passed'] and camera_quality['passed'])
    ground=[];solids=[];stance=[];flips=[];heading=[];previous_rot={};previous_root=None;previous_yaw=None
    root_speeds=[];yaw_rates=[];max_stance_error=0;actual_positions=[]
    support_frames=Counter();moving_frames=Counter()
    for record in records:
        frame=record['frame'];scene.frame_set(frame);bpy.context.view_layer.update()
        world=skin_vertices(actor);floor=float(world[:,2].min())
        if floor<-.045:ground.append({'frame':frame,'minimum_skin_z_m':floor})
        for failure in collision_parts(world,props):solids.append({'frame':frame,**failure})
        root=actor.root.matrix_world.translation.copy();yaw=actor.root.rotation_euler.z
        actual_positions.append(root.copy())
        if record['speed']>.18:
            moving_frames[record['gait']]+=1
            if record.get('toe_stance'):support_frames[record['gait']]+=1
        if previous_root is not None:
            delta=root-previous_root;speed=delta.xy.length*30;root_speeds.append(speed)
            rate=math.degrees(angular_distance(yaw,previous_yaw))*30;yaw_rates.append(rate)
            if speed>.18:
                direction=Vector((math.sin(yaw),-math.cos(yaw),0))
                error=math.degrees(math.acos(min(1,max(-1,direction.dot(delta.normalized())))))
                if error>25:heading.append({'frame':frame,'error_deg':error,'speed_m_s':speed})
        previous_root=root;previous_yaw=yaw
        for key,target in record.get('toe_stance',{}).items():
            side=key.split(':')[-1].removesuffix('ToeBase').removesuffix('Foot')
            actual=point(actor.arm,side+'ToeBase');goal=Vector(target)
            error=(actual-goal).length;max_stance_error=max(max_stance_error,error)
            if error>.0165:stance.append({'frame':frame,'limb':key,'error_m':error})
        for joint in ('Hips','Spine','LeftUpLeg','RightUpLeg','LeftLeg','RightLeg','LeftFoot','RightFoot'):
            rotation=actor.arm.pose.bones[PREFIX+joint].matrix.to_quaternion()
            if joint in previous_rot:
                angle=math.degrees(2*math.acos(min(1,abs(rotation.dot(previous_rot[joint])))))
                if angle>65:flips.append({'frame':frame,'joint':joint,'angle_deg':angle})
            previous_rot[joint]=rotation.copy()
    destinations=[];turns=[]
    for index,segment in enumerate(movement['segments']):
        if segment['gait']=='turn':
            at_frame=min(single['frame_count'],max(1,round(segment['end']*30)+1))
            scene.frame_set(at_frame)
            error=math.degrees(angular_distance(actor.root.rotation_euler.z,math.radians(segment['end_yaw_deg'])))
            turns.append({'segment_index':index,'frame':at_frame,'error_deg':error,'passed':error<=2})
        if segment['gait'] not in ('walk','run'):continue
        at_frame=min(single['frame_count'],max(1,round(segment['end']*30)+1))
        scene.frame_set(at_frame);actual=actor.root.matrix_world.translation
        target=Vector((*segment['waypoints'][-1],0));error=(actual-target).xy.length
        destinations.append({'segment_index':index,'frame':at_frame,'target':list(target),
                             'actual':list(actual),'error_m':error,'passed':error<=.04})
    accelerations=[abs(b-a)*30 for a,b in zip(root_speeds,root_speeds[1:])]
    # A root at its destination does not prove the swing foot has landed.
    # Verify both actual skinned feet and the stable final toe markers.
    terminal=[];previous_toes={};max_terminal_slide=0
    for frame in range(max(1,single['frame_count']-2),single['frame_count']+1):
        scene.frame_set(frame);bpy.context.view_layer.update()
        for side in ('Left','Right'):
            minimum=gait._skin_min(side)
            toe=point(actor.arm,side+'ToeBase')
            if side in previous_toes:
                max_terminal_slide=max(max_terminal_slide,(toe-previous_toes[side]).length)
            previous_toes[side]=toe.copy()
            terminal.append({'frame':frame,'side':side,'minimum_skin_z_m':minimum,
                             'toe_anchor_present':actor.id+':'+side+'ToeBase' in records[frame-1]['toe_stance']})
    terminal_ok=bool(all(-.025<=sample['minimum_skin_z_m']<=.03 and sample['toe_anchor_present']
                         for sample in terminal) and max_terminal_slide<=.01)
    speeds=max(root_speeds,default=0);rate=max(yaw_rates,default=0)
    support_ok=all(support_frames[key]>0 for key,count in moving_frames.items() if count>=15)
    passed=bool(quality['passed'] and not ground and not solids and not stance and not flips and not heading
                and all(d['passed'] for d in destinations) and all(t['passed'] for t in turns)
                and support_ok and terminal_ok and speeds<=3.55 and rate<=125)
    quality.update({'passed':passed,'kind':'single_locomotion','actor':'A','frame_count':single['frame_count'],
        'phase_counts':dict(Counter(r['gait'] for r in records)),'destinations':destinations,'turns':turns,
        'moving_frames':dict(moving_frames),'support_frames':dict(support_frames),'support_coverage_passed':support_ok,
        'skin_obstacle_failures':solids[:40],'ground_failures':ground[:30],'stance_failures':stance[:30],
        'joint_flip_failures':flips[:30],'heading_failures':heading[:30],
        'max_stance_error_m':max_stance_error,'max_root_speed_m_s':speeds,
        'max_root_acceleration_m_s2':max(accelerations,default=0),'max_yaw_rate_deg_s':rate,
        'terminal_feet_passed':terminal_ok,'terminal_feet':terminal,
        'max_terminal_toe_slide_m':max_terminal_slide,
        'camera_bounds':camera_bounds,'route_warnings':timeline.get('warnings',[]),
        'engine_sources_sha256':SOURCE_HASHES,'visual_review_required':True,
        'scope':'Flat-ground single-actor route, acceleration, heading, skin vertices vs static solid OBBs, cyclic gait, toe support markers, grounded feet and full-frame bounds. Not arbitrary action matching, a full continuous triangle collision solver or rigid-body physics.'})
    atomic_json(folder/'quality.json',quality);atomic_json(folder/'trajectory.json',{'frames':records})
    atomic_json(folder/'executed_locomotion_plan.json',{'version':1,'request':movement,
        'segments':timeline.get('segments',[]),'warnings':timeline.get('warnings',[])})
    scene['single_plan_json']=json.dumps(single)
    if environment:scene['scene_plan_json']=json.dumps(environment)
    scene['locomotion_quality_json']=json.dumps(quality);scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(folder/'scene.blend'))
    print('LOCOMOTION_SCENE '+json.dumps({k:quality[k] for k in ('passed','phase_counts','max_stance_error_m',
        'max_root_speed_m_s','max_yaw_rate_deg_s','destinations')},ensure_ascii=False),flush=True)
    return quality


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);path=Path(args.plan)
    raw=json.loads(path.read_text());quality=build(validate_single(raw,raw['job_id']),path.parent)
    if not quality['passed']:raise RuntimeError('移动路线、支撑脚、碰撞、朝向或构图检查失败，查看quality.json')
