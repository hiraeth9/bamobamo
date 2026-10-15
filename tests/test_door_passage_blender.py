"""Independent saved/reloaded door-chain measurements on actual deformed skin.

Run with Blender. Optional --only names limits expensive scene builds; --reload
folder checks an existing fixture without rebuilding. No bar/hand collision
exemptions are used. These checks do not prove subjective animation naturalness.
"""
import argparse
from collections import Counter
import contextlib
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import sys
import traceback

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import duel_plan
from duel_plan import atomic_json
from duel_scene import point
from single_plan import validate_single
from scene_plan import validate_scene
from door_passage_scene import build, ENGINE_FILES

OUTPUT=ROOT/'output/verification/door_passage_regression'
REPORT=ROOT/'reports/door_passage_blender_regression_20261013.json'
# Isolate protocol job-directory checks from live service jobs.
duel_plan.JOBS=OUTPUT


def source_hashes():
    return {name:hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
            for name in ENGINE_FILES}


def fixture(name, duration=15, direction='outward', mode='chain', room=True,
            width=1.3, angle=80, yaw=0):
    body={'version':1,'duration_seconds':duration,
          'animations':['mixamo_fbx/basic/Idle.fbx','mixamo_fbx/basic/Walk.fbx']}
    if mode=='chain':
        events=[{'action':'open_door','start':0,'end':duration/3},
                {'action':'pass_door','start':duration/3,'end':duration*2/3,'direction':direction},
                {'action':'close_door','start':duration*2/3,'end':duration}]
    elif mode=='pass_close':
        events=[{'action':'pass_door','start':0,'end':duration*.65,'direction':direction},
                {'action':'close_door','start':duration*.65,'end':duration}]
    else:
        events=[{'action':'pass_door','start':0,'end':duration,'direction':direction}]
    for event in events:event.update(object='door_1',side='left')
    raw={'version':1,'duration_seconds':duration,'room':{'enabled':room,'size':[6,5,3]},
         'objects':[{'id':'door_1','type':'door','size':[width,.14,2.1],
                     'open_angle_deg':angle,'state':'closed' if mode=='chain' else 'open',
                     'position':[0,2.5 if room else 0,0],'yaw_deg':yaw}],
         'interactions':events}
    scene=validate_scene(raw,name,1,duration)
    if duration>15:
        atomic_json(duel_plan.job_directory(name)/'scene_plan.json',scene)
    return validate_single(body,name),scene


def indices(meshes):
    result={}
    for mesh in meshes:
        result[mesh.name]={}
        for region in ('LeftFoot','RightFoot','LeftHand','RightHand'):
            if region.endswith('Foot'):
                side=region[:-4]
                groups={g.index for g in mesh.vertex_groups
                        if g.name.startswith('mixamorig:'+side)
                        and any(p in g.name for p in ('Foot','Toe'))}
            else:
                groups={g.index for g in mesh.vertex_groups if g.name.startswith('mixamorig:'+region)}
            result[mesh.name][region]=[v.index for v in mesh.data.vertices
                                      if any(g.group in groups and g.weight>=.3 for g in v.groups)]
    return result


def world_skin(meshes,masks):
    all_world=[];regions={key:[] for key in ('LeftFoot','RightFoot','LeftHand','RightHand')}
    graph=bpy.context.evaluated_depsgraph_get()
    for mesh in meshes:
        evaluated=mesh.evaluated_get(graph)
        local=np.empty((len(evaluated.data.vertices),3),dtype=np.float64)
        evaluated.data.vertices.foreach_get('co',local.ravel())
        matrix=np.array(evaluated.matrix_world,dtype=np.float64)
        transformed=local@matrix[:3,:3].T+matrix[:3,3];all_world.append(transformed)
        for region in regions:
            selected=masks[mesh.name][region]
            if selected:regions[region].append(transformed[selected])
    return np.concatenate(all_world),{key:np.concatenate(value) for key,value in regions.items()}


def solid_hits(world,solids):
    failures=[]
    for solid in solids:
        inverse=np.array(solid.matrix_world.inverted(),dtype=np.float64)
        local=world@inverse[:3,:3].T+inverse[:3,3]
        box=np.array(solid.bound_box,dtype=np.float64);lower,upper=box.min(axis=0),box.max(axis=0)
        # Same physical four-mm surface tolerance as the project; every solid
        # is checked, including the held bar and its supports.
        margin=min(.004,float((upper-lower).min())*.2)
        mask=np.all((local>lower+margin)&(local<upper-margin),axis=1)
        if np.any(mask):failures.append({'solid':solid.name,'vertices':int(np.count_nonzero(mask))})
    return failures


def distance_to_bar(vertices,bar,target):
    # Restrict the measured hand patch to the real, saved contact anchor.
    near=vertices[np.linalg.norm(vertices-np.array(target),axis=1)<.10]
    if len(near)==0:return float('inf')
    inverse=np.array(bar.matrix_world.inverted(),dtype=np.float64)
    local=near@inverse[:3,:3].T+inverse[:3,3]
    box=np.array(bar.bound_box,dtype=np.float64)
    closest=np.maximum(box.min(axis=0),np.minimum(local,box.max(axis=0)))
    return float(np.linalg.norm(local-closest,axis=1).min())


def saved_surface_alpha(material):
    assert material.use_nodes and material.node_tree,'透明展示没有启用实际节点'
    outputs=[n for n in material.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
    assert len(outputs)==1 and outputs[0].inputs['Surface'].is_linked,'没有实际表面输出'
    shader=outputs[0].inputs['Surface'].links[0].from_node
    assert shader.type=='BSDF_PRINCIPLED','输出不是可独立测量的实际Principled表面'
    alpha=shader.inputs['Alpha']
    assert not alpha.is_linked,'无法用常量测量实际透明度'
    return float(alpha.default_value)


def reference_visibility(door_id,scene,room_expected):
    """Inspect saved render data, without treating bounds as optical visibility."""
    leaf=bpy.data.objects[door_id+'_leaf']
    assert leaf.get('prop_solid') and not leaf.hide_render,'实际门扇固体或可见渲染被移除'
    assert leaf.data.materials,'门扇缺少真实渲染材质'
    material=leaf.data.materials[0]
    actual=saved_surface_alpha(material);diffuse=float(material.diffuse_color[3])
    assert 0<actual<=.15 and 0<diffuse<=.15,'门扇透明度不足以保证参考展示可见性'
    assert material.surface_render_method=='DITHERED','门扇没有实际启用透明渲染'
    assert not scene.camera.hide_render,'相机不可渲染'
    walls=[obj for obj in scene.objects if obj.type=='MESH' and obj.parent
           and obj.parent.name=='Room_Cutaway' and obj.name!='Room_Floor']
    assert bool(walls)==room_expected,'保存房间墙体与原始room请求不一致'
    frames=[obj for obj in scene.objects if obj.type=='MESH'
            and obj.name.startswith(door_id+'_frame_')]
    assert len(frames)==3,'实际固定门框缺少一侧立柱或顶框'
    structure_details={'walls':[],'door_frame':[]}
    for label,objects in (('walls',walls),('door_frame',frames)):
        for obj in objects:
            assert obj.get('prop_solid'),'实体墙面/门框被取消碰撞'
            item={'name':obj.name,'collision_solid_retained':True,'render_cutaway':bool(obj.hide_render)}
            if not obj.hide_render:
                assert obj.data.materials,'显示墙面/门框缺少真实材质'
                mat=obj.data.materials[0];alpha=saved_surface_alpha(mat)
                assert 0<alpha<=.05 and 0<float(mat.diffuse_color[3])<=.05,'显示墙面/门框仍可能遮挡人物'
                assert mat.surface_render_method=='DITHERED','墙面/门框没有启用透明渲染'
                item.update(actual_surface_alpha=alpha,render_method=mat.surface_render_method)
            structure_details[label].append(item)
    return {'actual_surface_alpha':actual,'diffuse_alpha':diffuse,
            'render_method':material.surface_render_method,'leaf_solid_retained':True,
            'wall_solids':structure_details['walls'],'door_frame_solids':structure_details['door_frame'],
            'scope':'Saved output-connected shader and retained collision solid. Projection margins and material alpha do not independently prove freedom from all optical occlusion; separate rendered visual review is required.'}


def subframe_root_continuity(scene,root,trajectory):
    """Raw Euler and evaluated quarter frames must preserve the short turn."""
    failures=[];max_step=max_error=max_rate=0.
    for first,second in zip(trajectory,trajectory[1:]):
        a=float(first['yaw']);b=float(second['yaw']);step=abs(math.degrees(b-a))
        max_step=max(max_step,step)
        if step*scene.render.fps>125:
            failures.append({'frame':second['frame'],'kind':'raw_euler_turn_jump','step_deg':step})
        old=a
        for fraction in (.25,.5,.75):
            scene.frame_set(first['frame'],subframe=fraction);actual=float(root.rotation_euler.z)
            expected=a+(b-a)*fraction;error=abs(math.degrees(actual-expected))
            max_error=max(max_error,error)
            rate=abs(math.degrees(actual-old))*4*scene.render.fps;max_rate=max(max_rate,rate)
            if error>.001 or rate>125:
                failures.append({'frame':first['frame'],'subframe':fraction,'kind':'subframe_turn_continuity',
                                 'error_deg':error,'yaw_rate_deg_s':rate})
            old=actual
    scene.frame_set(trajectory[-1]['frame'])
    return {'passed':not failures,'max_raw_euler_frame_step_deg':max_step,
            'max_evaluated_subframe_interpolation_error_deg':max_error,
            'max_evaluated_subframe_yaw_rate_deg_s':max_rate,
            'subframes_per_interval':3,'failures':failures}


def reload_measurements(folder):
    folder=Path(folder);single=json.loads((folder/'single_plan.json').read_text())
    request=json.loads((folder/'scene_plan.json').read_text())
    executed=json.loads((folder/'executed_scene_plan.json').read_text())
    trajectory=json.loads((folder/'trajectory.json').read_text())['frames']
    bpy.ops.wm.open_mainfile(filepath=str(folder/'scene.blend'))
    scene=bpy.context.scene;root=bpy.data.objects['A_Space'];arm=bpy.data.objects['A_Rig']
    meshes=[o for o in scene.objects if o.type=='MESH' and o.get('actor_id')=='A']
    solids=[o for o in scene.objects if o.type=='MESH' and o.get('prop_solid')]
    assert meshes and solids,'缺少实际人物蒙皮或门/墙实体'
    assert scene.frame_end==single['frame_count']==len(trajectory),'保存场景/轨迹帧数不一致'
    assert scene.render.fps==30,'门链采样帧率变化'
    door_id=next(e['object'] for e in executed['interactions'] if e['action']=='pass_door')
    door=bpy.data.objects['Prop_'+door_id];inverse=np.array(door.matrix_world.inverted(),dtype=np.float64)
    presentation=reference_visibility(door_id,scene,request['room']['enabled'])
    requested_events=request['interactions'];events=executed['interactions']
    assert [e['action'] for e in requested_events]==[e['action'] for e in events],'动作被删去或改变顺序'
    assert single['duration_seconds']==executed['duration_seconds'],'执行器延长了用户时长'
    assert all(e['side']==r['side'] for e,r in zip(events,requested_events)),'执行器暗改接触手'
    for bone in arm.pose.bones:
        for constraint in bone.constraints:
            if constraint.type=='IK':assert not constraint.use_stretch,'IK开启了骨骼拉伸'
    masks=indices(meshes);previous_toes={};previous_joints={};previous_root=None;previous_yaw=None
    max_toe_error=max_toe_slide=max_hand_error=max_joint_step=max_speed=max_yaw_rate=0.
    min_skin=float('inf');min_camera=1.;max_bar_gap=0.;contact_counts=Counter();contacts=[]
    failures=[];pass_samples=[];root_crossings=[];previous_pass_root=None;terminal=[];previous_phase=None;phase_deltas=[]
    for record in trajectory:
        frame=record['frame'];scene.frame_set(frame);bpy.context.view_layer.update()
        world,regions=world_skin(meshes,masks);lowest=float(world[:,2].min());min_skin=min(min_skin,lowest)
        if lowest<-.045:failures.append({'frame':frame,'kind':'ground','minimum_z_m':lowest})
        for hit in solid_hits(world,solids):failures.append({'frame':frame,'kind':'skin_solid',**hit})
        actual=root.matrix_world.translation.copy()
        if (actual-Vector(record['root'])).length>.001:failures.append({'frame':frame,'kind':'root_record_mismatch'})
        yaw=float(root.rotation_euler.z)
        if previous_root is not None:
            speed=(actual-previous_root).xy.length*30;max_speed=max(max_speed,speed)
            rate=math.degrees(abs(math.atan2(math.sin(yaw-previous_yaw),math.cos(yaw-previous_yaw))))*30
            max_yaw_rate=max(max_yaw_rate,rate)
            if speed>1.70:failures.append({'frame':frame,'kind':'root_speed','speed':speed})
            if rate>125:failures.append({'frame':frame,'kind':'root_yaw_rate','rate':rate})
        previous_root,previous_yaw=actual.copy(),yaw
        for joint in ('Hips','Spine','Spine1','LeftUpLeg','RightUpLeg','LeftLeg','RightLeg',
                      'LeftFoot','RightFoot','LeftArm','RightArm','LeftForeArm','RightForeArm','LeftHand','RightHand'):
            rotation=arm.pose.bones['mixamorig:'+joint].matrix.to_quaternion()
            if joint in previous_joints:
                step=math.degrees(2*math.acos(min(1.,abs(rotation.dot(previous_joints[joint])))))
                max_joint_step=max(max_joint_step,step)
                if step>65:failures.append({'frame':frame,'kind':'joint_jump','joint':joint,'angle_deg':step})
            previous_joints[joint]=rotation.copy()
        current_toes={}
        for key,target in record['toe_stance'].items():
            toe=point(arm,key.split(':')[-1]);target=Vector(target);error=(toe-target).length
            max_toe_error=max(max_toe_error,error)
            if error>.0165:failures.append({'frame':frame,'kind':'toe_target','error_m':error})
            if key in previous_toes:
                old_target,old_toe=previous_toes[key]
                if (old_target-target).length<=1e-5:
                    slide=(toe-old_toe).length;max_toe_slide=max(max_toe_slide,slide)
                    if slide>.0165:failures.append({'frame':frame,'kind':'locked_toe_slide','error_m':slide})
            current_toes[key]=(target.copy(),toe.copy())
        previous_toes=current_toes
        phase=record['gait_cycle']+record['gait_phase']
        if previous_phase is not None:
            delta=phase-previous_phase;phase_deltas.append(delta)
            if delta<-.00001 or delta>.18:failures.append({'frame':frame,'kind':'gait_phase_restart','delta':delta})
        previous_phase=phase
        lower,upper=world.min(axis=0),world.max(axis=0)
        for xyz in itertools.product(*zip(lower,upper)):
            uv=world_to_camera_view(scene,scene.camera,Vector(xyz));margin=min(uv.x,1-uv.x,uv.y,1-uv.y)
            min_camera=min(min_camera,margin)
            if margin<.04 or uv.z<=0:failures.append({'frame':frame,'kind':'body_camera_bounds'});break
        contact=record.get('contact')
        if contact and contact['weight']>=.999:
            label='contact_front' if contact['door_side']=='inside' else 'contact_back'
            anchor=bpy.data.objects[door_id+'_'+label+'_anchor'].matrix_world.translation.copy()
            if (anchor-Vector(contact['target'])).length>1e-5:failures.append({'frame':frame,'kind':'contact_target_not_real_anchor'})
            error=(point(arm,contact['tip'])-anchor).length;max_hand_error=max(max_hand_error,error)
            if error>.0495:failures.append({'frame':frame,'kind':'hand_contact','error_m':error})
            bar=bpy.data.objects[door_id+'_grip_bar_'+('front' if contact['door_side']=='inside' else 'back')]
            gap=distance_to_bar(regions[contact['hand']],bar,anchor);max_bar_gap=max(max_bar_gap,gap)
            if gap>.02:failures.append({'frame':frame,'kind':'hand_skin_bar_gap','gap_m':gap})
            contact_counts[record['event']]+=1
            contacts.append({'frame':frame,'event':record['event'],'side':contact['door_side'],
                             'hand':contact['hand'],'marker_error_m':error,'actual_hand_skin_gap_m':gap})
            local_root=door.matrix_world.inverted()@actual
            if (contact['door_side']=='inside' and local_root.y>=-.20) or (
                contact['door_side']=='outside' and local_root.y<=.20):
                failures.append({'frame':frame,'kind':'operation_wrong_frame_side'})
        if record['action']=='pass_door':
            local=world@inverse[:3,:3].T+inverse[:3,3]
            pass_samples.append({'frame':frame,'min_y':float(local[:,1].min()),'max_y':float(local[:,1].max())})
            local_root=door.matrix_world.inverted()@actual
            if previous_pass_root is not None and previous_pass_root.y*local_root.y<0:
                root_crossings.append({'frame':frame,'x':local_root.x,'from_y':previous_pass_root.y,'to_y':local_root.y})
            if abs(local_root.y)>1e-5:previous_pass_root=local_root.copy()
            expected_angle=next(o['open_angle_deg'] for o in executed['objects'] if o['id']==door_id)
            actual_angle=abs(math.degrees(bpy.data.objects[door_id+'_hinge'].rotation_euler.z))
            if abs(actual_angle-expected_angle)>.1:failures.append({'frame':frame,'kind':'door_angle_during_passage','angle_deg':actual_angle})
        if frame>=single['frame_count']-2:
            terminal.append({'frame':frame,'locks':sorted(record['toe_stance']),
                             'feet_z':{side:float(regions[side+'Foot'][:,2].min()) for side in ('Left','Right')},
                             'toes':{side:list(point(arm,side+'ToeBase')) for side in ('Left','Right')}})
    for event in events:
        if event['action']!='pass_door' and contact_counts[event['id']]<15:
            failures.append({'kind':'missing_continuous_contact','event':event['id'],'frames':contact_counts[event['id']]})
    passage=next(e for e in events if e['action']=='pass_door');outward=passage['direction']=='outward'
    entered=any(p['max_y']<-.02 if outward else p['min_y']>.02 for p in pass_samples)
    exited=any(p['min_y']>.02 if outward else p['max_y']<-.02 for p in pass_samples)
    if not entered or not exited:failures.append({'kind':'whole_skin_passage','entered':entered,'exited':exited})
    door_width=next(o['size'][0] for o in executed['objects'] if o['id']==door_id)
    if len(root_crossings)!=1 or any(abs(c['x'])>=door_width/2-.055 or
        (c['to_y']<c['from_y'])==outward for c in root_crossings):
        failures.append({'kind':'root_did_not_cross_fixed_aperture_once_in_order','crossings':root_crossings})
    for state in terminal:
        if len(state['locks'])!=2 or any(z<-.025 or z>.03 for z in state['feet_z'].values()):
            failures.append({'kind':'terminal_feet','frame':state['frame']})
    terminal_slide=max(((Vector(b['toes'][s])-Vector(a['toes'][s])).length
                        for a,b in zip(terminal,terminal[1:]) for s in ('Left','Right')),default=0.)
    if terminal_slide>.01:failures.append({'kind':'terminal_toe_slide','error_m':terminal_slide})
    for bone in arm.pose.bones:
        if 'ForeArm' in bone.name or bone.name.endswith('Hand'):
            for constraint in bone.constraints:
                if constraint.type in ('IK','COPY_ROTATION') and constraint.influence>.001:
                    failures.append({'kind':'terminal_hand_not_released','bone':bone.name})
    final_angle=abs(math.degrees(bpy.data.objects[door_id+'_hinge'].rotation_euler.z))
    if events[-1]['action']=='close_door' and final_angle>.1:failures.append({'kind':'door_not_closed','angle_deg':final_angle})
    if events[-1]['action']=='pass_door' and abs(final_angle-executed['objects'][0]['open_angle_deg'])>.1:
        failures.append({'kind':'opened_door_moved_during_passage'})
    continuity=subframe_root_continuity(scene,root,trajectory)
    failures.extend(continuity['failures'])
    result={'passed':not failures,'frame_count':len(trajectory),'max_toe_target_error_m':max_toe_error,
            'max_continuous_locked_toe_slide_m':max_toe_slide,'max_actual_hand_marker_error_m':max_hand_error,
            'max_actual_hand_skin_bar_gap_m':max_bar_gap,'minimum_actual_skin_z_m':min_skin,
            'max_actual_joint_step_deg':max_joint_step,'max_actual_root_speed_m_s':max_speed,
            'max_actual_yaw_rate_deg_s':max_yaw_rate,'minimum_camera_margin':min_camera,
            'whole_body_entered':entered,'whole_body_exited':exited,'root_aperture_crossings':root_crossings,'contact_counts':dict(contact_counts),
            'terminal_feet':terminal,'terminal_slide_m':terminal_slide,'final_door_angle_deg':final_angle,
            'reference_presentation':presentation,'subframe_root_continuity':continuity,
            'max_gait_phase_delta':max(phase_deltas,default=0.),'contacts':contacts,
            'failures':failures[:100],'failure_count':len(failures),
            'scope':'Every saved/reloaded frame: all actual skin vs all saved solids with 4mm surface tolerance; true bar anchors plus hand skin proximity; continuous gait/toe locks; whole-body crossing and camera projection bounds. Actual saved leaf/wall render alpha, retained collision solids and quarter-frame root turns checked. No hand/bar exemptions. Optical visibility and subjective naturalness require a separate rendered review.'}
    atomic_json(folder/'independent_door_quality.json',result)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--only');parser.add_argument('--reload')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if args.reload:
        result=reload_measurements(args.reload);print(json.dumps(result,ensure_ascii=False));return not result['passed']
    selected=set(args.only.split(',')) if args.only else None
    cases=[('outward_open_pass_close',20,'outward','chain',True,0),
           ('inward_open_pass_close',20,'inward','chain',True,0),
           ('already_open_pass_only',6,'outward','pass',True,0),
           ('already_open_inward_pass_only',6,'inward','pass',True,0),
           ('already_open_pass_then_close',12,'outward','pass_close',True,0),
           ('rotated_freestanding_chain',20,'outward','chain',False,35)]
    results=[];before=source_hashes()
    for name,duration,direction,mode,room,yaw in cases:
        if selected is not None and name not in selected:continue
        folder=OUTPUT/name;folder.mkdir(parents=True,exist_ok=True)
        try:
            single,scene=fixture(name,duration,direction,mode,room,yaw=yaw)
            atomic_json(folder/'single_plan.json',single);atomic_json(folder/'scene_plan.json',scene)
            original={f:hashlib.sha256((folder/f).read_bytes()).hexdigest() for f in ('single_plan.json','scene_plan.json')}
            with contextlib.redirect_stdout(io.StringIO()):quality=build(single,folder)
            reloaded=reload_measurements(folder)
            assert original=={f:hashlib.sha256((folder/f).read_bytes()).hexdigest() for f in original},'执行器改写原请求计划'
            assert quality['engine_sources_sha256']==before,'scene来源哈希与本轮模块不一致'
            assert quality['passed'] and reloaded['passed'],'执行器或独立重载测量拒绝'
            result={'name':name,'passed':True,'fixture':str(folder.relative_to(ROOT)),
                    'quality':quality,'independent':reloaded,'source_plans_unchanged':True}
        except Exception as error:
            result={'name':name,'passed':False,'error':str(error),'traceback':traceback.format_exc(),
                    'fixture':str(folder.relative_to(ROOT))}
            for f in ('quality.json','independent_door_quality.json'):
                if (folder/f).exists():result[f]=json.loads((folder/f).read_text())
        results.append(result);atomic_json(REPORT,{'passed':all(r['passed'] for r in results),'cases':results,
            'engine_sources_sha256':before,'sources_unchanged_during_run':before==source_hashes(),
            'scope':'Saved and independently reloaded Blender scenes, all skin solids/hand contact/toe locks/camera; subjective naturalness requires separate visual review.'})
        print('DOOR_TEST '+json.dumps({'name':name,'passed':result['passed'],'error':result.get('error')},ensure_ascii=False),flush=True)
    # Conservative aperture refusal is meaningful before any animation bake.
    for name,width,angle in [('reject_explicit_narrow_door',1.2,80),('reject_small_open_angle',1.3,50)]:
        if selected is not None and name not in selected:continue
        folder=OUTPUT/name;folder.mkdir(parents=True,exist_ok=True)
        try:
            single,scene=fixture(name,width=width,angle=angle)
            atomic_json(folder/'single_plan.json',single);atomic_json(folder/'scene_plan.json',scene)
            try:
                with contextlib.redirect_stdout(io.StringIO()):build(single,folder)
            except ValueError as error:
                assert any(t in str(error) for t in ('净宽','净空','门洞')),'拒绝原因不是门洞净空'
                result={'name':name,'passed':True,'expected_refusal':str(error)}
            else:raise AssertionError('不满足真实门洞净空却接受计划')
        except Exception as error:result={'name':name,'passed':False,'error':str(error),'traceback':traceback.format_exc()}
        results.append(result);atomic_json(REPORT,{'passed':all(r['passed'] for r in results),'cases':results,
            'engine_sources_sha256':before,'sources_unchanged_during_run':before==source_hashes()})
        print('DOOR_TEST '+json.dumps(result,ensure_ascii=False),flush=True)
    for name in ('reject_blocked_aperture','reject_exact_insufficient_time'):
        if selected is not None and name not in selected:continue
        folder=OUTPUT/name;folder.mkdir(parents=True,exist_ok=True)
        try:
            single,scene=fixture(name,duration=9 if 'time' in name else 15)
            if name=='reject_blocked_aperture':
                scene['objects'].append({'id':'box_1','type':'box','size':[.8,.8,1.2],
                                         'position':[0,2.0,0]})
                scene=validate_scene(scene,name,1,single['duration_seconds'])
                reasons=('被真实','阻挡','门洞')
            else:
                scene['timing_policy']='exact';reasons=('时刻不足','至少需要','不足')
            atomic_json(folder/'single_plan.json',single);atomic_json(folder/'scene_plan.json',scene)
            try:
                with contextlib.redirect_stdout(io.StringIO()):build(single,folder)
            except ValueError as error:
                assert any(t in str(error) for t in reasons),'拒绝原因不是预期物理/时间约束'
                result={'name':name,'passed':True,'expected_refusal':str(error)}
            else:raise AssertionError('阻挡门洞/不足时间却接受计划')
        except Exception as error:result={'name':name,'passed':False,'error':str(error),'traceback':traceback.format_exc()}
        results.append(result);atomic_json(REPORT,{'passed':all(r['passed'] for r in results),'cases':results,
            'engine_sources_sha256':before,'sources_unchanged_during_run':before==source_hashes()})
        print('DOOR_TEST '+json.dumps(result,ensure_ascii=False),flush=True)
    unchanged=before==source_hashes()
    atomic_json(REPORT,{'passed':bool(results) and all(r['passed'] for r in results) and unchanged,
        'completed':True,'cases':results,'engine_sources_sha256':before,
        'sources_unchanged_during_run':unchanged,
        'test_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':'Saved and independently reloaded Blender scenes; all actual skin vs every collision solid, hand marker and real bar skin contact, continuous toe locks, ordered aperture passage, terminal feet, leaf/wall material-output alpha with original solids retained, raw Euler and evaluated quarter-frame turn continuity. Separate rendered review is required for optical visibility and subjective naturalness.'})
    return bool(not results or any(not r['passed'] for r in results) or not unchanged)


if __name__=='__main__':
    if main():raise RuntimeError('门链Blender独立回归失败；查看报告及原始fixture')
