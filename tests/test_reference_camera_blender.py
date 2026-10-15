"""Independent camera placement/visibility checks on saved Blender scenes.

Keep older physical regression reports and real job plans unchanged. Run the
small probes first while the camera implementation is under development; run
integration after the shared helper and executors are frozen. A material/point
visibility check does not certify subjective animation naturalness.
"""
import argparse
import contextlib
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Vector

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import duel_plan
from duel_plan import atomic_json
from camera_plan import validate_camera
from single_plan import validate_single
from scene_plan import validate_scene
from scene_objects import SceneObjects
from duel_scene import Actor,point,setup_stage
from duel_motion_sources import MotionSources
from reference_camera import setup_reference_camera,verify_reference_camera

OUTPUT=ROOT/'output/verification/reference_camera'
REPORT=ROOT/'reports/reference_camera_blender_regression_20261013.json'
ORIGINAL_VAULT=ROOT/'output/jobs/7d7b5fc125b541a7'
ENGINE_FILES=('camera_plan.py','reference_camera.py','single_plan.py',
              'window_vault_scene.py','door_passage_scene.py','locomotion_scene.py',
              'prop_interaction_scene.py','scene_objects.py','duel_scene.py',
              'duel_motion_sources.py','vault_pose.py','locomotion_gait.py',
              'locomotion_path.py','door_interaction_pose.py','door_passage_geometry.py')
PROTECTED_REPORTS=('window_vault_blender_regression.json',
                   'door_phase_window_vault_blender_regression_20261013.json',
                   'door_passage_blender_regression_20261013.json',
                   'locomotion_blender_regression_20261013.json')
duel_plan.JOBS=OUTPUT


def hashes(paths):
    return {str(path):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def angle_error_deg(actual,expected):
    return abs((actual-expected+180)%360-180)


def skin_world(meshes):
    graph=bpy.context.evaluated_depsgraph_get();arrays=[]
    for mesh in meshes:
        evaluated=mesh.evaluated_get(graph)
        local=np.empty((len(evaluated.data.vertices),3),dtype=np.float64)
        evaluated.data.vertices.foreach_get('co',local.ravel())
        matrix=np.array(evaluated.matrix_world,dtype=np.float64)
        arrays.append(local@matrix[:3,:3].T+matrix[:3,3])
    assert arrays,'独立检查缺少实际人物蒙皮'
    return np.concatenate(arrays)


def actual_projection_margin(scene,camera,world):
    graph=bpy.context.evaluated_depsgraph_get()
    projection=camera.calc_matrix_camera(graph,x=scene.render.resolution_x,
        y=scene.render.resolution_y,scale_x=scene.render.pixel_aspect_x,
        scale_y=scene.render.pixel_aspect_y)
    view=np.asarray(camera.matrix_world.inverted(),dtype=float)
    homogeneous=np.c_[world,np.ones(len(world))]
    clipped=homogeneous@np.asarray(projection@camera.matrix_world.inverted(),dtype=float).T
    assert np.all(np.abs(clipped[:,3])>1e-8),'相机投影出现无穷坐标'
    uv=clipped[:,:2]/clipped[:,3:4]*.5+.5
    depths=-(homogeneous@view.T)[:,2]
    assert np.all(depths>camera.data.clip_start),'实际人物在相机后面或近裁切面内'
    return float(np.minimum(uv,1-uv).min())


def actual_camera(scene,spec,meshes,reference_yaw_deg=0,expected_look_at=None):
    """Read the saved camera, independent of requested/applied angle claims."""
    camera=scene.camera;assert camera is not None,'没有保存活动相机'
    assert camera.animation_data is None or camera.animation_data.action is None,'固定镜头被写成相机动画'
    facts=json.loads(camera['reference_camera_facts_json'])
    assert facts['requested']==spec,'保存相机请求与原始canonical请求不一致'
    actual_position=camera.matrix_world.translation.copy()
    look_at=Vector(spec.get('look_at',expected_look_at or facts['applied']['look_at']))
    if 'position' in spec:
        assert (actual_position-Vector(spec['position'])).length<=1e-6,'明确position被替换或后退'
    if 'look_at' in spec:
        assert (look_at-Vector(facts['applied']['look_at'])).length<=1e-6,'明确look_at被替换'
    gaze=camera.matrix_world.to_quaternion()@Vector((0,0,-1))
    expected_gaze=(look_at-actual_position).normalized()
    gaze_error=math.degrees(math.atan2(gaze.cross(expected_gaze).length,gaze.dot(expected_gaze)))
    assert gaze_error<=.002,'实际保存相机旋转没有指向明确look_at'
    offset=actual_position-look_at
    world_azimuth=math.degrees(math.atan2(offset.x,-offset.y))
    elevation=math.degrees(math.atan2(offset.z,offset.xy.length))
    if 'position' not in spec:
        assert angle_error_deg(world_azimuth,reference_yaw_deg+spec['azimuth_deg'])<=.001,'明确参照方位被静默改角'
        assert abs(elevation-spec['elevation_deg'])<=.001,'明确俯仰被静默改变'
    projection='orthographic' if camera.data.type=='ORTHO' else 'perspective'
    assert projection==spec['projection'],'实际投影类型与请求不一致'
    actual_fov=math.degrees(camera.data.angle_x)
    assert abs(actual_fov-spec['fov_deg'])<=.001,'实际水平FOV与请求不一致'
    start_position=actual_position.copy();start_rotation=camera.matrix_world.to_quaternion()
    minimum=1.;frames=0
    for frame in range(scene.frame_start,scene.frame_end+1):
        scene.frame_set(frame);bpy.context.view_layer.update();frames+=1
        assert (camera.matrix_world.translation-start_position).length<=1e-6,'固定相机位置随帧变化'
        assert abs(camera.matrix_world.to_quaternion().dot(start_rotation))>=1-1e-7,'固定相机旋转随帧变化'
        margin=actual_projection_margin(scene,camera,skin_world(meshes));minimum=min(minimum,margin)
        assert margin>=.05-1e-4,f'实际第{frame}帧皮肤全身构图不足5%边距：{margin}'
    return {'position':list(actual_position),'look_at':list(look_at),
            'world_azimuth_deg':world_azimuth,'reference_yaw_deg':reference_yaw_deg,
            'actual_relative_azimuth_deg':(world_azimuth-reference_yaw_deg+180)%360-180,
            'actual_elevation_deg':elevation,'projection':projection,'actual_horizontal_fov_deg':actual_fov,
            'gaze_error_deg':gaze_error,'minimum_actual_skin_margin':minimum,'tested_frames':frames}


def solid_snapshot(props):
    result={}
    for obj in props.solids:
        geometry=np.array([tuple(v.co) for v in obj.data.vertices],dtype=np.float64)
        result[obj.name]={'solid':bool(obj.get('prop_solid')),
            'vertex_sha256':hashlib.sha256(geometry.tobytes()).hexdigest(),
            'dimensions':list(obj.dimensions),'world_matrix':[list(row) for row in obj.matrix_world]}
    return result


def expect_refusal(fn,reasons=None):
    try:fn()
    except (ValueError,AssertionError) as error:
        message=str(error)
        if reasons:assert any(reason in message for reason in reasons),'拒绝原因不是预期约束：'+message
        return {'passed':True,'expected_refusal':message}
    raise AssertionError('不能执行的明确相机要求被接受或静默替换')


def schema_cases():
    baseline={'version':1,'animations':['mixamo_fbx/basic/Idle.fbx'],'duration_seconds':3}
    assert 'camera' not in validate_single(baseline,'camera_legacy_schema'),'legacy缺省相机被强行迁移'
    good=[{}, {'reference':'object','target':'window_1','azimuth_deg':-45,'elevation_deg':15},
          {'reference':'actor','target':'A','azimuth_deg':90,'projection':'perspective','fov_deg':90},
          {'position':[5,-7,4],'look_at':[0,0,.9],'projection':'perspective','fov_deg':20}]
    for raw in good:
        canonical=validate_camera(raw);assert validate_camera(canonical)==canonical,'camera重复校验改变请求'
    explicit=validate_camera({'reference':'world','azimuth_deg':-45,'elevation_deg':15})
    assert validate_single({**baseline,'camera':explicit},'camera_schema_preserved')['camera']==explicit,'single计划丢弃明确camera'
    bad=[{'azimuth_deg':0,'unsupported_camera_setting':True},
         {'projection':'ortho'}, {'framing':'close_up'}, {'reference':'object'},
         {'reference':'world','target':'window_1'}, {'position':[0,-3,1],'azimuth_deg':45},
         {'position':[0,-3,1],'look_at':[0,-3,1]}, {'fov_deg':True}, {'fov_deg':float('nan')},
         {'azimuth_deg':181}, {'elevation_deg':81}, {'position':[0,0]}, {'look_at':[0,0,31]}]
    refusals=[expect_refusal(lambda raw=raw:validate_camera(raw)) for raw in bad]
    refusals.append(expect_refusal(lambda:validate_single({**baseline,'camera':bad[0]},'camera_schema_unknown')))
    return {'passed':True,'legacy_absent_camera_preserved':True,'canonical_roundtrips':len(good),
            'invalid_requests_explicitly_rejected':len(refusals)}


def small_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sources=MotionSources({'guard':{'asset':'mixamo_fbx/basic/Idle.fbx'}})
    actor=Actor('A',sources,0,math.radians(28),.8);actor.set_pose(sources.sample('guard',1))
    for obj in sources.meshes+[sources.template]:bpy.data.objects.remove(obj,do_unlink=True)
    scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=3;setup_stage()
    bpy.context.view_layer.update()
    return actor


def small_cases():
    actor=small_scene();scene=bpy.context.scene;results=[]
    plan=validate_scene({'version':1,'duration_seconds':3,'room':{'enabled':False},
        'objects':[{'id':'window_1','type':'window','position':[3,3,0],
                    'size':[1.3,.14,1.2],'yaw_deg':40,'state':'open'}],
        'interactions':[]},'camera_small_reference',1,3)
    props=SceneObjects(plan)
    variants=[('world_front',{'azimuth_deg':0}),('world_right',{'azimuth_deg':90}),
        ('world_left',{'azimuth_deg':-90}),
        ('object_minus45',{'reference':'object','target':'window_1','azimuth_deg':-45,'elevation_deg':15}),
        ('object_plus45',{'reference':'object','target':'window_1','azimuth_deg':45,'elevation_deg':15}),
        ('actor_initial_heading',{'reference':'actor','target':'A','azimuth_deg':-45,'elevation_deg':15}),
        ('perspective20',{'projection':'perspective','fov_deg':20}),
        ('perspective90',{'projection':'perspective','fov_deg':90})]
    for name,raw in variants:
        spec=validate_camera(raw);before=solid_snapshot(props)
        facts=setup_reference_camera(actor.meshes+props.focus_meshes(),spec,{'A':actor},props)
        scene.frame_set(1);bpy.context.view_layer.update()
        yaw=40 if spec.get('reference')=='object' else 28 if spec.get('reference')=='actor' else 0
        actual=actual_camera(scene,spec,actor.meshes,yaw)
        assert solid_snapshot(props)==before,'相机布局改变物理solid几何'
        qa=verify_reference_camera(actor.meshes+props.focus_meshes(),spec,{'A':actor},props,frame_count=3)
        assert qa['passed'],'小探针实际构图/可见性失败：'+json.dumps(qa,ensure_ascii=False)
        results.append({'name':name,'passed':True,'actual':actual,'visibility':qa})
    # Explicit coordinates are intentionally off the helper's automatically
    # fitted bearing; matching them rules out merely recording a requested pose.
    for name,position,look in [('world_position_a',[5,-7,4],[0,0,.9]),
                               ('world_position_b',[-6,-4,3.8],[.1,.2,1])]:
        spec=validate_camera({'position':position,'look_at':look,'projection':'perspective'})
        setup_reference_camera(actor.meshes,spec,{'A':actor})
        actual=actual_camera(scene,spec,actor.meshes)
        results.append({'name':name,'passed':True,'actual':actual})
    near=validate_camera({'position':[0,-.4,1],'look_at':[0,0,1],'projection':'perspective'})
    results.append({'name':'reject_too_near_fixed_perspective',**expect_refusal(
        lambda:setup_reference_camera(actor.meshes,near,{'A':actor}),('固定相机','完整显示'))})
    results.append({'name':'reject_unknown_setup_field',**expect_refusal(
        lambda:setup_reference_camera(actor.meshes,{'unexecuted_angle':45},{'A':actor}),('未执行字段',))})
    results.append({'name':'reject_below_ground_camera',**expect_refusal(
        lambda:setup_reference_camera(actor.meshes,validate_camera({'elevation_deg':-80}),{'A':actor}),('地面','地下','低于'))})
    # A closed door between actor and camera is an obvious opaque obstruction.
    # First prove strict rejects it, then only change architectural display.
    doorplan=validate_scene({'version':1,'duration_seconds':3,'room':{'enabled':False},
        'objects':[{'id':'door_1','type':'door','position':[0,-.9,0],
                    'size':[1.3,.14,2.1],'state':'closed'}],
        'interactions':[]},'camera_small_occlusion',1,3)
    blockers=SceneObjects(doorplan);before=solid_snapshot(blockers)
    strict=validate_camera({'occlusion_policy':'strict'})
    setup_reference_camera(actor.meshes+blockers.focus_meshes(),strict,{'A':actor},blockers)
    qa=verify_reference_camera(actor.meshes+blockers.focus_meshes(),strict,{'A':actor},blockers,frame_count=3)
    assert not qa['passed'] and qa.get('occlusion_failure_count',len(qa.get('occlusion_failures',[])))>0,'strict接受明显的门扇遮挡'
    assert solid_snapshot(blockers)==before,'strict展示改变实体门'
    results.append({'name':'reject_strict_opaque_door','passed':True,'visibility_refusal':qa})
    transparent=validate_camera({'occlusion_policy':'transparent_architecture'})
    facts=setup_reference_camera(actor.meshes+blockers.focus_meshes(),transparent,{'A':actor},blockers)
    qa=verify_reference_camera(actor.meshes+blockers.focus_meshes(),transparent,{'A':actor},blockers,frame_count=3)
    assert qa['passed'],'透明参考建筑仍遮挡完整人体'
    assert solid_snapshot(blockers)==before,'透明显示删除/改变实际物理solid'
    results.append({'name':'transparent_door_preserves_all_solids','passed':True,
                    'visibility':qa,'presentation':facts['reference_presentation'],'solid_count':len(before)})
    return results


def floor_cases():
    actor=small_scene();scene=bpy.context.scene;results=[]
    for projection,expected in (('orthographic',False),('perspective',True)):
        spec=validate_camera({'position':[0,-5,.2],'look_at':[0,0,1.2],
                              'projection':projection,'fov_deg':60,'occlusion_policy':'strict'})
        setup_reference_camera(actor.meshes,spec,{'A':actor})
        actual=actual_camera(scene,spec,actor.meshes)
        qa=verify_reference_camera(actor.meshes,spec,{'A':actor},frame_count=3)
        assert qa['passed']==expected,'实际Rig低角相机/不透明stage floor判断与物理射线不符'
        if not expected:
            assert qa['occlusion_failure_count']>0
            assert any('Stage_Floor' in hit['opaque_parts'] for hit in qa['occlusion_failures']),'低角正交拒绝没有来自真实Stage_Floor'
        results.append({'name':'low_fixed_'+projection,'passed':True,'expected_visibility_passed':expected,
                        'actual_camera':actual,'visibility':qa})
    return results


def loaded_parts(environment):
    scene=bpy.context.scene;arm=bpy.data.objects['A_Rig'];root=bpy.data.objects['A_Space']
    meshes=[obj for obj in scene.objects if obj.type=='MESH' and obj.get('actor_id')=='A']
    assert meshes,'保存场景缺少实际人物蒙皮'
    actor={'meshes':meshes,'root':root,'arm':arm}
    entries={spec['id']:{'spec':spec,'root':bpy.data.objects['Prop_'+spec['id']],
             'meshes':[obj for obj in scene.objects if obj.type=='MESH' and obj.get('prop_id')==spec['id']]}
             for spec in environment.get('objects',[])}
    props=SimpleNamespace(solids=[obj for obj in scene.objects if obj.type=='MESH' and obj.get('prop_solid')],
                          objects=entries)
    return actor,props


def pose_samples(scene,arm):
    result=[]
    for frame in (scene.frame_start,(scene.frame_start+scene.frame_end)//2,scene.frame_end):
        scene.frame_set(frame);bpy.context.view_layer.update()
        result.append([frame]+[coordinate for name in ('Hips','Spine','LeftArm','RightArm','LeftForeArm','RightForeArm',
                    'LeftHandMiddle1','RightHandMiddle1','LeftUpLeg','RightUpLeg','LeftLeg','RightLeg','LeftFoot','RightFoot')
                for coordinate in point(arm,name)])
    return result


def saved_physical_checks(folder,kind,quality,actor):
    """Repeat existing physical gates on the saved scene; camera cannot fix pose."""
    scene=bpy.context.scene;arm=actor['arm'];meshes=actor['meshes'];root=actor['root']
    records=json.loads((folder/'trajectory.json').read_text())['frames']
    assert len(records)==scene.frame_end,'保存轨迹缺少动作帧'
    max_hand=max_toe=max_slide=max_joint=0.;lowest=100.;contacts=0;previous_toes={};previous_joints={}
    joints=('LeftForeArm','RightForeArm','LeftLeg','RightLeg','Spine') if kind=='vault' else (
        'Hips','Spine','LeftUpLeg','RightUpLeg','LeftLeg','RightLeg','LeftFoot','RightFoot')
    for record in records:
        scene.frame_set(record['frame']);bpy.context.view_layer.update()
        expected=record.get('root',record.get('position'))
        assert expected is not None and (root.matrix_world.translation-Vector(expected)).length<=.001,'新镜头改变实际身体路线'
        world=skin_world(meshes);minimum=float(world[:,2].min());lowest=min(lowest,minimum)
        assert minimum>=-.045,'新保存场景实际皮肤穿地'
        for joint in joints:
            rotation=arm.pose.bones['mixamorig:'+joint].matrix.to_quaternion()
            if joint in previous_joints:
                step=math.degrees(2*math.acos(min(1.,abs(rotation.dot(previous_joints[joint])))))
                max_joint=max(max_joint,step);assert step<=65,'实际身体关节跳变超过原65度阈值'
            previous_joints[joint]=rotation.copy()
        for contact in record.get('contacts',[]):
            marker=point(arm,contact['limb'].replace('Hand','HandMiddle1'))
            error=(marker-Vector(contact['target'])).length;max_hand=max(max_hand,error);contacts+=1
            assert error<=.035+1e-6,'实际双手撑窗接触超过原35mm阈值'
        contact=record.get('contact')
        if contact and contact['weight']>=.999:
            error=(point(arm,contact['tip'])-Vector(contact['target'])).length
            max_hand=max(max_hand,error);contacts+=1;assert error<=.0495,'实际门把接触超过原阈值'
        current={}
        for key,target in record.get('toe_stance',{}).items():
            name=key.split(':')[-1];actual=point(arm,name);target=Vector(target)
            error=(actual-target).length;max_toe=max(max_toe,error)
            assert error<=(.0165 if kind=='door' else .025),'实际脚尖目标误差超过原阈值'
            if key in previous_toes:
                old_target,old_actual=previous_toes[key]
                if (target-old_target).length<=1e-5:
                    slide=(actual-old_actual).length;max_slide=max(max_slide,slide)
                    assert slide<=(.0165 if kind=='door' else .02),'实际同锚脚滑超过原阈值'
            current[key]=(target.copy(),actual.copy())
        previous_toes=current
    assert quality['passed'],'总身体/手/脚/镜头质量未通过'
    if kind in ('vault','door'):assert contacts>=15,'新镜头场景没有实际连续手接触'
    if kind=='vault':
        assert quality['traversal']['passed'] and quality['landing']['passed'],'翻窗穿越或落地能力丢失'
        assert not quality['skin_clearance_failures'] and not quality['ground_failures'] and not quality['joint_flip_failures']
        assert all(abs(point(arm,side+'ToeBase').z)<.09 for side in ('Left','Right')),'最终翻窗脚没有落地'
    else:
        assert len(previous_toes)==2,'最终站稳缺少双脚实际锁定'
    return {'passed':True,'checked_frames':len(records),'continuous_hand_contact_samples':contacts,
            'max_actual_hand_marker_error_m':max_hand,'max_actual_toe_target_error_m':max_toe,
            'max_same_anchor_actual_toe_slide_m':max_slide,'minimum_actual_skin_z_m':lowest,
            'max_actual_joint_step_deg':max_joint,'pose_samples':pose_samples(scene,arm),
            'runtime_physical_quality_passed':quality['passed']}


def integration_case(name,kind,source,camera):
    source=Path(source);folder=OUTPUT/name;folder.mkdir(parents=True,exist_ok=True)
    raw=json.loads((source/'single_plan.json').read_text());raw['job_id']=name;raw['camera']=camera
    if (source/'scene_plan.json').exists():
        environment=validate_scene(json.loads((source/'scene_plan.json').read_text()),name,1,raw['duration_seconds'])
        atomic_json(folder/'scene_plan.json',environment)
    else:environment={'objects':[],'interactions':[],'room':{'enabled':False}}
    single=validate_single(raw,name);atomic_json(folder/'single_plan.json',single)
    immutable=hashes([folder/'single_plan.json',folder/'scene_plan.json'])
    if kind=='vault':from window_vault_scene import build
    elif kind=='door':from door_passage_scene import build
    else:from locomotion_scene import build
    with contextlib.redirect_stdout(io.StringIO()):quality=build(single,folder)
    assert quality['passed'],'真实'+kind+'camera集成被QA拒绝：'+json.dumps({key:quality.get(key) for key in ('failure_count','failures','camera_quality')},ensure_ascii=False)[:2000]
    bpy.ops.wm.open_mainfile(filepath=str(folder/'scene.blend'))
    scene=bpy.context.scene;actor,props=loaded_parts(environment)
    scene.frame_set(1);bpy.context.view_layer.update();spec=single['camera']
    reference_yaw=0.
    if spec.get('reference')=='object':reference_yaw=math.degrees(props.objects[spec['target']]['root'].matrix_world.to_euler().z)
    elif spec.get('reference')=='actor':reference_yaw=math.degrees(actor['root'].matrix_world.to_euler().z)
    actual=actual_camera(scene,spec,actor['meshes'],reference_yaw)
    focus=[mesh for entry in props.objects.values() if entry['spec'].get('focus') for mesh in entry['meshes']]
    camera_quality=verify_reference_camera(actor['meshes']+focus,spec,{'A':actor},props)
    assert camera_quality['passed'],'保存重载后的真实相机全身或遮挡检查失败'
    physical=saved_physical_checks(folder,kind,quality,actor)
    assert immutable==hashes([Path(path) for path in immutable]),'执行器改写原始动作/相机请求'
    assert json.loads(scene['single_plan_json'])['camera']==spec,'scene.blend没有保留真实相机请求'
    result={'name':name,'kind':kind,'passed':True,'source_job_or_fixture':str(source.relative_to(ROOT)),
            'fixture':str(folder.relative_to(ROOT)),'requested':spec,'actual_saved_camera':actual,
            'camera_quality':camera_quality,'physical':physical,'original_request_files_unchanged':True}
    atomic_json(folder/'independent_camera_quality.json',result)
    return result


def integration_cases():
    # Read the original camera and body samples, leaving the real scene file untouched.
    bpy.ops.wm.open_mainfile(filepath=str(ORIGINAL_VAULT/'scene.blend'))
    scene=bpy.context.scene;original_camera_position=list(scene.camera.matrix_world.translation)
    original_camera_rotation=list(scene.camera.matrix_world.to_quaternion())
    original_pose=pose_samples(scene,bpy.data.objects['A_Rig'])
    results=[]
    for name,angle in [('vault10_window_minus45',-45),('vault10_window_plus45',45)]:
        results.append(integration_case(name,'vault',ORIGINAL_VAULT,
            {'reference':'object','target':'window_1','azimuth_deg':angle,'elevation_deg':15}))
        # Camera changes must leave all sampled core joints, contact limbs and
        # the original natural-language 10s scene trajectory unchanged.
        assert np.max(np.abs(np.asarray(results[-1]['physical']['pose_samples'])-np.asarray(original_pose)))<.0001,'camera变更意外改变原10s人物身体/手脚轨迹'
        assert json.loads((OUTPUT/name/'trajectory.json').read_text())==json.loads((ORIGINAL_VAULT/'trajectory.json').read_text()),'camera变更改变原控制器轨迹'
    difference=(Vector(results[0]['actual_saved_camera']['position'])-Vector(results[1]['actual_saved_camera']['position'])).length
    assert difference>.5,'±45度实际保存的相机位置没有改变'
    assert (Vector(results[0]['actual_saved_camera']['position'])-Vector(original_camera_position)).length>.5,'新window−45仍使用旧硬编码相机'
    results[0]['legacy_camera_comparison']={'original_position':original_camera_position,
        'original_rotation_quaternion':original_camera_rotation,'opposite_requested_view_position_distance_m':difference,
        'original_pose_and_controller_trajectory_preserved':True}
    results.append(integration_case('door20_object_minus45','door',
        ROOT/'output/verification/door_passage_regression/outward_open_pass_close',
        {'reference':'object','target':'door_1','azimuth_deg':-45,'elevation_deg':15}))
    results.append(integration_case('locomotion12_actor_right','locomotion',
        ROOT/'output/verification/locomotion_regression/natural_language_180_turn_chain',
        {'reference':'actor','target':'A','azimuth_deg':90,'elevation_deg':20,
         'projection':'perspective','fov_deg':65}))
    return results


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=('schema','small','floor','integration','all'),default='small')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    protected=hashes([ROOT/'reports'/p for p in PROTECTED_REPORTS]+
                     [ORIGINAL_VAULT/'single_plan.json',ORIGINAL_VAULT/'scene_plan.json'])
    before=hashes([ROOT/'scripts'/name for name in ENGINE_FILES])
    report_path=ROOT/'reports/reference_camera_floor_pose_probe_20261013.json' if args.stage=='floor' else REPORT
    result={'status':'pending','requested_stage':args.stage,'passed':False,
            'engine_sources_sha256':before,'stages':{},'scope':'Camera parameter probes and saved/reloaded physical integration; rendered visual review remains separate.'}
    try:
        if args.stage in ('schema','small','all'):
            result['stages']['schema']=schema_cases();atomic_json(report_path,result)
        if args.stage in ('small','all'):
            result['stages']['small']={'passed':True,'cases':small_cases()};atomic_json(report_path,result)
        if args.stage=='floor':
            result['stages']['floor']={'passed':True,'cases':floor_cases()};result['status']='complete'
        if args.stage in ('integration','all'):
            result['stages']['integration']={'passed':True,'cases':integration_cases()}
            result['status']='complete' if args.stage=='all' else 'pending'
        result['passed']=all(stage['passed'] for stage in result['stages'].values())
    except Exception as error:
        result.update(passed=False,error=str(error),traceback=traceback.format_exc())
    result['sources_unchanged_during_stage']=before==hashes([ROOT/'scripts'/name for name in ENGINE_FILES])
    if not result['sources_unchanged_during_stage']:
        result.update(passed=False,status='pending',error='Source files changed while this camera stage was running.')
    result['protected_legacy_reports_and_original_job_unchanged']=protected==hashes([Path(p) for p in protected])
    assert result['protected_legacy_reports_and_original_job_unchanged'],'旧报告或原始真实任务被改写'
    atomic_json(report_path,result);print('REFERENCE_CAMERA_TEST '+json.dumps({k:result.get(k) for k in ('status','passed','requested_stage','error')},ensure_ascii=False),flush=True)
    if not result['passed']:raise RuntimeError('独立camera检查失败或尚未完成；查看新camera报告')


if __name__=='__main__':main()
