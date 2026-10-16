"""Real independently registered CC0 character, clean-clone fallback and motion.

No Beta asset/cache/history is changed. A new /tmp workspace contains copied
CC0 profile assets; the simulated clone deliberately has no private Beta data.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))
PROFILE='quaternius_mannequin_f'


def points(actor):
    import bpy
    arm=actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {bone.name:list(arm.matrix_world@bone.head) for bone in arm.pose.bones}


def main():
    import bpy
    import duel_plan
    from mathutils import Vector
    import agent_character as characters
    from agent_motion import prepare_motion,sample_motion
    from agent_motion_controls import set_pose
    from agent_motion_timeline import register_event,motion_commit
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    parser.add_argument('--reload',action='store_true')
    parser.add_argument('--render',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output=args.output or Path(tempfile.mkdtemp(prefix='bamo_cc0_character_'))
    clone=output/'clone';clone_catalog=clone/'knowledge/characters/catalog.json'
    duel_plan.JOBS=output/'jobs';job='cc0_starter'
    if args.reload:
        with patch.object(characters,'PROJECT',clone),patch.object(characters,'CATALOG',clone_catalog):
            assert characters.default_character_id()==PROFILE
            actor=characters.resolve_actor(job,'Person')
            expected=json.loads(bpy.context.scene['cc0_test_expected'])
            for frame,reference in expected.items():
                bpy.context.scene.frame_set(int(frame));bpy.context.view_layer.update()
                actual=points(actor)
                assert max((Vector(actual[name])-Vector(value)).length for name,value in reference.items())<2e-5
            assert not characters.scene_actor_facts(job)['errors']
        print('CC0_CHARACTER_RELOAD_PASS',flush=True)
        return
    output.mkdir(parents=True,exist_ok=True)
    assert not (output/'scene.blend').exists(),'Use a new output directory'
    # Preserve the local Beta default and an explicitly registered CC0 actor.
    assert characters.default_character_id()=='beta'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    (duel_plan.JOBS/job).mkdir(parents=True)
    (duel_plan.JOBS/job/'request.json').write_text('{"prompt":"独立CC0真实蒙皮角色与公开动作，干净克隆启动测试","character_policy":"library_beta"}',encoding='utf-8')
    first=characters.actor_ensure(job,'Existing',character_id=PROFILE)
    assert characters.actor_ensure(job,'Existing')['character_id']==PROFILE
    assert characters.actor_ensure(job,'Existing')['root_object']==first['root_object']
    try:
        characters.actor_ensure(job,'Existing',character_id='beta')
        raise AssertionError('Explicit conflicting profile was ignored')
    except ValueError as error:
        assert '另一人物资产' in str(error)
    # All assets in this character-only clone are redistributable CC0.
    destination=clone/'bundled_assets/characters'/PROFILE
    shutil.copytree(PROJECT/'bundled_assets/characters'/PROFILE,destination)
    clone_catalog.parent.mkdir(parents=True)
    shutil.copy2(characters.CATALOG,clone_catalog)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=90;scene.render.fps=30
    with patch.object(characters,'PROJECT',clone),patch.object(characters,'CATALOG',clone_catalog):
        assert characters.default_character_id()==PROFILE
        try:
            characters.character_info('beta')
            raise AssertionError('Explicit Beta was silently replaced')
        except ValueError as error:
            assert '源资产缺失' in str(error)
        info=characters.actor_ensure(job,'Person',yaw=.35)
        assert info['character_id']==PROFILE
        actor=characters.resolve_actor(job,'Person')
        assert len(actor['meshes'])==1 and len(actor['meshes'][0].data.vertices)==7424
        assert len(actor['armature'].data.bones)==65
        arm=actor['armature']
        rest_point=lambda name:arm.matrix_basis@arm.data.bones['mixamorig:'+name].head_local
        across=rest_point('LeftArm')-rest_point('RightArm')
        toe=rest_point('LeftToeBase')-rest_point('LeftFoot')
        assert across.normalized().dot(Vector((1,0,0)))>.99,across
        assert toe.normalized().dot(Vector((0,-1,0)))>.5,toe
        assert characters.actor_ensure(job,'Person')['character_id']==PROFILE
        imported=set(bpy.data.objects)
        maximum_error=0.
        maximum_walk_wrist_over_shoulder=-100.
        walk_root_displacement=None
        for identifier in ('quaternius.punch_jab','quaternius.punch_cross','quaternius.death01',
                           'quaternius.idle_loop','quaternius.walk_loop','quaternius.sprint_loop'):
            measured=prepare_motion(actor['armature'],identifier)
            original=actor['root'].location.copy()
            for time in (0.,measured['duration_s']*.5,measured['duration_s']):
                preserved=sample_motion(actor['armature'],identifier,time,root_mode='preserve')
                extracted=sample_motion(actor['armature'],identifier,time)
                actor['root'].location=original;set_pose(actor['armature'],preserved['pose']);bpy.context.view_layer.update()
                reference=points(actor)
                actor['root'].location=original+actor['root'].matrix_basis.to_quaternion()@Vector(extracted['root'])
                set_pose(actor['armature'],extracted['pose']);bpy.context.view_layer.update()
                actual=points(actor)
                maximum_error=max(maximum_error,max((Vector(actual[name])-Vector(value)).length for name,value in reference.items() if name!='MotionRoot'))
                if identifier in ('quaternius.walk_loop','quaternius.idle_loop'):
                    for side in ('Left','Right'):
                        wrist=actual['mixamorig:'+side+'Hand'][2]
                        shoulder=actual['mixamorig:'+side+'Arm'][2]
                        elbow=actual['mixamorig:'+side+'ForeArm'][2]
                        head=actual['mixamorig:Head'][2]
                        maximum_walk_wrist_over_shoulder=max(maximum_walk_wrist_over_shoulder,wrist-shoulder)
                        assert wrist<shoulder-.1, ('Walk/Idle arms lifted',identifier,time,side,wrist,shoulder)
                        assert elbow<head-.1, ('Walk/Idle elbow above head',identifier,time,side,elbow,head)
            actor['root'].location=original
            if identifier=='quaternius.walk_loop':
                walk_root_displacement=Vector(sample_motion(actor['armature'],identifier,measured['duration_s'])['root'])
                assert walk_root_displacement.normalized().dot(Vector((0,-1,0)))>.9,walk_root_displacement
        assert maximum_error<2e-5,maximum_error
        assert imported==set(bpy.data.objects),'Imported source characters survived'
        identifier='quaternius.walk_loop'
        measured=prepare_motion(actor['armature'],identifier)
        end=1+round(measured['duration_s']*30)
        scene.frame_end=end
        register_event(job,'Person','real_cc0_source',{'event_id':'real_cc0_source','source':'library',
             'motion_id':identifier,'start_frame':1,'end_frame':end,'time_scale':1.,'fit':False,'root_strategy':'source'})
        motion_commit(job,'Person',1,end)
        assert not characters.scene_actor_facts(job)['errors']
        references={}
        for frame in range(1,end+1):
            scene.frame_set(frame);bpy.context.view_layer.update();references[str(frame)]=points(actor)
        scene['cc0_test_expected']=json.dumps(references)
        scene.world=bpy.data.worlds.new('World');scene.world.color=(.3,.3,.3)
        scene.render.engine='BLENDER_WORKBENCH';scene.display.shading.color_type='MATERIAL'
        scene.display.shading.show_cavity=True;scene.display.shading.show_shadows=True
        scene.render.resolution_x,scene.render.resolution_y=720,480;scene.render.resolution_percentage=100
        data=bpy.data.cameras.new('CC0Wide');camera=bpy.data.objects.new('CC0Wide',data);scene.collection.objects.link(camera)
        corners=[]
        for frame in range(1,end+1):
            scene.frame_set(frame);bpy.context.view_layer.update()
            for mesh in actor['meshes']:
                evaluated=mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
                corners.extend(evaluated.matrix_world@Vector(corner) for corner in evaluated.bound_box)
        center=Vector(tuple((max(point[i] for point in corners)+min(point[i] for point in corners))*.5 for i in range(3)))
        camera.location=center+Vector((3,-5,2.4));camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
        data.type='ORTHO';data.ortho_scale=3.4;scene.camera=camera
        bpy.context.view_layer.update()
        local=[camera.matrix_world.inverted()@point for point in corners]
        width=max(point.x for point in local)-min(point.x for point in local)
        height=max(point.y for point in local)-min(point.y for point in local)
        data.ortho_scale=max(width,height*720/480)*1.15
        from bpy_extras.object_utils import world_to_camera_view
        projected=[world_to_camera_view(scene,camera,point) for point in corners]
        assert all(.02<point.x<.98 and .02<point.y<.98 and point.z>0 for point in projected)
        frames=output/'frames';frames.mkdir()
        selected=range(1,end+1) if args.render else sorted({1,1+round((end-1)*.4),end})
        for frame in selected:
            scene.frame_set(frame);scene.render.filepath=str(frames/('frame_%04d.png'%frame));bpy.ops.render.render(write_still=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output/'scene.blend'),check_existing=False)
    child=subprocess.run([bpy.app.binary_path,'-b',str(output/'scene.blend'),'-t','2','--python-exit-code','1','--python',str(Path(__file__).resolve()),'--','--reload','--output',str(output)],capture_output=True,text=True)
    print(child.stdout[-1000:],flush=True);assert child.returncode==0,child.stderr[-2000:]
    if args.render:
        subprocess.run([str(PROJECT/'ffmpeg'),'-hide_banner','-loglevel','error','-framerate','30','-i',str(frames/'frame_%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(output/'cc0_original_motion.mp4')],check=True)
    print('CC0_CHARACTER_ACCEPTANCE '+json.dumps({'passed':True,'profile':PROFILE,'maximum_extraction_equivalence_error_m':maximum_error,
          'maximum_walk_idle_wrist_over_shoulder_m':maximum_walk_wrist_over_shoulder,
          'walk_root_displacement_m':list(walk_root_displacement),
          'clean_clone_default':PROFILE,'local_default_preserved':'beta','output':str(output)}),flush=True)


if __name__=='__main__':main()
