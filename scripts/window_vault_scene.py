"""A window-relative walk/run, supported vault and grounded landing sequence.

Source clips supply locomotion; a continuous world trajectory and calibrated
FK/IK supply the single traversal. Acceptance measures the deformed body, not
the presence of a Jump clip or a planner's assertion that the window was crossed.
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
from scene_objects import SceneObjects,path_to,along_path,scene_quality
from duel_motion_sources import MotionSources
from duel_scene import (Actor,PREFIX,smooth,blend_pose,point,prepare_feet,
                        setup_stage,setup_camera,action_fcurves)
from vault_pose import apply_vault_pose,_arm_to_target
from reference_camera import setup_reference_camera,verify_reference_camera

ENGINE_SOURCES = ('window_vault_scene.py','vault_pose.py','scene_objects.py',
                  'scene_plan.py','duel_motion_sources.py','camera_plan.py','reference_camera.py')
SOURCE_HASHES = {name:hashlib.sha256((PROJECT/'scripts'/name).read_bytes()).hexdigest()
                 for name in ENGINE_SOURCES}


def curve(value,keys):
    if value<=keys[0][0]: return keys[0][1]
    for (a,x),(b,y) in zip(keys,keys[1:]):
        if value<=b: return x+(y-x)*smooth((value-a)/(b-a))
    return keys[-1][1]


def approach_route(props,start,end):
    """Preserve a clear window-relative line before consulting the floor grid."""
    width,depth,_=props.plan['room']['size']
    samples=max(2,math.ceil((end-start).length/.04)+1)
    direct=[]
    for fraction in np.linspace(0,1,samples):
        position=start.lerp(end,float(fraction));direct.append(position)
        if props.plan['room']['enabled'] and (abs(position.x)>=width/2-.3 or abs(position.y)>=depth/2-.3):
            break
        if props.floor_blocked(position):break
    else:return [start.copy(),end.copy()]
    return path_to(props,start,end)


def vertices(actor):
    deps=bpy.context.evaluated_depsgraph_get();arrays=[]
    for mesh in actor.meshes:
        evaluated=mesh.evaluated_get(deps)
        local=np.empty((len(evaluated.data.vertices),3),dtype=np.float64)
        evaluated.data.vertices.foreach_get('co',local.ravel())
        matrix=np.array(evaluated.matrix_world,dtype=np.float64)
        arrays.append(local@matrix[:3,:3].T+matrix[:3,3])
    return np.concatenate(arrays)


def body_masks(actor):
    hands=[];feet={'Left':[],'Right':[]}
    for mesh in actor.meshes:
        group_names={g.index:g.name.removeprefix(PREFIX) for g in mesh.vertex_groups}
        for vertex in mesh.data.vertices:
            hands.append(any('Hand' in group_names[g.group] and g.weight>.25 for g in vertex.groups))
            for side in feet:
                feet[side].append(any(group_names[g.group].startswith(side) and
                    any(part in group_names[g.group] for part in ('Foot','Toe')) and g.weight>.25 for g in vertex.groups))
    return np.array(hands,dtype=bool),{k:np.array(v,dtype=bool) for k,v in feet.items()}


def aperture_failures(world,frame_inverse,spec,hands):
    """Skin vertices intersecting the real wall slab must lie in the aperture."""
    local=world@frame_inverse[:3,:3].T+frame_inverse[:3,3]
    in_slab=(np.abs(local[:,1])<=max(.07,spec['size'][1]/2)+.022)&~hands
    section=local[in_slab]
    if not len(section):return None
    half=spec['size'][0]/2-.055
    lower=.055;upper=spec['size'][2]-.055
    bad=(np.abs(section[:,0])>half-.008)|(section[:,2]<lower-.008)|(section[:,2]>upper+.008)
    if not np.any(bad):return None
    offending=section[bad]
    return {'vertex_count':int(np.count_nonzero(bad)),
            'local_min':offending.min(axis=0).tolist(),'local_max':offending.max(axis=0).tolist()}


def solid_failures(world,props,hands):
    """Check skin vertices against solid OBB interiors, excluding hand support."""
    failures=[]
    body=world[~hands]
    for solid in props.solids:
        inverse=np.array(solid.matrix_world.inverted(),dtype=np.float64)
        local=body@inverse[:3,:3].T+inverse[:3,3]
        bbox=np.array(solid.bound_box,dtype=np.float64)
        # The thin pane must still be checked; a small interior margin avoids
        # treating exact skin/sill surface contact as body interpenetration.
        margin=min(.004,float((bbox.max(axis=0)-bbox.min(axis=0)).min())*.2)
        mask=np.all((local>bbox.min(axis=0)+margin)&(local<bbox.max(axis=0)-margin),axis=1)
        if np.any(mask):failures.append({'part':solid.name,'vertex_count':int(np.count_nonzero(mask))})
    return failures


def bake_targets(actor,frame,desired,rotations,influences):
    for key,goal in actor.goals.items():
        goal.location=desired[key];goal.rotation_quaternion=rotations[key]
        goal.keyframe_insert('location',frame=frame);goal.keyframe_insert('rotation_quaternion',frame=frame)
        end='Hand' if key.endswith('Hand') else 'Foot';side=key[:-len(end)]
        middle,start=('ForeArm','Arm') if end=='Hand' else ('Leg','UpLeg')
        pole=actor.poles[key]
        pole.location=actor.pole_location(side,middle,start,desired[key]);pole.keyframe_insert('location',frame=frame)
        for constraint in (actor.constraints[key],actor.rotation_constraints[key]):
            constraint.influence=influences[key];constraint.keyframe_insert('influence',frame=frame)
    bpy.context.view_layer.update();actor.bake_pose(frame)


def build(single,folder):
    environment=load_scene(folder,single['job_id'],1,single['duration_seconds'])
    if not environment or len(environment['interactions'])!=1 or environment['interactions'][0]['action']!='vault_window':
        raise ValueError('翻窗执行器需要唯一vault_window交互')
    event=environment['interactions'][0];spec=next(o for o in environment['objects'] if o['id']==event['object'])
    if spec['size'][0]<1 or spec['size'][2]<1:
        raise ValueError('窗洞净空不足：翻窗第一版需要窗宽、窗高至少1.0米，不能缩放人物或改掉用户尺寸')
    if not .55<=spec['position'][2]<=1.05:
        raise ValueError('翻窗第一版支持窗底高0.55至1.05米，高窗攀爬或落差跳落尚未接入')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version=0
    sources=MotionSources({'guard':{'asset':'mixamo_fbx/basic/Idle.fbx'},
        'walk':{'asset':'mixamo_fbx/basic/Walk.fbx','extract_root':True},
        'run':{'asset':'mixamo_fbx/basic/Run.fbx','extract_root':True}})
    props=SceneObjects(environment);ident=event['object'];entry=props.objects[ident]
    normal=props.frame_normal(ident);forward=-normal
    right=entry['root'].matrix_world.to_3x3()@Vector((1,0,0))
    shift=.12
    for label in ('sill_left','sill_right'): entry['anchors'][label].location.x+=shift
    center=entry['root'].matrix_world.translation.copy()+right*shift;center.z=0
    # Providing initial clearance is scene preparation, not a remote opening
    # performance. Explicit closed/break-glass requirements are refused by the
    # semantic protocol rather than silently converted into this precondition.
    props.set_angle(ident,-math.radians(spec['open_angle_deg']))
    preconditions=[]
    if spec['state']=='closed':preconditions.append({'object':ident,'requested_default_state':'closed',
        'effective_initial_state':'open','kind':'initial_clearance','performed_before_video':True})
    hidden=props.traversal_cutaway(ident) if not single.get('camera') else []
    yaw=math.atan2(forward.x,-forward.y)
    actor=Actor('A',sources,0,yaw,.82)
    initial_distance=min(3.85,environment['room']['size'][1]-.65)
    # The inward-opening leaf occupies the hinge side of the approach. Enter
    # on the opposite side, then converge to the central sill support points.
    approach_offset=right*.12
    takeoff=center+normal*.85+approach_offset;initial=center+normal*initial_distance+approach_offset
    if props.floor_blocked(takeoff):raise ValueError('窗前起跳站位被物品封堵，没有可达的翻窗入口')
    if props.floor_blocked(initial):
        candidates=[initial+right*offset for offset in (.65,-.65,1,-1)]
        initial=next((p for p in candidates if not props.floor_blocked(p)),None)
        if initial is None:raise ValueError('窗前没有可行的走跑起始站位')
    route=approach_route(props,initial,takeoff)
    for a,b in zip(route,route[1:]):
        for fraction in np.linspace(0,1,9):
            if props.floor_blocked(a.lerp(b,float(fraction))):raise ValueError('助跑通道被物品封堵')
    actor.root.location=initial;actor.set_pose(sources.sample('guard',1))
    bpy.context.view_layer.update();actor.add_ik()
    for obj in sources.meshes+[sources.template]:bpy.data.objects.remove(obj,do_unlink=True)
    hands,foot_masks=body_masks(actor)
    # Grounded stance at the eventual landing is measured from actual skin.
    final=center+forward*1.05
    actor.root.location=final;actor.set_pose(sources.sample('guard',1))
    for c in (*actor.constraints.values(),*actor.rotation_constraints.values()):c.influence=0
    bpy.context.view_layer.update();world=vertices(actor)
    landing_goals={};landing_rotations={}
    for side in ('Left','Right'):
        key=side+'Foot';goal=point(actor.arm,key)
        goal.z-=float(world[foot_masks[side],2].min())+.006
        landing_goals[key]=goal
        landing_rotations[key]=(actor.arm.matrix_world@actor.arm.pose.bones[PREFIX+key].matrix).to_quaternion()
    actor.root.location=initial
    actor.set_pose(sources.sample('guard',1));bpy.context.view_layer.update()
    contacts={side:props.anchor(ident,'sill_left' if (point(actor.arm,side+'Arm')-initial).dot(right)<0 else 'sill_right')
              for side in ('Left','Right')}
    scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=single['frame_count'];scene.render.fps=30
    span=event['end']-event['start'];approach_seconds=min(span-2.8,3.4)
    if approach_seconds<2.8:raise ValueError('走跑、撑窗穿越和落地需要至少6秒')
    walk_fraction=event.get('walk_fraction',.45) if event['approach']=='walk_run' else (1 if event['approach']=='walk' else 0)
    split_distance=walk_fraction/(walk_fraction+(1-walk_fraction)*1.8) if 0<walk_fraction<1 else walk_fraction
    vault_seconds=2.05
    vault_start=event['start']+approach_seconds;vault_end=vault_start+vault_seconds
    phase_records=[];distance=0;previous=initial.copy();current=initial.copy();move_origins={}
    source_distance={name:max(.1,(sources.root_motion[name][-1]-sources.root_motion[name][0]).length) for name in ('walk','run')}
    last_approach_pose=sources.sample('guard',1)
    for frame in range(1,scene.frame_end+1):
        scene.frame_set(frame);seconds=(frame-1)/30
        for constraint in (*actor.constraints.values(),*actor.rotation_constraints.values()):constraint.influence=0
        actor.root.rotation_euler.z=yaw;actor.forward=forward.copy();actor.lateral=Vector((math.cos(yaw),math.sin(yaw),0))
        record={'frame':frame,'seconds':seconds,'root':None,'stance':{},'swing':{},'contacts':[]}
        if seconds<event['start']:
            stage='idle';current=initial.copy();pose=sources.sample('guard',1)
            actor.set_pose(pose);actor.root.location=current;pose_info=None
        elif seconds<vault_start:
            elapsed=seconds-event['start'];fraction=elapsed/approach_seconds
            if 0<walk_fraction<1:
                if fraction<walk_fraction:
                    progress=split_distance*(fraction/walk_fraction);stage='walk'
                else:
                    progress=split_distance+(1-split_distance)*(fraction-walk_fraction)/(1-walk_fraction);stage='run'
            else:progress=fraction;stage='walk' if walk_fraction==1 else 'run'
            # Ease the start over a few frames without restarting the clips.
            progress*=smooth(min(1,elapsed/.2))
            current=along_path(route,progress);distance+=(current-previous).xy.length
            walk=sources.sample('walk',1+(distance/source_distance['walk']*(sources.ends['walk']-1))%(sources.ends['walk']-1))
            run=sources.sample('run',1+(distance/source_distance['run']*(sources.ends['run']-1))%(sources.ends['run']-1))
            transition=smooth((fraction-walk_fraction+.045)/.09) if 0<walk_fraction<1 else 1-walk_fraction
            pose=blend_pose(walk,run,transition)
            pose=blend_pose(sources.sample('guard',1),pose,smooth(elapsed/.2))
            # Stop the running arm pump before reaching the open sash. Legs
            # keep their locomotion phase; the arms prepare for sill support.
            readiness=smooth((seconds-(vault_start-.3))/.3)
            guard=sources.sample('guard',1)
            ready=blend_pose(pose,guard,readiness)
            for name in pose:
                short=name.removeprefix(PREFIX)
                if any(short.startswith(side+part) for side in ('Left','Right')
                       for part in ('Shoulder','Arm','ForeArm','Hand')):
                    pose[name]=ready[name]
            last_approach_pose=pose
            actor.set_pose(pose);actor.root.location=current;pose_info=None
            bpy.context.view_layer.update()
            for side in ('Left','Right'):_arm_to_target(actor,side,contacts[side],readiness)
        else:
            phase=min(1,max(0,(seconds-vault_start)/vault_seconds))
            travel=curve(phase,[(0,-.85),(.2,-.4),(.34,-.37),(.5,.12),(.72,.7),(.87,1.03),(1,1.05)])
            height=curve(phase,[(0,0),(.12,.22),(.2,.375),(.34,.375),(.5,.5),(.72,.48),(.9,0),(1,0)])
            height+=(spec['position'][2]-.85)*smooth(phase/.22)*(1-smooth((phase-.74)/.16))
            current=center+forward*travel+approach_offset*(1-smooth(phase/.18));current.z=max(0,height)
            # Begin from the last locomotion pose, then enter a single FK vault.
            guard=sources.sample('guard',1)
            pose=blend_pose(last_approach_pose,guard,smooth(phase/.12)) if phase<.12 else guard
            actor.set_pose(pose);actor.root.location=current
            bpy.context.view_layer.update()
            for side in ('Left','Right'):_arm_to_target(actor,side,contacts[side],1-smooth(phase/.12))
            pose_info=apply_vault_pose(actor,phase,clearance={'hand_release_start':.34,'hand_release_end':.5,
                                                            'hand_targets':contacts,'tuck_release_start':.72})
            stage=pose_info['stage'] if phase<1 else 'settle'
            record['vault_phase']=phase
        bpy.context.view_layer.update()
        if pose_info is None:
            floor=float(vertices(actor)[:,2].min())
            if floor<-.012:actor.hip_offset(Vector((0,0,-.012-floor)));bpy.context.view_layer.update()
        record['root']=list(current);record['phase']=stage
        natural={k:point(actor.arm,k) for k in actor.goals}
        rotations={k:(actor.arm.matrix_world@actor.arm.pose.bones[PREFIX+k].matrix).to_quaternion() for k in actor.goals}
        desired={k:v.copy() for k,v in natural.items()};influences={k:0 for k in actor.goals}
        if pose_info is None:
            actor.source_feet=True
            desired.update(prepare_feet(actor,{},None,0,{},move_origins,record))
            for key in ('LeftFoot','RightFoot'):influences[key]=1
        else:
            hand_weight=pose_info['hand_ik_weight'];foot_weight=pose_info['foot_ik_weight']
            for side in ('Left','Right'):
                key=side+'Hand';tip=point(actor.arm,actor.tip_names[key])-natural[key]
                desired[key]=natural[key].lerp(contacts[side]-forward*tip.length,hand_weight)
                aimed=tip.rotation_difference(forward)@rotations[key]
                rotations[key]=rotations[key].slerp(aimed,hand_weight)
                influences[key]=hand_weight
                if hand_weight>=.999:record['contacts'].append({'limb':key,'target':list(contacts[side])})
                key=side+'Foot';desired[key]=natural[key].lerp(landing_goals[key],foot_weight)
                rotations[key]=rotations[key].slerp(landing_rotations[key],foot_weight);influences[key]=foot_weight
        bake_targets(actor,frame,desired,rotations,influences)
        props.set_angle(ident,-math.radians(spec['open_angle_deg']),frame)
        phase_records.append(record);previous=current.copy()
    for action in bpy.data.actions:
        if action.users:
            for fcurve in action_fcurves(action):
                for key in fcurve.keyframe_points:key.interpolation='LINEAR'
    setup_stage()
    # A window-relative side view separates both sides of the wall in screen
    # space, so the grounded actor remains visible after passing through it.
    camera_meshes=actor.meshes+props.focus_meshes()
    if single.get('camera'):
        bounds=setup_reference_camera(camera_meshes,single['camera'],{'A':actor},props)
        camera_quality=verify_reference_camera(camera_meshes,single['camera'],{'A':actor},props)
    else:
        bounds=setup_camera(camera_meshes,{'camera':{'azimuth_deg':spec['yaw_deg']}},{'A':actor})
        camera_quality=None
    quality=scene_quality(props,{'A':actor.arm},single['frame_count'])
    if camera_quality is not None:
        quality.update(camera_facts=bounds,camera_quality=camera_quality)
        quality['passed']=bool(quality['passed'] and camera_quality['passed'])
    inverse=np.array(entry['root'].matrix_world.inverted(),dtype=np.float64)
    skin_fail=[];ground_fail=[];flips=[];contact_reports=[];previous_rot={};crossing_frames=[]
    for record in phase_records:
        frame=record['frame'];scene.frame_set(frame);world=vertices(actor)
        if world[:,2].min()<-.045:ground_fail.append({'frame':frame,'minimum_skin_z_m':float(world[:,2].min())})
        failure=aperture_failures(world,inverse,spec,hands)
        if failure:skin_fail.append({'frame':frame,'kind':'aperture',**failure})
        local=world@inverse[:3,:3].T+inverse[:3,3]
        if np.any(np.abs(local[:,1])<.09):crossing_frames.append(frame)
        for failure in solid_failures(world,props,hands):skin_fail.append({'frame':frame,'kind':'solid',**failure})
        for joint in ('LeftForeArm','RightForeArm','LeftLeg','RightLeg','Spine'):
            q=actor.arm.pose.bones[PREFIX+joint].matrix.to_quaternion()
            if joint in previous_rot:
                angle=math.degrees(2*math.acos(min(1,abs(q.dot(previous_rot[joint])))))
                if angle>65:flips.append({'frame':frame,'joint':joint,'angle_deg':angle})
            previous_rot[joint]=q.copy()
        for contact in record['contacts']:
            error=(point(actor.arm,actor.tip_names[contact['limb']])-Vector(contact['target'])).length
            contact_reports.append({**contact,'frame':frame,'error_m':error,'passed':error<=.035})
    scene.frame_set(scene.frame_end);final_world=vertices(actor)
    landing={ 'passed':bool(final_world[:,2].min()>=-.045 and all(abs(point(actor.arm,s+'ToeBase').z)<.09 for s in ('Left','Right'))),
              'skin_min_z_m':float(final_world[:,2].min()),
              'toe_z_m':{s:point(actor.arm,s+'ToeBase').z for s in ('Left','Right')}}
    signed_start=(initial-center).dot(forward);signed_end=(final-center).dot(forward)
    outside=(final_world@inverse[:3,:3].T+inverse[:3,3])[:,1].min()
    traversal={'passed':bool(signed_start<-.7 and signed_end>.9 and outside>.1 and crossing_frames),
               'start_signed_distance_m':signed_start,'end_signed_distance_m':signed_end,
               'final_skin_min_outside_distance_m':float(outside),'crossing_frame_count':len(crossing_frames)}
    root_speeds=[(Vector(b['root'])-Vector(a['root'])).length*30 for a,b in zip(phase_records,phase_records[1:])]
    hand_coverage=all(any(c['limb']==side+'Hand' and c['passed'] for c in contact_reports) for side in ('Left','Right'))
    passed=quality['passed'] and not skin_fail and not ground_fail and not flips and all(c['passed'] for c in contact_reports)
    passed=bool(passed and hand_coverage and traversal['passed'] and landing['passed'] and max(root_speeds,default=0)<4.5)
    quality.update({'passed':passed,'kind':'single_window_vault','actor':'A','window_id':ident,
        'frame_count':single['frame_count'],'contacts':contact_reports,'phase_counts':dict(Counter(r['phase'] for r in phase_records)),
        'traversal':traversal,'landing':landing,'skin_clearance_failures':skin_fail[:40],
        'ground_failures':ground_fail[:30],'joint_flip_failures':flips[:30],
        'max_root_speed_m_s':max(root_speeds,default=0),'camera_bounds':bounds,
        'preconditions_applied':preconditions,'render_cutaway_wall_parts':hidden,
        'engine_sources_sha256':SOURCE_HASHES,
        'visual_review_required':True,'scope':'Deformed skin vertices and aperture slab, solid OBB interiors, full-frame body/prop bounds, support markers and grounded landing. Not a full continuous triangle/rigid-body physics solver.'})
    executed={**environment,'preconditions_applied':preconditions,'traversal_timing':{
        'approach_start':event['start'],'walk_run_transition':event['start']+approach_seconds*walk_fraction,
        'takeoff':vault_start,'settled_landing':vault_end,'event_end':event['end']}}
    atomic_json(folder/'executed_scene_plan.json',executed)
    atomic_json(folder/'quality.json',quality);atomic_json(folder/'trajectory.json',{'frames':phase_records})
    scene['single_plan_json']=json.dumps(single);scene['scene_plan_json']=json.dumps(environment)
    scene['window_vault_quality_json']=json.dumps(quality);scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(folder/'scene.blend'))
    print('WINDOW_VAULT_SCENE '+json.dumps({'passed':passed,'phase_counts':quality['phase_counts'],
        'skin_failures':len(skin_fail),'contact_failures':sum(not c['passed'] for c in contact_reports),
        'traversal':traversal,'landing':landing}),flush=True)
    return quality


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);path=Path(args.plan)
    raw=json.loads(path.read_text());quality=build(validate_single(raw,raw['job_id']),path.parent)
    if not quality['passed']:raise RuntimeError('翻窗的接触、净空、穿越或落地检查失败，查看quality.json')
