"""Actual original-robot composition/capture/reload/render evidence.

Run with Blender --background --python this_file -- [--render]. Writes only
new, unique proof folders; never touches historical user jobs or GUI state.
"""
import argparse
import hashlib
import json
import math
import subprocess
import sys
import uuid
from pathlib import Path

import bpy
from mathutils import Quaternion,Vector

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))
from agent_character import actor_ensure,resolve_actor,scene_actor_facts
from agent_motion import sample_motion
from agent_motion_controls import read_pose,set_pose,controls_ensure,controls_match,apply_targets
from agent_motion_timeline import register_event,motion_commit,timeline_info
from agent_motion_quality import motion_check,scene_motion_facts


def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def quaternion_curve_check(actor):
    from agent_motion_quality import _active_channelbags,rotation_delta_degrees
    arm=actor['armature'];ad=arm.animation_data
    curves={}
    for bag in _active_channelbags(ad):
        for curve in bag.fcurves:curves[(curve.data_path,curve.array_index)]=curve
    minimum_dot=1.;minimum_half_norm=1.;maximum_error=0.
    for bone in arm.pose.bones:
        path=bone.path_from_id('rotation_quaternion')
        if not all((path,i) in curves for i in range(4)):continue
        points=[curves[(path,i)].keyframe_points for i in range(4)]
        quaternions=[[float(points[i][key].co.y) for i in range(4)] for key in range(len(points[0]))]
        for first,second in zip(quaternions,quaternions[1:]):
            minimum_dot=min(minimum_dot,sum(a*b for a,b in zip(first,second)))
        for index in (0,len(quaternions)//2,max(0,len(quaternions)-2)):
            frame=float(points[0][index].co.x)
            bpy.context.scene.frame_set(math.floor(frame),subframe=.5)
            current=arm.pose.bones[bone.name].rotation_quaternion.copy()
            minimum_half_norm=min(minimum_half_norm,math.sqrt(sum(v*v for v in current)))
            following=min(index+1,len(quaternions)-1)
            expected=Quaternion(quaternions[index]).slerp(Quaternion(quaternions[following]),.5)
            maximum_error=max(maximum_error,rotation_delta_degrees(current,expected))
    bpy.context.scene.frame_set(1)
    return {'check':'continuous_quaternion_keys_and_fractional_pose','passed':minimum_dot>=-1e-6 and minimum_half_norm>.5 and maximum_error<.1,
            'min_adjacent_key_dot':minimum_dot,'min_half_frame_quaternion_norm':minimum_half_norm,
            'max_half_frame_orientation_error_deg':maximum_error}


def snapshot(job):
    result={}
    for info in scene_actor_facts(job)['actors']:
        actor=resolve_actor(job,info['actor_id'])
        for frame in (1,15,30,45,60,75,90):
            bpy.context.scene.frame_set(frame)
            depsgraph=bpy.context.evaluated_depsgraph_get()
            arm=actor['armature'].evaluated_get(depsgraph)
            root=actor['root'].evaluated_get(depsgraph)
            result[f"{info['actor_id']}:{frame}:root"]=[v for row in root.matrix_world for v in row]
            for bone in arm.pose.bones:
                result[f"{info['actor_id']}:{frame}:{bone.name}"]=[v for row in bone.matrix for v in row]
    return result


def setup_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene
    scene.frame_start=1;scene.frame_end=90;scene.render.fps=30
    scene.render.engine='BLENDER_WORKBENCH'
    scene.display.render_aa='OFF'
    scene.display.shading.light='STUDIO'
    scene.display.shading.color_type='MATERIAL'
    scene.display.shading.show_shadows=False
    scene.display.shading.show_cavity=False
    scene.render.resolution_x=640;scene.render.resolution_y=480;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'
    scene.world=bpy.data.worlds.new('HybridProofWorld');scene.world.color=(.2,.2,.2)
    camera_data=bpy.data.cameras.new('ProofCamera');camera=bpy.data.objects.new('ProofCamera',camera_data)
    scene.collection.objects.link(camera);scene.camera=camera
    camera.location=(7,-10,5)
    camera.rotation_euler=(Vector((0,0,1))-camera.location).to_track_quat('-Z','Y').to_euler()
    camera_data.type='ORTHO';camera_data.ortho_scale=7
    for name,location,energy,size in [('Key',(2,-4,6),1800,5),('Fill',(-4,-1,4),1100,4)]:
        data=bpy.data.lights.new(name,'AREA');data.energy=energy;data.shape='DISK';data.size=size
        light=bpy.data.objects.new(name,data);scene.collection.objects.link(light);light.location=location
        light.rotation_euler=(Vector((0,0,1))-light.location).to_track_quat('-Z','Y').to_euler()
    bpy.ops.mesh.primitive_plane_add(size=20,location=(0,0,-.01))
    bpy.context.object.name='ProofGround'
    return scene


def capture_proof(render=False):
    """Direct bpy + arbitrary non-WM constraints, followed by a library append."""
    from agent_motion_quality import animation_fingerprint
    scene=setup_scene();scene.frame_end=30
    job=uuid.uuid4().hex[:16]
    folder=PROJECT/'reports'/'robot_hybrid_20261014'/job;folder.mkdir(parents=True)
    atomic_json(PROJECT/'output'/'jobs'/job/'request.json',{'prompt':'同原机器人直接bpy与IK约束烘焙，然后追加库动作。',
                'origin':'robot_hybrid_capture_test','character_policy':'library_beta'})
    info=actor_ensure(job,'A');actor=resolve_actor(job,'A');arm=actor['armature'];root=actor['root']
    controls_ensure(job,'A');controls_match(job,'A')
    bpy.context.view_layer.update()
    target_position=arm.matrix_world @ arm.pose.bones[info['bone_roles']['right_hand']].head
    left=arm.pose.bones[info['bone_roles']['left_hand']]
    target=bpy.data.objects.new('DirectManualRotationTarget',None);scene.collection.objects.link(target)
    target.rotation_euler=(.2,.1,.3)
    constraint=left.constraints.new('COPY_ROTATION');constraint.name='DirectCustomOrientation'
    constraint.target=target;constraint.target_space='WORLD';constraint.owner_space='WORLD'
    for frame,amount in ((1,0),(10,1),(20,0)):
        scene.frame_set(frame)
        left.location=(amount*2,0,0);left.scale=(1+amount*.15,1,1)
        left.keyframe_insert('location',frame=frame);left.keyframe_insert('scale',frame=frame)
        root.location=(amount*.2,0,0);root.keyframe_insert('location',frame=frame)
        constraint.influence=amount;constraint.keyframe_insert('influence',frame=frame)
    expected={}
    for frame in range(1,21):
        scene.frame_set(frame);bpy.context.view_layer.update()
        expected[frame]=read_pose(arm,evaluated=True)
    before=animation_fingerprint(arm)
    preserved=False
    try:register_event(job,'A','unsafe_append',{'source':'library','motion_id':'mixamo.basic.walk','start_frame':21,'end_frame':30})
    except ValueError as exc:
        preserved=('capture=True' in str(exc) and animation_fingerprint(arm)==before)
    motion_commit(job,'A',1,20,capture=True,event_id='captured_existing_animation')
    error=0
    for frame in range(1,21):
        scene.frame_set(frame);actual=read_pose(arm,evaluated=True)
        for name,channels in expected[frame].items():
            for key,values in channels.items():
                # Quaternion sign may differ, compare the smaller equivalent error.
                differences=[abs(a-b) for a,b in zip(values,actual[name][key])]
                if key=='rotation_quaternion':differences=min(differences,[abs(a+b) for a,b in zip(values,actual[name][key])],key=max)
                error=max(error,max(differences))
    checks=[{'check':'unregistered_animation_append_rejected_without_mutation','passed':preserved},
            {'check':'all_actual_evaluated_channels_and_non_WM_constraints_baked','passed':error<1e-4,'max_local_channel_error':error}]
    register_event(job,'A','append_walk',{'source':'library','motion_id':'mixamo.basic.walk','start_frame':21,'end_frame':30,
                                        'root_strategy':'hold','blend_in_frames':3})
    motion_commit(job,'A',1,30)
    checks.append({'check':'captured_existing_event_preserved_when_appending_library','passed':len(timeline_info(job,'A')['events'])==2})
    controls_ensure(job,'A');matched=controls_match(job,'A')
    checks.append({'check':'matched_baked_controls_reactivated_at_zero_influence','passed':all(
        not arm.pose.bones[chain['middle_bone']].constraints[chain['ik_constraint']].mute and
        arm.pose.bones[chain['middle_bone']].constraints[chain['ik_constraint']].influence==0
        for chain in matched['chains'].values())})
    scene.frame_set(30);bpy.context.view_layer.update()
    position=arm.matrix_world @ arm.pose.bones[info['bone_roles']['right_hand']].head
    # Same existing rig, reachable IK move then smooth release, full body base held.
    register_event(job,'A','contact_overlay',{'source':'procedural','start_frame':21,'end_frame':30,
        'mask':['right_arm'],'targets':{'RightHand':{'keyframes':[
            {'frame':21,'position':list(position),'influence':0},
            {'frame':25,'position':list(position+Vector((.04,0,.04))),'influence':1},
            {'frame':30,'position':list(position),'influence':0}]}}})
    motion_commit(job,'A',1,30)
    scene.frame_set(25);bpy.context.view_layer.update()
    actual=arm.matrix_world @ arm.pose.bones[info['bone_roles']['right_hand']].head
    distance=(actual-position-Vector((.04,0,.04))).length
    checks.append({'check':'same_original_rig_IK_reachable_contact','passed':distance<.025,'bone_head_target_distance_m':distance})
    # Existing registered motion + a later direct bpy edit was the real Hermes
    # failure: ordinary commit formerly silently erased the new pointing keys.
    scene.frame_set(15)
    edited_bone=arm.pose.bones[info['bone_roles']['right_arm']]
    edited_bone.rotation_quaternion=edited_bone.rotation_quaternion @ Quaternion((1,0,0),.37)
    edited_bone.keyframe_insert('rotation_quaternion',frame=15)
    root.location+=Vector((.07,0,0));root.keyframe_insert('location',frame=15)
    bpy.context.view_layer.update()
    raw_pose=read_pose(arm,evaluated=True)
    raw_root=(list(root.location),list(root.rotation_quaternion),list(root.scale))
    fingerprints=(animation_fingerprint(arm),animation_fingerprint(root))
    recorded_timeline=timeline_info(job,'A')
    refused=False
    try:
        motion_commit(job,'A',1,30,events=[{'event_id':'must_not_register_on_failure','source':'library',
                      'motion_id':'mixamo.basic.walk','start_frame':1,'end_frame':30}])
    except ValueError as exc:
        refused='capture=True' in str(exc)
    preserved=(fingerprints==(animation_fingerprint(arm),animation_fingerprint(root)) and
               recorded_timeline==timeline_info(job,'A') and raw_pose==read_pose(arm,evaluated=True) and
               raw_root==(list(root.location),list(root.rotation_quaternion),list(root.scale)))
    checks.append({'check':'ordinary_commit_rejects_unregistered_bpy_edit_before_any_mutation','passed':refused and preserved})
    motion_commit(job,'A',1,30,capture=True,event_id='a_later_bpy_edit_registered')
    scene.frame_set(15);captured_pose=read_pose(arm,evaluated=True)
    capture_error=max(min(max(abs(a-b) for a,b in zip(values,captured_pose[name][channel])),
                          max(abs(a+b) for a,b in zip(values,captured_pose[name][channel])))
                      if channel=='rotation_quaternion' else max(abs(a-b) for a,b in zip(values,captured_pose[name][channel]))
                      for name,channels in raw_pose.items() for channel,values in channels.items())
    root_error=max(abs(a-b) for values,actual in zip(raw_root,(root.location,root.rotation_quaternion,root.scale)) for a,b in zip(values,actual))
    checks.append({'check':'explicit_capture_preserves_later_bpy_pose_and_root_edit','passed':capture_error<1e-4 and root_error<1e-5,
                   'max_root_channel_error':root_error,
                   'max_local_channel_error':capture_error})
    before_reload={}
    for frame in (1,10,20,25,30):
        scene.frame_set(frame);before_reload[frame]=read_pose(arm,evaluated=True)
    blend=folder/'single_original_robot_capture_and_append.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend));bpy.ops.wm.open_mainfile(filepath=str(blend),use_scripts=False)
    actor=resolve_actor(job,'A');reload_error=0
    for frame in before_reload:
        bpy.context.scene.frame_set(frame);pose=read_pose(actor['armature'],evaluated=True)
        reload_error=max(reload_error,max(abs(x-y) for name,channels in before_reload[frame].items()
                                         for channel,values in channels.items() for x,y in zip(values,pose[name][channel])))
    checks.append({'check':'capture_and_IK_independent_reload','passed':reload_error<1e-5,'max_channel_error':reload_error})
    checks.append(quaternion_curve_check(actor))
    video=None
    if render:
        images=folder/'frames';images.mkdir()
        bpy.context.scene.render.filepath=str(images/'frame_')
        bpy.ops.render.render(animation=True)
        video=folder/'single_original_robot_capture_and_append.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-framerate','30','-i',str(images/'frame_%04d.png'),
                        '-c:v','libx264','-pix_fmt','yuv420p','-y',str(video)],check=True)
        subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True)
        checks.append({'check':'single_original_robot_render_30_frames_and_decode','passed':len(list(images.glob('frame_*.png')))==30})
    result={'passed':all(c['passed'] for c in checks),'job_id':job,'checks':checks,
            'scene':str(blend.relative_to(PROJECT)),'scene_sha256':digest(blend),'motion_facts':scene_motion_facts(job),
            'video':str(video.relative_to(PROJECT)) if video else None,'video_sha256':digest(video) if video else None,
            'scope':'direct bpy channels, arbitrary constraint bake, existing motion preservation, same-rig IK proximity and reload',
            'naturalness_verified':False,'fight_contact_verified':False}
    atomic_json(folder/'capture_validation.json',result)
    print('ROBOT_CAPTURE_REPORT',folder/'capture_validation.json',flush=True)
    if not result['passed']:raise RuntimeError('Direct bpy/IK capture bounded validation failed')
    return result


def slot_proof():
    from agent_motion_quality import _action_facts,animation_fingerprint
    bpy.ops.wm.read_factory_settings(use_empty=True)
    data=bpy.data.armatures.new('ActualSlotRig');obj=bpy.data.objects.new('ActualSlotRig',data)
    bpy.context.scene.collection.objects.link(obj);bpy.context.view_layer.objects.active=obj;obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT');bone=data.edit_bones.new('Probe');bone.tail=(0,0,1)
    bpy.ops.object.mode_set(mode='OBJECT')
    action=bpy.data.actions.new('ActualMultislotAction')
    active_empty=action.slots.new(id_type='OBJECT',name='EmptyActualSlot')
    other=action.slots.new(id_type='OBJECT',name='OtherSlotWithMotion')
    layer=action.layers.new('Layer');strip=layer.strips.new(type='KEYFRAME')
    bag=strip.channelbag(other,ensure=True)
    curve=bag.fcurves.new(data_path='pose.bones["Probe"].location',index=0)
    curve.keyframe_points.insert(1,0);curve.keyframe_points.insert(2,3)
    obj.animation_data_create();obj.animation_data.action=action;obj.animation_data.action_slot=active_empty
    bpy.context.scene.frame_set(2)
    empty=_action_facts(obj);empty_hash=animation_fingerprint(obj)
    empty_pose=float(obj.pose.bones['Probe'].location.x)
    obj.animation_data.action_slot=other;bpy.context.scene.frame_set(1);bpy.context.scene.frame_set(2)
    populated=_action_facts(obj);populated_hash=animation_fingerprint(obj)
    populated_pose=float(obj.pose.bones['Probe'].location.x)
    old=float(curve.keyframe_points[-1].co.y);curve.keyframe_points[-1].co.y=4;curve.update()
    drift=animation_fingerprint(obj)!=populated_hash
    curve.keyframe_points[-1].co.y=old;curve.update()
    checks=[{'check':'inactive_slot_curves_do_not_certify_empty_actual_slot','passed':empty['curve_count']==0 and empty['pose_curve_count']==0 and abs(empty_pose)<1e-6},
            {'check':'selected_slot_actual_pose_curve_and_evaluated_motion','passed':populated['pose_curve_count']==1 and abs(populated_pose-3)<1e-6},
            {'check':'active_slot_fingerprint_differs_and_detects_value_edit','passed':empty_hash!=populated_hash and drift},
            {'check':'restored_curve_fingerprint_matches_original','passed':animation_fingerprint(obj)==populated_hash}]
    result={'passed':all(c['passed'] for c in checks),'checks':checks,'empty_actual_slot':empty,'populated_actual_slot':populated,
            'scope':'actual selected Blender Action Slot and channel-value drift; no character or semantic quality claim'}
    target=PROJECT/'reports'/'robot_hybrid_20261014'/('action_slot_'+uuid.uuid4().hex[:16]+'.json')
    atomic_json(target,result);print('ROBOT_SLOT_REPORT',target,flush=True)
    if not result['passed']:raise RuntimeError('Actual Action Slot validation failed')
    return result


def main():
    args=argparse.ArgumentParser();args.add_argument('--render',action='store_true');args.add_argument('--capture-only',action='store_true');args.add_argument('--slot-only',action='store_true')
    tail=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    options=args.parse_args(tail)
    if options.slot_only:slot_proof();return
    if options.capture_only:
        capture_proof(render=options.render);return
    scene=setup_scene()
    job=uuid.uuid4().hex[:16]
    folder=PROJECT/'reports'/'robot_hybrid_20261014'/job;folder.mkdir(parents=True)
    atomic_json(PROJECT/'output'/'jobs'/job/'request.json',
                {'prompt':'自动验证：原Beta机器人库动作与同骨架新动作混合，三人独立控制。',
                 'generation_contract_version':3,'execution_strategy':'hermes_mcp',
                 'character_policy':'library_beta','origin':'robot_hybrid_headless_test'})
    checks=[]
    # Same Beta asset, independent armatures. A crosses library -> new -> library;
    # B and C demonstrate independently timed masked gestures over library gait.
    for index,aid in enumerate(('A','B','C')):
        info=actor_ensure(job,aid,position=((index-1)*1.5,0,0))
        actor=resolve_actor(job,aid)
        pose_start=sample_motion(actor['armature'],'mixamo.basic.idle',0)['pose']
        first=sample_motion(actor['armature'],'mixamo.basic.walk',0)['pose']
        last=sample_motion(actor['armature'],'mixamo.basic.run',0)['pose']
        register_event(job,aid,'walking',{'source':'library','motion_id':'mixamo.basic.walk','start_frame':1,'end_frame':30,
                                         'root_strategy':'hold'})
        if aid=='A':
            middle=json.loads(json.dumps(pose_start))
            name=info['bone_roles']['right_arm']
            q=Quaternion(middle[name]['rotation_quaternion']) @ Quaternion((0,0,1),-.8)
            middle[name]['rotation_quaternion']=list(q)
            # Explicit non-Hips location and scale prove all authored channels survive.
            hand=info['bone_roles']['right_hand']
            middle[hand]['location']=[.03,0,0];middle[hand]['scale']=[1.04,1,1]
            register_event(job,aid,'invented_gesture',{'source':'procedural','intent':'右手抬起并稍微张开手掌',
                'start_frame':31,'end_frame':60,'root_strategy':'hold','blend_in_frames':5,
                'keyframes':[{'frame':31,'pose':first},{'frame':45,'pose':middle},{'frame':60,'pose':last}]})
            register_event(job,aid,'running',{'source':'library','motion_id':'mixamo.basic.run','start_frame':61,'end_frame':90,
                                             'blend_in_frames':5,'root_strategy':'hold'})
        else:
            register_event(job,aid,'running',{'source':'library','motion_id':'mixamo.basic.run','start_frame':31,'end_frame':90,
                                             'blend_in_frames':5,'root_strategy':'hold','loop':True,'fit':False})
            arm_name=info['bone_roles']['right_arm']
            arm_pose=pose_start[arm_name]
            raised=dict(arm_pose,rotation_quaternion=list(Quaternion(arm_pose['rotation_quaternion']) @ Quaternion((0,0,1),-.55)))
            register_event(job,aid,'wave',{'source':'procedural','intent':'边跑边挥右手','start_frame':15+index*10,
                 'end_frame':65+index*10,'mask':['right_arm'],'blend_in_frames':5,'blend_out_frames':5,
                 'keyframes':[{'frame':15+index*10,'pose':{arm_name:arm_pose}},
                              {'frame':40+index*10,'pose':{arm_name:raised}},
                              {'frame':65+index*10,'pose':{arm_name:arm_pose}}]})
        motion_commit(job,aid,1,90)
        checks.append({'check':'original_robot_identity','actor_id':aid,'passed':info['binding_verified']})
    checks.append({'check':'three_independent_robot_rigs','passed':len({resolve_actor(job,aid)['armature'].data.as_pointer() for aid in ('A','B','C')})==3})
    actor_a=resolve_actor(job,'A');scene.frame_set(45)
    hand=actor_a['info']['bone_roles']['right_hand']
    checks.append({'check':'non_hips_translation_scale_persisted','passed':
                   abs(actor_a['armature'].pose.bones[hand].location.x-.03)<1e-5 and
                   abs(actor_a['armature'].pose.bones[hand].scale.x-1.04)<1e-5})
    # Changing/replacing only A's gesture must not change B/C or duplicate actors.
    before_bc={key:value for key,value in snapshot(job).items() if not key.startswith('A:')}
    event=next(e for e in timeline_info(job,'A')['events'] if e['event_id']=='invented_gesture')
    register_event(job,'A',event['event_id'],event);motion_commit(job,'A',1,90)
    after_bc={key:value for key,value in snapshot(job).items() if not key.startswith('A:')}
    checks.append({'check':'local_event_repair_preserves_other_actors','passed':before_bc==after_bc})
    checks.append({'check':'repair_event_id_no_duplicate','passed':len(timeline_info(job,'A')['events'])==3})
    quality=motion_check(job,frame_start=1,frame_end=90,check_camera=True)
    checks.append({'check':'finite_original_skin_and_full_body_frame','passed':quality['passed']})
    before=snapshot(job)
    blend=folder/'three_original_robot_hybrid.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend),use_scripts=False)
    after=snapshot(job)
    error=max(abs(a-b) for key,value in before.items() for a,b in zip(value,after[key]))
    checks.append({'check':'headless_reload_same_pose_root','passed':error<1e-5,'max_matrix_error':error})
    checks.append({'check':'reload_real_robot_identity','passed':scene_actor_facts(job)['identity_verified']})
    checks.extend({**quaternion_curve_check(resolve_actor(job,aid)),'actor_id':aid} for aid in ('A','B','C'))
    facts=scene_motion_facts(job)
    checks.append({'check':'committed_actual_channel_fingerprints_match','passed':not facts['errors']})
    video=None
    if options.render:
        images=folder/'frames';images.mkdir()
        bpy.context.scene.render.filepath=str(images/'frame_')
        bpy.ops.render.render(animation=True)
        video=folder/'three_original_robot_hybrid.mp4'
        command=['ffmpeg','-hide_banner','-loglevel','error','-framerate','30','-i',str(images/'frame_%04d.png'),
                 '-c:v','libx264','-pix_fmt','yuv420p','-y',str(video)]
        subprocess.run(command,check=True)
        subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True)
        checks.append({'check':'render_90_frames_and_full_video_decode','passed':len(list(images.glob('frame_*.png')))==90})
    result={'passed':all(c['passed'] for c in checks),'job_id':job,'checks':checks,'quality':quality,'motion_facts':facts,
            'scene':str(blend.relative_to(PROJECT)),'scene_sha256':digest(blend),
            'video':str(video.relative_to(PROJECT)) if video else None,'video_sha256':digest(video) if video else None,
            'scope':'three original Beta identities, library/procedural/library, masked simultaneous gestures, actual channels, independence and reload',
            'naturalness_verified':False,'fight_contact_verified':False,'visual_review_required':True}
    atomic_json(folder/'validation.json',result)
    print('ROBOT_HYBRID_REPORT',folder/'validation.json',flush=True)
    if not result['passed']:raise RuntimeError('Robot hybrid bounded validation failed')


if __name__=='__main__':main()
