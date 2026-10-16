"""Rokoko Mixamo 52/53-bone public sampling onto the original Beta skin.

Headless Blender test; writes only a new /tmp directory and stdout. Uses the ordinary catalog and public sampling APIs. Scene baking/reload also
uses public timeline operations.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'scripts'))

SOURCES = {
 'rokoko.mixamo.09_slowwalkforward_mixamo_769': {'asset': 'rokoko_staging/mixamo/09-SlowWalkForward_MIXAMO_769.fbx', 'expected_bones':53, 'expected_hips':'armature_object'},
 'rokoko.mixamo.boxing_mixamo': {'asset': 'rokoko_staging/mixamo/Boxing_mixamo.fbx', 'expected_bones':52, 'expected_hips':'bone'},
}


def point_state(actor):
    import bpy
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {bone.name: arm.matrix_world @ bone.head for bone in arm.pose.bones}


def sample_checks(actor, identifier):
    import bpy
    from mathutils import Vector
    import agent_motion
    from agent_motion_controls import set_pose
    from agent_character import binding_facts
    scene = bpy.context.scene
    original_objects, original_actions = set(bpy.data.objects), set(bpy.data.actions)
    original_frame = scene.frame_current, scene.frame_subframe
    original_fps = scene.render.fps, scene.render.fps_base
    binding = binding_facts(actor['meshes'], actor['armature'])['signature']
    root_start = actor['root'].location.copy()
    info = agent_motion.prepare_motion(actor['armature'], identifier)
    assert info['compatibility']['source_bone_count'] == SOURCES[identifier]['expected_bones']
    assert info['compatibility']['hips_source'] == SOURCES[identifier]['expected_hips']
    assert not info['skin_replaced']
    assert original_objects == set(bpy.data.objects) and original_actions == set(bpy.data.actions)
    errors, hip_heights, roots, poses = [], [], [], []
    for fraction in (0, .1, .25, .5, .75, 1):
        seconds = info['duration_s']*fraction
        preserved = agent_motion.sample_motion(actor['armature'], identifier, seconds, root_mode='preserve')
        sampled = agent_motion.sample_motion(actor['armature'], identifier, seconds)
        actor['root'].location = root_start
        set_pose(actor['armature'], preserved['pose']); bpy.context.view_layer.update()
        reference = point_state(actor)
        actor['root'].location = root_start+actor['root'].matrix_basis.to_quaternion() @ Vector(sampled['root'])
        set_pose(actor['armature'], sampled['pose']); bpy.context.view_layer.update()
        actual = point_state(actor)
        errors.append(max((actual[name]-reference[name]).length for name in actual))
        hip_heights.append(actual['mixamorig:Hips'].z)
        roots.append(sampled['root']); poses.append(sampled['pose'])
        assert abs(sampled['source_frame']-(info['source_frame_range'][0]+seconds*info['key_timebase_fps'])) < 1e-5
    assert max(errors) < 2e-5, errors
    assert max(hip_heights)-min(hip_heights) > .01, 'Vertical pelvis motion disappeared'
    assert math.dist(roots[0], roots[-1]) > .02, 'Planar source motion disappeared'
    assert any(abs(poses[-1][name]['rotation_quaternion'][i]-poses[1][name]['rotation_quaternion'][i]) > .02
               for name in poses[0] for i in range(4)), 'Source pose animation disappeared'
    assert binding_facts(actor['meshes'], actor['armature'])['signature'] == binding
    assert original_objects == set(bpy.data.objects) and original_actions == set(bpy.data.actions)
    assert (scene.frame_current, scene.frame_subframe) == original_frame
    assert (scene.render.fps, scene.render.fps_base) == original_fps
    actor['root'].location = root_start
    return {'motion_id':identifier, 'duration_s':info['duration_s'], 'source_fps':info['source_fps'],
            'key_timebase_fps':info['key_timebase_fps'], 'source_bones':info['compatibility']['source_bone_count'],
            'hips_source':info['compatibility']['hips_source'],
            'maximum_extraction_equivalence_error_m':max(errors),
            'pelvis_vertical_range_m':max(hip_heights)-min(hip_heights),
            'planar_end_displacement_m':math.dist(roots[0],roots[-1])}


def visual_setup():
    import bpy
    from mathutils import Vector
    s=bpy.context.scene
    s.world=bpy.data.worlds.new('World');s.world.color=(.3,.3,.3)
    s.render.engine='BLENDER_WORKBENCH';s.display.shading.color_type='MATERIAL'
    s.display.shading.show_shadows=True;s.display.shading.show_cavity=True
    s.render.resolution_x,s.render.resolution_y=720,480;s.render.resolution_percentage=100
    data=bpy.data.cameras.new('MotionReview');camera=bpy.data.objects.new('MotionReview',data)
    s.collection.objects.link(camera);s.camera=camera;data.type='ORTHO';data.ortho_scale=3.2
    return camera


def main():
    import bpy
    import duel_plan
    import agent_motion
    from mathutils import Vector
    from agent_character import actor_ensure,resolve_actor
    from agent_motion_timeline import register_event,motion_commit
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    parser.add_argument('--reload',action='store_true')
    parser.add_argument('--render-boxing',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output=args.output or Path(tempfile.mkdtemp(prefix='bamo_s1_rokoko_sampler_'))
    output.mkdir(parents=True,exist_ok=True)
    duel_plan.JOBS=output/'jobs'
    job='rokoko_sampler'
    if args.reload:
        actor=resolve_actor(job,'Person')
        expected=json.loads(bpy.context.scene['rokoko_test_expected'])
        for frame in (1,30,60,75,105,135):
            bpy.context.scene.frame_set(frame);bpy.context.view_layer.update()
            actual=point_state(actor)
            assert all(math.isfinite(value) for point in actual.values() for value in point)
            assert max((actual[name]-Vector(point)).length for name,point in expected[str(frame)].items())<2e-5
        assert len([o for o in bpy.context.scene.objects if o.type=='ARMATURE'])==1
        print('ROKOKO_SAMPLER_RELOAD_PASS',flush=True)
        return
    assert not (output/'scene.blend').exists(),'Use a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=135
    scene.render.fps,scene.render.fps_base=24,1.001
    scene.frame_set(7,subframe=.25)
    folder=duel_plan.JOBS/job;folder.mkdir(parents=True)
    (folder/'request.json').write_text('{"prompt":"Rokoko真人慢走与徒手拳击素材，原Beta原速采样验证","character_policy":"library_beta"}',encoding='utf-8')
    actor_ensure(job,'Person',position=(.3,-.2,0),yaw=.55)
    actor=resolve_actor(job,'Person')
    results=[]
    for identifier in SOURCES:
        results.append(sample_checks(actor,identifier))
    # Two short windows at scene 30fps remain time_scale=1. They are
    # chosen for source transfer/reload, not claimed contact choreography.
    scene.render.fps,scene.render.fps_base=30,1.
    for index,identifier in enumerate(SOURCES):
        first=1+index*75;last=first+59
        offset=1. if index==0 else 4.5
        register_event(job,'Person','source_'+str(index),{'source':'library','event_id':'source_'+str(index),
            'motion_id':identifier,'start_frame':first,'end_frame':last,
            'source_window':[offset,offset+59/30], 'time_scale':1.,'fit':False,'root_strategy':'source'})
    motion_commit(job,'Person',1,135)
    camera=visual_setup()
    images=output/'keyframes';images.mkdir()
    expected={}
    for frame in (1,30,60,75,105,135):
        scene.frame_set(frame);bpy.context.view_layer.update()
        expected[str(frame)]={name:list(point) for name,point in point_state(actor).items()}
    scene['rokoko_test_expected']=json.dumps(expected)
    for frame in (1,30,60,76,105,135):
        scene.frame_set(frame);bpy.context.view_layer.update()
        arm=actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        hip=arm.matrix_world@arm.pose.bones['mixamorig:Hips'].head
        center=Vector((hip.x,hip.y,.9));camera.location=center+Vector((3,-4,1.8))
        camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
        scene.render.filepath=str(images/('frame_%04d.png'%frame));bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'scene.blend'),check_existing=False)
    child=subprocess.run([bpy.app.binary_path,'-b',str(output/'scene.blend'),'-t','2','--python-exit-code','1',
                          '--python',str(Path(__file__).resolve()),'--','--reload','--output',str(output)],capture_output=True,text=True)
    print(child.stdout[-1000:],flush=True)
    assert child.returncode==0,child.stderr[-2000:]
    if args.render_boxing:
        frames=output/'boxing_frames';frames.mkdir()
        for frame in range(76,136):
            scene.frame_set(frame)
            scene.render.filepath=str(frames/('frame_%04d.png'%(frame-75)))
            bpy.ops.render.render(write_still=True)
        subprocess.run([str(PROJECT/'ffmpeg'),'-hide_banner','-loglevel','error','-framerate','30',
                        '-i',str(frames/'frame_%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',
                        str(output/'boxing_original_speed_2s.mp4')],check=True)
    print('ROKOKO_SAMPLER_ACCEPTANCE '+json.dumps({'passed':True,'checks':results,'output':str(output)}),flush=True)


if __name__=='__main__':main()
