"""Real original-Beta geometry, contracts, reload and occlusion; no rendering/GUI.

LP_NUM_THREADS=1 ./blender -b -t 1 --python-exit-code 1 --python tests/test_agent_camera_quality_blender.py
"""
import sys,json,uuid,hashlib,tempfile,copy,time,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import bpy
from mathutils import Vector
import duel_plan
import agent_scene
from agent_scene_probe import probe_scene
from agent_character import actor_ensure
from agent_camera_quality import STORE,camera_requirements,check_camera_visibility


def main():
    out=ROOT/'reports/camera_visibility_20261014'/uuid.uuid4().hex[:16];out.mkdir(parents=True,exist_ok=False)
    duel_plan.JOBS=out/'isolated_jobs';job='camera_qa_'+uuid.uuid4().hex[:12];folder=duel_plan.JOBS/job;folder.mkdir(parents=True)
    (folder/'request.json').write_text(json.dumps({'prompt':'Isolated geometry QA: original Beta, arbitrary target, cameras and walls.'}))
    bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
    scene.frame_start=1;scene.frame_end=3;scene.render.resolution_x=640;scene.render.resolution_y=360
    scene.render.engine='BLENDER_EEVEE'
    actor_ensure(job,'A',position=(-1.5,0,0))
    data=bpy.data.cameras.new('Camera');camera=bpy.data.objects.new('Camera',data);scene.collection.objects.link(camera)
    camera.location=(0,-6,2.5);camera.rotation_euler=(Vector((0,0,.9))-camera.location).to_track_quat('-Z','Y').to_euler();data.lens=35;scene.camera=camera
    def box(name,location,scale):
        bpy.ops.mesh.primitive_cube_add(size=1,location=location);obj=bpy.context.object;obj.name=name;obj.scale=scale;return obj
    target=box('Arbitrary_Target',(1.2,0,1),(.6,.2,1.8))
    floor=box('Not_a_reserved_floor_name',(0,0,-.025),(10,10,.05))
    wall=box('Any_wall_name',(10,-2,1.2),(8,.1,4))
    contract={'version':1,'shots':[{'camera':'Camera','frame_start':1,'frame_end':3,'actor_ids':['A'],
                    'focus_objects':[target.name],'framing':'full_body','fixed_transform':True,
                    'look_at':[0,0,.9],'expected_projection':'perspective','margin':.04}]}
    checks=[]
    def record(name,value,expected=True):
        assert value['passed']==expected,(name,value)
        checks.append({'name':name,'passed':True,'actual':value})
    camera_requirements(job,contract)
    scene.frame_set(2,subframe=.25);saved=(scene.frame_current,scene.frame_subframe,scene.camera)
    record('original_beta_and_arbitrary_focus_are_visible',check_camera_visibility(job))
    assert saved==(scene.frame_current,scene.frame_subframe,scene.camera)
    checks.append({'name':'frame_subframe_and_camera_restored','passed':True})
    wall.location.x=0;bpy.context.view_layer.update()
    blocked=check_camera_visibility(job)
    record('opaque_free_scene_wall_blocks_body_and_object',blocked,False)
    assert any(x['code']=='subject_occluded' and x.get('kind')=='object' for x in blocked['failures']),blocked
    assert any(x['code']=='subject_occluded' and x.get('kind')=='actor' for x in blocked['failures']),blocked
    wall.location.x=10;wall.keyframe_insert(data_path='location',frame=1)
    wall.location.x=0;wall.keyframe_insert(data_path='location',frame=2)
    wall.location.x=10;wall.keyframe_insert(data_path='location',frame=3)
    moved=check_camera_visibility(job);record('animated_wall_checked_at_actual_intermediate_frame',moved,False)
    assert any(x.get('frame')==2 and x['code']=='subject_occluded' for x in moved['failures'])
    wall.animation_data_clear();wall.location.x=10
    wall.shape_key_add(name='Basis');key=wall.shape_key_add(name='DynamicShape')
    for v in key.data:v.co.x-=10
    key.value=0;key.keyframe_insert(data_path='value',frame=1)
    key.value=1;key.keyframe_insert(data_path='value',frame=2)
    key.value=0;key.keyframe_insert(data_path='value',frame=3)
    # Local deformation is ten units, but world scale is eight: compensate.
    for v,b in zip(key.data,wall.data.vertices):v.co.x=b.co.x-1.25
    deformed=check_camera_visibility(job);record('deforming_wall_bvh_is_updated',deformed,False)
    assert any(x.get('frame')==2 and x['code']=='subject_occluded' for x in deformed['failures'])
    wall.data.shape_keys.animation_data_clear();key.value=0;wall.hide_render=True
    behind=box('Wall_coplanar_behind_front_surface',(1.2,0,1),(.6,.08,1.8))
    focus_only={'version':1,'shots':[{'camera':'Camera','actor_ids':[],'focus_objects':[target.name]}]}
    record('target_front_surface_prevents_center_ray_false_block',check_camera_visibility(job,focus_only))
    behind.hide_render=True
    far_leg=box('Far_Chair_Leg',(1.2,.18,.8),(.12,.08,1.2))
    single_part={'version':1,'shots':[{'camera':'Camera','actor_ids':[], 'focus_objects':[far_leg.name]}]}
    record('independent_hidden_part_is_occluded',check_camera_visibility(job,single_part),False)
    grouped={'version':1,'shots':[{'camera':'Camera','actor_ids':['A'],'focus_objects':[],
                      'focus_groups':{'logical_chair':[target.name,far_leg.name]}}]}
    record('composite_prop_does_not_fail_on_its_own_parts',check_camera_visibility(job,grouped))
    wall.hide_render=False;wall.location.x=0
    group_block=check_camera_visibility(job);record('outside_wall_still_blocks_whole_composite_prop',group_block,False)
    assert any(x.get('subject')=='logical_chair' and x['code']=='subject_occluded' for x in group_block['failures'])
    wall.hide_render=True;wall.location.x=10;far_leg.hide_render=True
    # Render-disabled collections must not become occluders.
    collection=bpy.data.collections.new('Hidden collection');scene.collection.children.link(collection)
    for old in list(wall.users_collection):old.objects.unlink(wall)
    collection.objects.link(wall);collection.hide_render=True;wall.hide_render=False;wall.location.x=0
    record('hidden_render_collection_is_not_occluder',check_camera_visibility(job,contract))
    collection.hide_render=False;wall.hide_render=False
    group=wall.vertex_groups.new(name='RenderMask');group.add(list(range(len(wall.data.vertices))),1.,'REPLACE')
    mask=wall.modifiers.new('Any_render_only_mask','MASK');mask.vertex_group=group.name
    mask.threshold=.15;mask.invert_vertex_group=True;mask.show_render=True;mask.show_viewport=False
    record('render_only_mask_faces_do_not_become_false_occluders',check_camera_visibility(job))
    mask.show_render=False
    record('inactive_render_mask_keeps_real_opaque_occlusion',check_camera_visibility(job),False)
    wall.hide_render=True
    expected=copy.deepcopy(contract);shot=expected['shots'][0]
    shot.update(fixed_transform=False,expected_position=[0,-6,2.5],expected_azimuth_deg=0,
                expected_elevation_deg=math.degrees(math.atan2(1.6,6)),expected_fov_deg=math.degrees(data.angle_x))
    record('explicit_position_angle_elevation_fov_and_gaze_match',check_camera_visibility(job,expected))
    data.lens=40
    optical=check_camera_visibility(job);record('explicit_fov_change_detected',optical,False)
    assert any(x['code']=='camera_expected_fov' for x in optical['failures'])
    data.lens=35;camera.rotation_euler.z+=.1
    gaze=check_camera_visibility(job);record('explicit_look_at_gaze_change_detected',gaze,False)
    assert any(x['code']=='camera_expected_gaze' for x in gaze['failures'])
    camera.rotation_euler=(Vector((0,0,.9))-camera.location).to_track_quat('-Z','Y').to_euler()
    second_data=data.copy();second=bpy.data.objects.new('Camera2',second_data);scene.collection.objects.link(second);second.matrix_world=camera.matrix_world.copy()
    marker=scene.timeline_markers.new('First',frame=1);marker.camera=camera
    marker=scene.timeline_markers.new('Cut',frame=2);marker.camera=second
    record('unregistered_actual_camera_window_fails',check_camera_visibility(job,contract),False)
    multi=copy.deepcopy(contract);multi['shots'].append({'camera':'Camera2','frame_start':2,'frame_end':3,
                   'actor_ids':['A'],'focus_objects':[],'framing':'regions','regions':['trunk']})
    record('real_marker_closeup_region_window_passes',check_camera_visibility(job,multi))
    scene.timeline_markers.clear();scene.camera=camera
    camera.keyframe_insert(data_path='location',frame=1);camera.location.x+=.3;camera.keyframe_insert(data_path='location',frame=3)
    # Reuse the registered baseline, do not silently reset it in the getter.
    changed=check_camera_visibility(job);record('fixed_camera_movement_detected',changed,False)
    assert any(x['code']=='camera_fixed_transform_changed' for x in changed['failures'])
    camera.animation_data_clear();camera.location.x=0;scene.frame_set(1);bpy.context.view_layer.update()
    camera_requirements(job,contract)
    agent_scene.analyze_job(job,{'actor_count':1,'camera':'fixed full body with arbitrary prop'})
    result=agent_scene.submit_scene(job);assert result['ok']
    manifest_path=folder/'agent_scene.json';manifest=json.loads(manifest_path.read_text())
    assert manifest['camera_visibility_version']==1 and manifest['camera_visibility_requirements']
    reloaded=probe_scene(manifest_path);assert reloaded['passed'],reloaded
    checks.append({'name':'new_manifest_contract_and_savedscene_visibility_reload','passed':True,
                   'visibility':reloaded['camera_visibility']})
    del bpy.context.scene[STORE]
    missing=check_camera_visibility(job);assert missing['status']=='missing'
    result=agent_scene.submit_scene(job);assert result['ok']
    missing_probe=probe_scene(manifest_path);assert not missing_probe['passed'] and any(e['code']=='camera_visibility' for e in missing_probe['errors'])
    checks.append({'name':'new_submission_missing_contract_reports_structured_error','passed':True})
    legacy=json.loads(manifest_path.read_text());legacy.pop('camera_visibility_version');legacy.pop('camera_visibility_requirements');legacy.pop('camera_visibility_status')
    manifest_path.write_text(json.dumps(legacy));legacy_probe=probe_scene(manifest_path)
    assert legacy_probe['passed'],legacy_probe
    checks.append({'name':'historical_manifest_without_camera_version_stays_compatible','passed':True})
    report={'passed':all(x['passed'] for x in checks),'checks':checks,'scope':'Independent headless evaluated geometry and save/reload; original Beta and free-scene props. No GUI, render, pixel or action naturalness certification.',
            'created_at':time.time(),'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'scripts/agent_camera_quality.py',ROOT/'scripts/agent_scene.py',ROOT/'scripts/agent_scene_probe.py',Path(__file__)]}}
    path=out/'validation.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print('CAMERA_VISIBILITY_PROOF',str(path),'checks',len(checks),flush=True)


if __name__=='__main__':main()
