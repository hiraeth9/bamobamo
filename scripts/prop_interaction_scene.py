"""Single-person reach/hold/hinge interactions using FK, foot support and hand IK."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import bpy
from mathutils import Vector,Quaternion

sys.path.insert(0,str(Path(__file__).resolve().parent))
from duel_plan import PROJECT,atomic_json
from single_plan import validate_single
from scene_plan import load_scene,allocate_interaction_times
from scene_objects import SceneObjects,path_to,along_path,scene_quality
from duel_motion_sources import MotionSources
from duel_scene import Actor,PREFIX,smooth,blend_pose,point,rotate_world,prepare_feet,setup_stage,setup_camera,action_fcurves,evaluated_corners
from reference_camera import setup_reference_camera,verify_reference_camera


def standing_distance(props,event):
    if event['action']=='touch_table':
        height=props.objects[event['object']]['spec']['size'][2]
        return .38+max(0,.75-height)*.4
    return .43


def timing_requirements(props,event,route):
    length=sum((b-a).length for a,b in zip(route,route[1:]))
    travel=max(.4,length*1.5/1.3)
    operate=.6
    if event['action']!='touch_table':
        spec=props.objects[event['object']]['spec']
        radius=math.hypot(spec['size'][0]-.18,standing_distance(props,event))
        operate=max(.8,radius*math.radians(spec['open_angle_deg'])*1.5/1.3)
    return travel,operate


def build(single,folder):
    environment=load_scene(folder,single['job_id'],1,single['duration_seconds'])
    if not environment or not environment['interactions']: raise ValueError('交互执行器需要物品交互计划')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    definitions={'guard':{'asset':'mixamo_fbx/basic/Idle.fbx'},
                 'step':{'asset':'mixamo_fbx/basic/Walk.fbx','extract_root':True},
                 'grip':{'asset':'mixamo_fbx/combat_melee/Lead Jab.fbx'}}
    sources=MotionSources(definitions)
    props=SceneObjects(environment)
    actor=Actor('A',sources,0,math.pi,.82)
    first=environment['interactions'][0]
    first_anchor=props.anchor(first['object'],'touch' if first['action']=='touch_table' else 'handle')
    first_normal=props.normal(first['object'])
    first_distance=standing_distance(props,first)
    destination=first_anchor+first_normal*first_distance;destination.z=0
    initial=None
    for offset in (0,.6,-.6,1,-1):
        candidate=destination+first_normal*.8+Vector((offset,0,0))
        if not props.floor_blocked(candidate): initial=candidate;break
    if initial is None: raise ValueError('第一个交互物品附近没有安全起始站位')
    # Language-model timing is a proposal. For ordinary prompts redistribute
    # time within the same total, preserving every action and pause. Explicit
    # user timestamps use exact policy and are never moved silently.
    initial_angles={key:e['hinge'].rotation_euler.z for key,e in props.objects.items() if 'hinge' in e}
    probe=initial.copy();required=[];requested=[dict(e) for e in environment['interactions']]
    for event in requested:
        label='touch' if event['action']=='touch_table' else 'handle';ident=event['object']
        destination=props.anchor(ident,label)+props.normal(ident)*standing_distance(props,event);destination.z=0
        route=path_to(props,probe,destination)
        travel,operate=timing_requirements(props,event,route);required.append(travel+.5+operate+.45+.1)
        if label=='handle':
            angle=-math.radians(props.objects[ident]['spec']['open_angle_deg']) if event['action'].startswith('open_') else 0
            props.set_angle(ident,angle)
        probe=props.anchor(ident,label)+props.normal(ident)*standing_distance(props,event);probe.z=0
    for ident,angle in initial_angles.items(): props.set_angle(ident,angle)
    environment['interactions']=allocate_interaction_times(requested,required,environment['timing_policy'])
    atomic_json(folder/'executed_scene_plan.json',environment)
    actor.root.location=initial;actor.set_pose(sources.sample('guard',1))
    bpy.context.view_layer.update();actor.add_ik()
    for obj in sources.meshes+[sources.template]: bpy.data.objects.remove(obj,do_unlink=True)
    scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=single['frame_count'];scene.render.fps=30
    angles={key:e['hinge'].rotation_euler.z for key,e in props.objects.items() if 'hinge' in e}
    event_states={};current=actor.root.location.copy();walk_weight=0;stride=1;move_origins={};metadata=[]
    stride_distance=max(.2,(sources.root_motion['step'][-1]-sources.root_motion['step'][0]).length*.48)
    for frame in range(1,scene.frame_end+1):
        scene.frame_set(frame);seconds=(frame-1)/30
        event=next((e for e in environment['interactions'] if e['start']<=seconds<=e['end']),None)
        phase=(seconds-event['start'])/(event['end']-event['start']) if event else 0
        for c in actor.constraints.values(): c.influence=0
        for c in actor.rotation_constraints.values(): c.influence=0
        for key,angle in angles.items(): props.set_angle(key,angle)
        previous=current.copy();reach=0;body_weight=0;anchor=None;normal=None
        if event:
            ident=event['object'];label='touch' if event['action']=='touch_table' else 'handle'
            anchor=props.anchor(ident,label);normal=props.normal(ident)
            distance=standing_distance(props,event)
            if event['id'] not in event_states:
                destination=anchor+normal*distance;destination.z=0
                route=path_to(props,current,destination)
                total=event['end']-event['start']
                travel,operate=timing_requirements(props,event,route)
                if travel+.5+operate+.45>total:
                    raise ValueError('走近和操作需要更多时间，请延长该交互或把物品放近一些')
                approach=max(travel,min(total*.35,total-.5-operate-.45))/total
                event_states[event['id']]={'path':route,'angle':angles.get(ident,0),
                    'approach':approach,'operate_start':approach+.5/total,
                    'operate_end':1-.55/total,'release_start':1-.45/total}
            state=event_states[event['id']]
            if phase<=state['approach']:
                current=along_path(state['path'],smooth(phase/state['approach']))
            else:
                if label=='handle':
                    goal_angle=-math.radians(props.objects[ident]['spec']['open_angle_deg']) if event['action'].startswith('open_') else 0
                    angle=state['angle']+(goal_angle-state['angle'])*smooth((phase-state['operate_start'])/(state['operate_end']-state['operate_start']))
                    props.set_angle(ident,angle);angles[ident]=angle
                    anchor=props.anchor(ident,label);normal=props.normal(ident)
                current=anchor+normal*distance;current.z=0
            reach=smooth((phase-state['approach'])/(state['operate_start']-state['approach']))*(1-smooth((phase-state['release_start'])/(1-state['release_start'])))
            body_weight=smooth((phase-state['approach']+.04)/(state['operate_start']-state['approach']+.04))*(1-smooth((phase-state['release_start'])/(1-state['release_start'])))
        velocity=(current-previous).length*30
        walk_weight+=(min(1,velocity/.65)-walk_weight)*.3
        stride+=(current-previous).length/stride_distance*(sources.ends['step']-1)
        pose=sources.sample('guard',1+(seconds*30)%(sources.ends['guard']-1))
        walking=sources.sample('step',1+(stride-1)%(sources.ends['step']-1))
        # Small approach steps keep the leading foot clear of the wall/leaf.
        pose=blend_pose(pose,walking,walk_weight*.48)
        if event and event['action']!='touch_table':
            grip=sources.sample('grip',8,event['side']=='right')
            for bone in pose:
                if event['side'].title()+'Hand' in bone and any(t in bone for t in ('Thumb','Index','Middle','Ring','Pinky')):
                    pose[bone]=blend_pose({bone:pose[bone]},{bone:grip[bone]},reach*.7)[bone]
        actor.set_pose(pose);actor.root.location=current
        if event:
            direction=-normal
            motion=current-previous
            if phase<state['approach']-.04 and motion.xy.length>.001:
                direction=motion.normalized()
            target_yaw=math.atan2(direction.x,-direction.y)
            delta=math.atan2(math.sin(target_yaw-actor.root.rotation_euler.z),math.cos(target_yaw-actor.root.rotation_euler.z))
            actor.root.rotation_euler.z+=max(-.13,min(.13,delta))
        yaw=actor.root.rotation_euler.z
        actor.forward=Vector((math.sin(yaw),-math.cos(yaw),0));actor.lateral=Vector((math.cos(yaw),math.sin(yaw),0))
        bpy.context.view_layer.update()
        floor_z=min(p.z for p in evaluated_corners(actor.meshes))
        if floor_z<-.018:
            actor.hip_offset(Vector((0,0,-.018-floor_z)))
            bpy.context.view_layer.update()
        # Foot anchors come from the standing/walking FK before body lean, so
        # crouching bends the knees instead of pushing the feet below the floor.
        actor.source_feet=True
        record={'frame':frame,'stance':{},'swing':{},'support_crouch_m':{},'event':event['id'] if event else None,
                'root':list(current),'contact':None}
        feet=prepare_feet(actor,{},None,0,{},move_origins,record)
        if event and anchor.z<1.05:
            low=max(0,.768-anchor.z)
            lean=1+low*1.7
            rotate_world(actor,'Spine',actor.lateral,.38*lean*body_weight)
            bpy.context.view_layer.update()
            rotate_world(actor,'Spine1',actor.lateral,.26*lean*body_weight)
            actor.hip_offset(Vector((0,0,-(.16+low*.9)*body_weight)))
            bpy.context.view_layer.update()
        natural={key:point(actor.arm,key) for key in actor.goals}
        rotations={key:(actor.arm.matrix_world@actor.arm.pose.bones[PREFIX+key].matrix).to_quaternion() for key in actor.goals}
        desired={**natural,**feet}
        active_hand=None
        if event:
            active_hand=event['side'].title()+'Hand'
            tip=point(actor.arm,actor.tip_names[active_hand])-natural[active_hand]
            direction=-normal
            aimed=tip.rotation_difference(direction)@rotations[active_hand]
            desired[active_hand]=natural[active_hand].lerp(anchor-direction*tip.length,reach)
            rotations[active_hand]=rotations[active_hand].slerp(aimed,reach)
            if reach>=.999:
                record['contact']={'event':event['id'],'object':event['object'],'action':event['action'],
                                   'limb':active_hand,'target':list(anchor)}
        for key,goal in actor.goals.items():
            goal.location=desired[key];goal.keyframe_insert('location',frame=frame)
            goal.rotation_quaternion=rotations[key];goal.keyframe_insert('rotation_quaternion',frame=frame)
            end='Hand' if key.endswith('Hand') else 'Foot';side=key[:-len(end)]
            middle,start=('ForeArm','Arm') if end=='Hand' else ('Leg','UpLeg')
            pole=actor.poles[key];pole.location=actor.pole_location(side,middle,start,desired[key]);pole.keyframe_insert('location',frame=frame)
            influence=(reach if key==active_hand else 0) if end=='Hand' else 1
            actor.constraints[key].influence=influence;actor.constraints[key].keyframe_insert('influence',frame=frame)
            actor.rotation_constraints[key].influence=influence;actor.rotation_constraints[key].keyframe_insert('influence',frame=frame)
        actor.bake_pose(frame)
        for ident,angle in angles.items(): props.set_angle(ident,angle,frame)
        metadata.append(record)
    for action in bpy.data.actions:
        if action.users:
            for curve in action_fcurves(action):
                for key in curve.keyframe_points: key.interpolation='LINEAR'
    setup_stage()
    # The side view separates a rear window operator from the foreground table.
    camera_meshes=actor.meshes+props.focus_meshes()
    if single.get('camera'):
        bounds=setup_reference_camera(camera_meshes,single['camera'],{'A':actor},props)
        camera_quality=verify_reference_camera(camera_meshes,single['camera'],{'A':actor},props)
    else:
        bounds=setup_camera(camera_meshes,{'camera':{'azimuth_deg':-20}}, {'A':actor})
        camera_quality=None
    quality=scene_quality(props,{'A':actor.arm},single['frame_count'])
    if camera_quality is not None:
        quality.update(camera_facts=bounds,camera_quality=camera_quality)
        quality['passed']=bool(quality['passed'] and camera_quality['passed'])
    contacts=[];feet_fail=[];ground_fail=[];flip_fail=[];previous_rot={};max_foot=0
    for record in metadata:
        frame=record['frame'];scene.frame_set(frame)
        for joint in ('LeftForeArm','RightForeArm','LeftLeg','RightLeg'):
            q=actor.arm.pose.bones[PREFIX+joint].matrix.to_quaternion()
            if joint in previous_rot:
                angle=math.degrees(2*math.acos(min(1,abs(q.dot(previous_rot[joint])))))
                if angle>65: flip_fail.append({'frame':frame,'joint':joint,'angle_deg':round(angle,2)})
            previous_rot[joint]=q.copy()
        if min(p.z for p in evaluated_corners(actor.meshes))<-.045: ground_fail.append({'frame':frame})
        for name,target in record['stance'].items():
            actual=point(actor.arm,name.split(':')[1]);goal=Vector(target)
            error=(actual-goal).xy.length;max_foot=max(max_foot,error)
            if error>.0165: feet_fail.append({'frame':frame,'limb':name,'error_m':round(error,4)})
        if record['contact']:
            contact=record['contact'];actual=point(actor.arm,actor.tip_names[contact['limb']])
            error=(actual-Vector(contact['target'])).length
            contacts.append({**contact,'frame':frame,'error_m':round(error,4),'passed':error<=.0495})
    for event in environment['interactions']:
        if not any(c['event']==event['id'] for c in contacts): raise RuntimeError('交互没有实际接触帧')
    max_speed=max(((Vector(b['root'])-Vector(a['root'])).length*30 for a,b in zip(metadata,metadata[1:])),default=0)
    quality.update({'passed':quality['passed'] and not any((feet_fail,ground_fail,flip_fail)) and all(c['passed'] for c in contacts) and max_speed<=1.35,
                    'kind':'single_object_interaction','frame_count':single['frame_count'],'contacts':contacts,
                    'max_stance_error_m':round(max_foot,4),'stance_failures':feet_fail[:20],'ground_failures':ground_fail[:20],
                    'joint_flip_failures':flip_fail[:20],'max_root_speed_m_s':round(max_speed,3),'camera_bounds':bounds,'final_angles_deg':{k:round(math.degrees(v),3) for k,v in angles.items()},
                    'requested_interactions':requested,'executed_interactions':environment['interactions'],'timing_policy':environment['timing_policy'],
                    'engine_sources_sha256':{f:hashlib.sha256((PROJECT/'scripts'/f).read_bytes()).hexdigest() for f in ('prop_interaction_scene.py','scene_objects.py','scene_plan.py','duel_scene.py','camera_plan.py','reference_camera.py')},
                    'visual_review_required':True})
    atomic_json(folder/'quality.json',quality);atomic_json(folder/'trajectory.json',{'frames':metadata})
    scene['single_plan_json']=json.dumps(single);scene['scene_plan_json']=json.dumps(environment);scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(folder/'scene.blend'))
    print('PROP_SCENE '+json.dumps({'passed':quality['passed'],'quality':quality}),flush=True)
    return quality


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);path=Path(args.plan)
    raw=json.loads(path.read_text());quality=build(validate_single(raw,raw['job_id']),path.parent)
    if not quality['passed']: raise RuntimeError('物品交互检查失败，查看quality.json')
