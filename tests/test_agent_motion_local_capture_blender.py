"""Actual local-capture range preservation on the unchanged original Beta.

Headless only. Full integer-frame bones, deformed skin and root comparisons;
no GUI, historical user-job mutation or visual-naturalness certification.
"""
import sys,json,uuid,hashlib,time
from pathlib import Path
import bpy
import numpy as np
from mathutils import Quaternion,Vector
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))
sys.path.insert(0,str(PROJECT/'tests'))
from test_robot_hybrid_blender import setup_scene,atomic_json,digest,quaternion_curve_check
from agent_character import actor_ensure,resolve_actor
from agent_motion_controls import read_pose
from agent_motion_timeline import register_event,motion_commit,timeline_info,validate_event
from agent_motion_quality import animation_fingerprint,scene_motion_facts


def snapshot(actor,frames):
    result={}
    for frame in frames:
        bpy.context.scene.frame_set(frame);deps=bpy.context.evaluated_depsgraph_get()
        arm=actor['armature'].evaluated_get(deps);root=actor['root'].evaluated_get(deps)
        bones=np.asarray([list(arm.matrix_world@pb.matrix) for pb in arm.pose.bones],dtype=np.float64)
        skin=[]
        for obj in actor['meshes']:
            eo=obj.evaluated_get(deps);mesh=eo.to_mesh()
            try:
                values=np.empty(len(mesh.vertices)*3,dtype=np.float32);mesh.vertices.foreach_get('co',values)
                mat=np.asarray(eo.matrix_world,dtype=np.float64)
                skin.append(np.einsum('ij,nj->ni',mat[:3,:3],values.reshape((-1,3)).astype(np.float64),optimize=False)+mat[:3,3])
            finally:eo.to_mesh_clear()
        result[frame]={'bones':bones,'skin':np.concatenate(skin),'root':np.asarray(root.matrix_world,dtype=np.float64)}
    return result


def compare(first,second,frames):
    return {key:max(float(np.max(np.abs(first[f][key]-second[f][key]))) for f in frames) for key in ('bones','skin','root')}


def preserved(error):return error['bones']<8e-6 and error['skin']<8e-6 and error['root']<1e-6


def final_layers_proof(expect_duplicate=False,auto_pole_full=False,auto_pole_partial=False):
    """Final captures must not replay an included .5 FK layer or .4 IK layer."""
    scene=setup_scene();scene.frame_end=30;job=uuid.uuid4().hex[:16]
    folder=PROJECT/'reports'/'robot_hybrid_20261014'/('final_layers_'+job);folder.mkdir(parents=True)
    atomic_json(PROJECT/'output'/'jobs'/job/'request.json',{'prompt':'实际捕获已含半权重FK与IK的最终姿态，禁止重复合成；窗口外保持原样、更高priority新层仍可用。','origin':'final_layers_capture_regression','character_policy':'library_beta'})
    info=actor_ensure(job,'A');actor=resolve_actor(job,'A');arm=actor['armature'];root=actor['root']
    right=info['bone_roles']['right_arm'];initial=read_pose(arm,evaluated=True)
    q=Quaternion(initial[right]['rotation_quaternion'])@Quaternion((1,0,0),.4)
    from agent_motion_controls import controls_ensure,controls_match
    pole=None
    if not auto_pole_full and not auto_pole_partial:
        controls=controls_ensure(job,'A');controls_match(job,'A',chain='LeftHand')
        pole=list(bpy.data.objects[controls['chains']['LeftHand']['pole_object']].location)
    bpy.context.view_layer.update();left_position=arm.matrix_world@arm.pose.bones[info['bone_roles']['left_hand']].head
    register_event(job,'A','idle',{'source':'library','motion_id':'mixamo.basic.idle','start_frame':1,'end_frame':30,'root_strategy':'hold'})
    register_event(job,'A','half_right_FK',{'source':'procedural','start_frame':1,'end_frame':30,'mask':[right],'weight':.5,
        'keyframes':[{'frame':1,'pose':{right:{'rotation_quaternion':list(q)}}},{'frame':30,'pose':{right:{'rotation_quaternion':list(q)}}}]})
    register_event(job,'A','partial_left_IK',{'source':'procedural','start_frame':1,'end_frame':30,'mask':['left_arm'],
        'targets':{'LeftHand':{'keyframes':[{'frame':1,'position':list(left_position+Vector((.02,0,.08))),**({'pole':pole} if pole is not None else {}),'influence':.4},
                                             {'frame':30,'position':list(left_position+Vector((.02,0,.08))),**({'pole':pole} if pole is not None else {}),'influence':.4}]}}})
    motion_commit(job,'A',1,30);expected=snapshot(actor,range(1,31))
    result=motion_commit(job,'A',1 if auto_pole_full else 11,30 if auto_pole_full else 20,capture=True,event_id='actual_final_segment');actual=snapshot(actor,range(1,31))
    if auto_pole_full:
        error=compare(expected,actual,range(1,31))
        checks=[{'check':'automatic_pole_original_actual_FK_IK_preserved_by_full_final_pose_capture','passed':preserved(error),'errors':error},
                {'check':'old_automatic_pole_targets_are_not_recomputed_under_full_final_pose','passed':not result['target_diagnostics']}]
        blend=folder/'automatic_pole_full_final_capture.blend';bpy.ops.wm.save_as_mainfile(filepath=str(blend));bpy.ops.wm.open_mainfile(filepath=str(blend),use_scripts=False)
        actor=resolve_actor(job,'A');reloaded=snapshot(actor,range(1,31));reload_error=compare(actual,reloaded,range(1,31))
        checks.append({'check':'automatic_pole_full_final_capture_reload_all_bones_skin_root','passed':preserved(reload_error),'errors':reload_error})
        report={'passed':all(c['passed'] for c in checks),'job_id':job,'checks':checks,'capture_commit':result,'scene':str(blend.relative_to(PROJECT)),
                'source_sha256':digest(PROJECT/'scripts/agent_motion_timeline.py'),'test_sha256':digest(Path(__file__)),
                'automatic_pole_partial_capture_outside_window_verified':False,
                'scope':'All 30 frames of actual original automatic-pole FK/IK final channels are captured and preserved without recomputing old layers. Does not certify partial capture outside-window automatic-pole reconstruction; pre-fix evidence includes 2.36cm outside drift.'}
        atomic_json(folder/'automatic_pole_full_validation.json',report);print('AUTOMATIC_POLE_FULL_CAPTURE',folder/'automatic_pole_full_validation.json',json.dumps(checks),flush=True)
        if not report['passed']:raise RuntimeError('Automatic-pole full final-pose preservation failed')
        return
    inside=compare(expected,actual,range(11,21));outside=compare(expected,actual,[*range(1,11),*range(21,31)])
    if expect_duplicate:
        report={'expected_issue_reproduced':inside['skin']>.001 and preserved(outside),'job_id':job,'inside_errors':inside,'outside_errors':outside,
                'source_sha256':digest(PROJECT/'scripts/agent_motion_timeline.py'),'scope':'Pre-fix actual half-weight FK plus partial-influence IK final capture repeats original layers, full bones/skin/root measured.'}
        atomic_json(folder/'before_fix.json',report);print('FINAL_LAYERS_BEFORE_FIX',folder/'before_fix.json',json.dumps(report),flush=True)
        if not report['expected_issue_reproduced']:raise RuntimeError('Expected actual duplicate layer regression was not reproduced')
        return
    checks=[{'check':'actual_captured_final_pose_does_not_double_apply_original_half_weight_FK_or_IK','passed':preserved(inside),'inside_errors':inside},
            {'check':'original_overlay_and_targets_still_apply_outside_capture_window','passed':preserved(outside),'outside_errors':outside}]
    if auto_pole_partial:
        scene.frame_set(24);futureq=arm.pose.bones[right].rotation_quaternion@Quaternion((0,1,0),.35)
        registered=register_event(job,'A','future_masked_edit',{'source':'procedural','start_frame':22,'end_frame':27,'mask':['right_arm'],'weight':.6,
            'keyframes':[{'frame':22,'pose':{right:{'rotation_quaternion':list(futureq)}}},{'frame':27,'pose':{right:{'rotation_quaternion':list(futureq)}}}]})
        motion_commit(job,'A',22,27);future=snapshot(actor,range(1,31))
        from agent_motion_timeline import _mask
        affected=_mask(arm,{'mask':['right_arm']});names=[bone.name for bone in arm.pose.bones]
        other_bone_indices=[i for i,name in enumerate(names) if name not in affected]
        unaffected_skin=[];offset=0
        for obj in actor['meshes']:
            group_names={group.index:group.name for group in obj.vertex_groups}
            unaffected_skin.extend(offset+vertex.index for vertex in obj.data.vertices if sum(group.weight for group in vertex.groups if group_names[group.group] in affected)<=1e-8)
            offset+=len(obj.data.vertices)
        future_outside=compare(actual,future,[*range(1,22),*range(28,31)])
        untouched={'bones':max(float(np.max(np.abs(actual[f]['bones'][other_bone_indices]-future[f]['bones'][other_bone_indices]))) for f in range(22,28)),
                   'skin':max(float(np.max(np.abs(actual[f]['skin'][unaffected_skin]-future[f]['skin'][unaffected_skin]))) for f in range(22,28)),
                   'root':max(float(np.max(np.abs(actual[f]['root']-future[f]['root']))) for f in range(22,28))}
        selected_delta=compare(actual,future,range(22,28))
        checks.append({'check':'future_masked_edit_takes_over_only_its_region_of_auto_preserved_base',
                       'passed':preserved(untouched) and preserved(future_outside) and selected_delta['skin']>.01 and len(unaffected_skin)>10000 and registered['priority_diagnostic']['priority_source']=='automatic_takeover_of_preserved_snapshot',
                       'unselected_region_errors':untouched,'outside_event_window_errors':future_outside,'selected_region_change':selected_delta,
                       'unaffected_skin_vertex_count':len(unaffected_skin),'priority_diagnostic':registered['priority_diagnostic']})
        actual=future
    scene.frame_set(15);highq=arm.pose.bones[right].rotation_quaternion@Quaternion((0,1,0),.25)
    priority=max(e.get('priority',0) for e in timeline_info(job,'A')['events'])+1
    register_event(job,'A','new_higher_FK',{'source':'procedural','start_frame':11,'end_frame':20,'mask':[right],'priority':priority,'weight':.5,
        'keyframes':[{'frame':11,'pose':{right:{'rotation_quaternion':list(highq)}}},{'frame':20,'pose':{right:{'rotation_quaternion':list(highq)}}}]})
    new_left=arm.matrix_world@arm.pose.bones[info['bone_roles']['left_hand']].head
    register_event(job,'A','new_higher_IK',{'source':'procedural','start_frame':11,'end_frame':20,'mask':['left_arm'],'priority':priority+1,
        'targets':{'LeftHand':{'keyframes':[{'frame':11,'position':list(new_left+Vector((0,0,.06))),**({'pole':pole} if pole is not None else {}),'influence':.4},
                                             {'frame':20,'position':list(new_left+Vector((0,0,.06))),**({'pole':pole} if pole is not None else {}),'influence':.4}]}}})
    higher=motion_commit(job,'A',11,20);higher_data=snapshot(actor,range(1,31));delta=compare(actual,higher_data,range(11,21))
    outside_high=compare(actual,higher_data,[*range(1,11),*range(21,31)])
    checks.append({'check':'higher_priority_new_FK_and_IK_layers_are_allowed_only_in_their_window','passed':delta['skin']>.01 and preserved(outside_high) and any(x['event_id']=='new_higher_IK' for x in higher['target_diagnostics']),
                   'inside_change':delta,'outside_errors':outside_high})
    keys=[]
    for frame in range(11,21):
        scene.frame_set(frame);keys.append({'frame':frame,'interpolation':'linear','pose':read_pose(arm,evaluated=True),
            'root':list(root.location),'root_rotation_quaternion':list(root.rotation_quaternion),'root_scale':list(root.scale)})
    register_event(job,'A','helper_final_hybrid',{'source':'hybrid','motion_id':'mixamo.basic.idle','start_frame':11,'end_frame':20,
        'priority':priority+2,'root_strategy':'trajectory','keyframes':keys,'final_pose_layers':True,
        'metadata':{'final_pose_origin':'Actual evaluated FK/IK channels; library source retained'}})
    helper=motion_commit(job,'A',11,20);helper_data=snapshot(actor,range(1,31));helper_error=compare(higher_data,helper_data,range(1,31))
    checks.append({'check':'guarded_final_pose_hybrid_marker_preserves_actual_included_layers','passed':preserved(helper_error),'all_frame_errors':helper_error})
    blend=folder/'final_pose_layers.blend';bpy.ops.wm.save_as_mainfile(filepath=str(blend));bpy.ops.wm.open_mainfile(filepath=str(blend),use_scripts=False)
    actor=resolve_actor(job,'A');reload_data=snapshot(actor,range(1,31));reload_error=compare(helper_data,reload_data,range(1,31))
    checks.append({'check':'final_pose_layer_capture_and_helper_reload_all_bones_skin_root','passed':preserved(reload_error),'errors':reload_error})
    facts=scene_motion_facts(job);checks.append({'check':'final_pose_registered_sources_and_actual_channels_match','passed':not facts['errors']})
    checks.append(quaternion_curve_check(actor))
    report={'passed':all(c['passed'] for c in checks),'job_id':job,'checks':checks,'capture_commit':result,'higher_commit':higher,'helper_commit':helper,
            'scene':str(blend.relative_to(PROJECT)),'scene_sha256':digest(blend),'motion_facts':facts,'source_sha256':digest(PROJECT/'scripts/agent_motion_timeline.py'),
            'test_sha256':digest(Path(__file__)),'automatic_pole_partial_capture_inside_outside_verified':auto_pole_partial,
            'scope':'Exact every-frame final-channel FK/IK layering, priority, full skin/root, outside-window preservation and reload; no naturalness certification.'}
    atomic_json(folder/'validation.json',report);print('FINAL_LAYERS_PROOF',folder/'validation.json',json.dumps(checks),flush=True)
    if not report['passed']:raise RuntimeError('Final evaluated pose layer capture regression failed')


if '--final-layers' in sys.argv:
    final_layers_proof('--expect-duplicate' in sys.argv,'--auto-pole-full' in sys.argv,'--auto-pole-partial' in sys.argv)
    raise SystemExit(0)


scene=setup_scene();scene.frame_end=180
job=uuid.uuid4().hex[:16];folder=PROJECT/'reports'/'robot_hybrid_20261014'/('local_capture_'+job);folder.mkdir(parents=True)
atomic_json(PROJECT/'output'/'jobs'/job/'request.json',{'prompt':'测试局部捕获101–110帧，窗口外全部原库/程序/库与末尾库骨骼、皮肤、根不能丢失。','origin':'local_capture_regression','character_policy':'library_beta'})
info=actor_ensure(job,'A');actor=resolve_actor(job,'A');arm=actor['armature'];root=actor['root'];hand=info['bone_roles']['right_arm']
def gesture(amount):return {'source':'procedural','start_frame':61,'end_frame':90,'root_strategy':'hold','keyframes':[
    {'frame':61,'pose':{hand:{'rotation_quaternion':list(Quaternion((1,0,0),.1))}}},
    {'frame':75,'pose':{hand:{'rotation_quaternion':list(Quaternion((1,0,0),amount))}}},
    {'frame':90,'pose':{hand:{'rotation_quaternion':list(Quaternion((1,0,0),.1))}}}],
    'intent':'未经枚举的自然语言手势','metadata':{'root_trajectory':'semantic metadata remains open'}}
for name,start,end in (('walk1',1,60),('walk2',91,150),('walk3',151,180)):
    register_event(job,'A',name,{'source':'library','motion_id':'mixamo.basic.walk','start_frame':start,'end_frame':end,'root_strategy':'source','blend_in_frames':5})
register_event(job,'A','gesture',gesture(.35))
first=motion_commit(job,'A',1,180)
baseline=snapshot(actor,range(1,181));stored=timeline_info(job,'A');checks=[]
for frame in range(101,111):
    scene.frame_set(frame)
    pb=arm.pose.bones[hand];pb.rotation_quaternion=pb.rotation_quaternion@Quaternion((0,1,0),.3)
    pb.keyframe_insert('rotation_quaternion',frame=frame)
    wrist=arm.pose.bones[info['bone_roles']['right_hand']]
    wrist.location+=Vector((.7,0,0));wrist.scale=(1.04,1,1)
    wrist.keyframe_insert('location',frame=frame);wrist.keyframe_insert('scale',frame=frame)
    root.location+=Vector((.04,.02,0));root.rotation_quaternion=root.rotation_quaternion@Quaternion((0,0,1),.12)
    root.scale=(.97,.97,.97)
    for channel in ('location','rotation_quaternion','scale'):root.keyframe_insert(channel,frame=frame)
raw=snapshot(actor,range(1,181));outside=[f for f in range(1,181) if not 101<=f<=110]
raw_outside=compare(baseline,raw,outside)
checks.append({'check':'direct_bpy_edit_is_actually_limited_to_101_110','passed':preserved(raw_outside),'errors':raw_outside})
fingerprints=(animation_fingerprint(arm),animation_fingerprint(root));before_guard=timeline_info(job,'A')
try:motion_commit(job,'A',101,110);refused=False
except ValueError as exc:refused='capture=True' in str(exc)
checks.append({'check':'ordinary_partial_commit_refuses_unregistered_edits_without_mutation','passed':refused and fingerprints==(animation_fingerprint(arm),animation_fingerprint(root)) and before_guard==timeline_info(job,'A')})
start=time.perf_counter();captured=motion_commit(job,'A',101,110,capture=True,event_id='local_actual_edit');seconds=time.perf_counter()-start
after=snapshot(actor,range(1,181));outside_errors=compare(baseline,after,outside);inside_errors=compare(raw,after,range(101,111))
checks.extend([
 {'check':'partial_capture_returns_requested_and_actual_bake_ranges','passed':captured['requested_range']==[101,110] and captured['captured_range']==[101,110] and captured['actual_bake_range']==[1,180] and captured['frame_count']==180},
 {'check':'all_bones_skin_root_outside_capture_including_next_library_are_preserved','passed':preserved(outside_errors),'errors':outside_errors,'sampled_frames':len(outside)},
 {'check':'actual_local_bpy_channels_remain_in_capture_window','passed':preserved(inside_errors),'errors':inside_errors,'sampled_frames':10},
 {'check':'original_unrelated_event_records_are_unchanged','passed':all(e in timeline_info(job,'A')['events'] for e in stored['events'])},
 {'check':'local_capture_source_and_actual_fingerprints_agree','passed':not scene_motion_facts(job)['errors']},
])
same_id=motion_commit(job,'A',105,107,capture=True,event_id='local_actual_edit')
same_id_data=snapshot(actor,range(1,181));same_id_error=compare(after,same_id_data,range(1,181))
checks.append({'check':'reusing_capture_event_id_with_narrower_window_preserves_previous_capture_outside_window',
               'passed':preserved(same_id_error) and same_id['captured_range']==[105,107] and same_id['actual_bake_range']==[1,180] and
               any(e.get('preservation_reason','').startswith('Same event_id') for e in timeline_info(job,'A')['events']),
               'errors':same_id_error})
after=same_id_data
ordinary=motion_commit(job,'A',70,80,events=[dict(gesture(.6),event_id='gesture')])
updated=snapshot(actor,range(1,181));outside_gesture=[f for f in range(1,181) if not 61<=f<=90]
ordinary_errors=compare(after,updated,outside_gesture)
change=float(np.max(np.abs(updated[75]['skin']-after[75]['skin'])))
checks.append({'check':'ordinary_local_event_update_rebakes_full_timeline_and_changes_only_event_window','passed':ordinary['actual_bake_range']==[1,180] and ordinary['requested_range']==[70,80] and ordinary['captured_range'] is None and preserved(ordinary_errors) and change>.01,'outside_errors':ordinary_errors,'gesture_skin_change_m':change})
invalid=[]
for fields,text in (({'root_trajectory':[[0,0,0]]},'root_path'),({'source_window_frames':[0,20]},'source_window'),({'root_strategy':'trajectory'},'actual root')):
    state=timeline_info(job,'A');fp=(animation_fingerprint(arm),animation_fingerprint(root))
    try:motion_commit(job,'A',5,10,events=[dict(gesture(.5),event_id='bad',**fields)]);ok=False
    except ValueError as exc:ok=text in str(exc)
    invalid.append(ok and state==timeline_info(job,'A') and fp==(animation_fingerprint(arm),animation_fingerprint(root)))
checks.append({'check':'wrong_execution_fields_are_diagnosed_before_mutation','passed':all(invalid),'cases':invalid})
masked=register_event(job,'A','masked_root_request',{'source':'procedural','start_frame':121,'end_frame':130,'mask':['right_arm'],'root_strategy':'trajectory','keyframes':[
    {'frame':121,'root':[50,50,50]},{'frame':130,'root':[60,60,60]}]})
masked_commit=motion_commit(job,'A',121,130);masked_data=snapshot(actor,range(1,181));masked_error=compare(updated,masked_data,range(1,181))
checks.append({'check':'masked_root_request_reports_no_ownership_and_does_not_move_root_or_body','passed':masked['root_owner'] is False and masked['root_requests_ignored'] and preserved(masked_error),'errors':masked_error})
blend=folder/'local_capture_full_timeline.blend';bpy.ops.wm.save_as_mainfile(filepath=str(blend));bpy.ops.wm.open_mainfile(filepath=str(blend),use_scripts=False)
actor=resolve_actor(job,'A');reloaded=snapshot(actor,range(1,181));reload_error=compare(masked_data,reloaded,range(1,181))
checks.append({'check':'independent_reload_preserves_all_180_frame_actual_bones_skin_root','passed':preserved(reload_error),'errors':reload_error})
checks.append(quaternion_curve_check(actor))
report={'passed':all(c['passed'] for c in checks),'job_id':job,'checks':checks,'capture_commit_seconds':seconds,'captured_commit':captured,'same_id_narrower_capture_commit':same_id,'ordinary_commit':ordinary,'masked_commit':masked_commit,
        'scene':str(blend.relative_to(PROJECT)),'scene_sha256':digest(blend),'motion_facts':scene_motion_facts(job),
        'source_sha256':{name:digest(PROJECT/'scripts'/name) for name in ('agent_motion_controls.py','agent_motion_timeline.py','agent_motion_quality.py')},
        'test_sha256':digest(Path(__file__)),'scope':'180 exact integer frames, every actual Beta bone and all 24746 deformed skin vertices: nested local capture, outside-window range and next-event context preservation, local ordinary replacement, source fingerprint, root single-owner, reload and quaternion continuity. Not arbitrary action naturalness certification.'}
atomic_json(folder/'validation.json',report)
print('LOCAL_CAPTURE_PROOF',folder/'validation.json',json.dumps(checks,ensure_ascii=False),flush=True)
if not report['passed']:raise RuntimeError('Actual local capture range/context regression failed')
