"""Real CMU body sampling on both registered skins, original speed and reload.

Uses public catalog/timeline APIs. Output is a new /tmp directory; no paid
agent, production job, old video or source character is modified.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))
PROFILES=('beta','quaternius_mannequin_f')
IDS=('cmu.02_01','cmu.07_01','cmu.02_03','cmu.79_08','cmu.80_10')


def points(actor):
    import bpy
    arm=actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {b.name:arm.matrix_world@b.head for b in arm.pose.bones}


def mesh_floor(actor):
    import bpy
    graph=bpy.context.evaluated_depsgraph_get()
    return min((obj.evaluated_get(graph).matrix_world@v.co).z for obj in actor['meshes']
               for v in obj.evaluated_get(graph).data.vertices)


def check_default_action_start(actors, origins):
    """A direct uncropped event begins in the action, at the actor's origin."""
    import bpy
    from mathutils import Vector
    bpy.context.scene.frame_set(1);bpy.context.view_layer.update()
    values={}
    for profile,actor in zip(PROFILES,actors):
        actual=points(actor)
        gaps=[actual['mixamorig:'+side+'Arm'].z-actual['mixamorig:'+side+'Hand'].z
              for side in ('Left','Right')]
        assert min(gaps)>.2,('Default library event starts in calibration T',profile,gaps)
        error=(actor['root'].location-Vector(origins[profile])).length
        assert error<2e-5,('Default window did not zero its first root',profile,error)
        values[profile]={'minimum_shoulder_above_wrist_m':min(gaps),'initial_root_error_m':error}
    return values


def inspect_source(actor,identifier):
    import bpy
    from mathutils import Vector
    from agent_motion import prepare_motion,sample_motion
    from agent_motion_controls import set_pose
    from agent_character import binding_facts
    scene=bpy.context.scene
    before=(set(bpy.data.objects),set(bpy.data.actions))
    clock=(scene.frame_current,scene.frame_subframe,scene.render.fps,scene.render.fps_base)
    signature=binding_facts(actor['meshes'],actor['armature'])['signature']
    initial=actor['root'].location.copy()
    info=prepare_motion(actor['armature'],identifier)
    assert info['compatibility']['source_bone_count']==43
    assert info['source_fps']==30 and info['key_timebase_fps']==30
    assert info['source_frame_range'][0]==1 and not info['skin_replaced']
    assert .005<info['retarget_scale']<.025,'Centimetres were not converted to character metres'
    errors=[];hips=[];low_arms=[];extensions={'Left':[],'Right':[]};forward={'Left':[],'Right':[]};floors=[]
    times=[i/30 for i in range(1,round(info['duration_s']*30)+1)]
    for i,time in enumerate(times):
        extracted=sample_motion(actor['armature'],identifier,time)
        # Check extraction against the independently applicable full-pelvis mode.
        if i%13==0 or i==len(times)-1:
            preserved=sample_motion(actor['armature'],identifier,time,root_mode='preserve')
            actor['root'].location=initial;set_pose(actor['armature'],preserved['pose']);bpy.context.view_layer.update()
            reference=points(actor)
        actor['root'].location=initial+actor['root'].matrix_basis.to_quaternion()@Vector(extracted['root'])
        set_pose(actor['armature'],extracted['pose']);bpy.context.view_layer.update()
        actual=points(actor)
        if i%13==0 or i==len(times)-1:
            errors.append(max((value-reference[name]).length for name,value in actual.items() if name!='MotionRoot'))
        assert abs(extracted['source_frame']-(1+time*30))<1e-5,'Original source timing changed'
        hips.append(actual['mixamorig:Hips'].z)
        if i%10==0:floors.append(mesh_floor(actor))
        for side in ('Left','Right'):
            shoulder,elbow,hand=[actual['mixamorig:'+side+part] for part in ('Arm','ForeArm','Hand')]
            extensions[side].append((hand-shoulder).length/((elbow-shoulder).length+(hand-elbow).length))
            local=actor['root'].matrix_world.inverted()@(hand-actual['mixamorig:Hips']+actor['root'].matrix_world.translation)
            forward[side].append(-local.y)
            if identifier in ('cmu.02_01','cmu.07_01'):
                low_arms.append(hand.z-shoulder.z)
                assert hand.z<shoulder.z-.1,('Walk has lifted wrists',identifier,time,side,hand.z,shoulder.z)
                assert elbow.z<actual['mixamorig:Head'].z-.1,('Walk elbow is above head',identifier,time,side)
    assert max(errors)<2e-5,errors
    assert max(hips)-min(hips)>.01,'Vertical source pelvis motion disappeared'
    end_root=Vector(sample_motion(actor['armature'],identifier,info['duration_s'])['root'])
    if identifier in ('cmu.02_01','cmu.07_01','cmu.02_03'):
        assert end_root.length>2.,('Source travel lost',identifier,list(end_root))
        assert end_root.normalized().dot(Vector((0,-1,0)))>.9,('Travel heading reversed',identifier,list(end_root))
    if identifier.startswith(('cmu.79','cmu.80')):
        assert any(max(values)>.9 and max(values)-min(values)>.2 for values in extensions.values()),('No punch extension',extensions)
        assert any(max(values)-min(values)>.2 for values in forward.values()),'No forward hand excursion'
    assert before==(set(bpy.data.objects),set(bpy.data.actions)),'Temporary source actors/actions remained'
    assert clock==(scene.frame_current,scene.frame_subframe,scene.render.fps,scene.render.fps_base)
    assert binding_facts(actor['meshes'],actor['armature'])['signature']==signature
    actor['root'].location=initial
    return {'motion_id':identifier,'duration_s':info['duration_s'],'retarget_scale':info['retarget_scale'],
            'root_last_m':list(end_root),'pelvis_vertical_range_m':max(hips)-min(hips),
            'extraction_error_m':max(errors),'sampled_skin_floor_range_m':[min(floors),max(floors)],
            'maximum_walk_wrist_over_shoulder_m':max(low_arms) if low_arms else None,
            'arm_extension_range':{side:[min(values),max(values)] for side,values in extensions.items()}}


def setup_view(actors,end):
    import bpy
    from mathutils import Vector
    from bpy_extras.object_utils import world_to_camera_view
    scene=bpy.context.scene
    scene.world=bpy.data.worlds.new('CMUWorld');scene.world.color=(.32,.32,.32)
    scene.render.engine='BLENDER_WORKBENCH';scene.display.shading.color_type='MATERIAL'
    scene.display.shading.show_shadows=True;scene.display.shading.show_cavity=True
    scene.render.resolution_x,scene.render.resolution_y=960,540;scene.render.resolution_percentage=100
    if 'CMUGround' not in scene.objects:
        bpy.ops.mesh.primitive_plane_add(size=30, location=(0,0,0))
        bpy.context.object.name='CMUGround'
        material=bpy.data.materials.new('CMUGroundMat');material.diffuse_color=(.44,.44,.44,1)
        bpy.context.object.data.materials.append(material)
    data=bpy.data.cameras.new('CMUFullBody');camera=bpy.data.objects.new('CMUFullBody',data);scene.collection.objects.link(camera)
    scene.camera=camera;data.type='ORTHO'
    corners=[]
    for frame in range(1,end+1):
        scene.frame_set(frame);bpy.context.view_layer.update()
        for actor in actors:
            for obj in actor['meshes']:
                mesh=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
                corners.extend(mesh.matrix_world@Vector(corner) for corner in mesh.bound_box)
    center=Vector(tuple((min(p[i] for p in corners)+max(p[i] for p in corners))/2 for i in range(3)))
    camera.location=center+Vector((4,-6,3));camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
    bpy.context.view_layer.update()
    local=[camera.matrix_world.inverted()@p for p in corners]
    data.ortho_scale=max(max(p.x for p in local)-min(p.x for p in local),
        (max(p.y for p in local)-min(p.y for p in local))*960/540)*1.18
    assert all(.02<p.x<.98 and .02<p.y<.98 and p.z>0 for p in (world_to_camera_view(scene,camera,p) for p in corners))


def main():
    import bpy
    from mathutils import Vector
    import duel_plan
    from agent_character import actor_ensure,resolve_actor,scene_actor_facts
    from agent_motion import prepare_motion
    from agent_motion_timeline import register_event,motion_commit
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);parser.add_argument('--reload',action='store_true');parser.add_argument('--render',action='store_true')
    parser.add_argument('--default-window-only',action='store_true',help='Only the short direct-event calibration/reload regression; no rendering')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output=args.output or Path(tempfile.mkdtemp(prefix='bamo_cmu_sampler_'))
    output.mkdir(parents=True,exist_ok=True);duel_plan.JOBS=output/'jobs'
    if args.reload:
        expected=json.loads(bpy.context.scene['cmu_expected'])
        for frame,actors in expected.items():
            bpy.context.scene.frame_set(int(frame));bpy.context.view_layer.update()
            for profile,reference in actors.items():
                actor=resolve_actor('cmu_source',profile)
                actual=points(actor)
                assert max((actual[name]-Vector(value)).length for name,value in reference.items())<2e-5
        assert len([o for o in bpy.context.scene.objects if o.type=='ARMATURE'])==2
        assert not scene_actor_facts('cmu_source')['errors']
        if bpy.context.scene.get('cmu_default_window_checked'):
            actors=[resolve_actor('cmu_source',profile) for profile in PROFILES]
            values=check_default_action_start(actors,json.loads(bpy.context.scene['cmu_default_start_origins']))
            print('CMU_DEFAULT_WINDOW_RELOAD_PASS '+json.dumps(values),flush=True)
        print('CMU_SAMPLER_RELOAD_PASS',flush=True);return
    assert not (output/'walking.blend').exists(),'Use a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.render.fps,scene.render.fps_base=24,1.001;scene.frame_set(7,subframe=.25)
    job='cmu_source';folder=duel_plan.JOBS/job;folder.mkdir(parents=True)
    (folder/'request.json').write_text('{"prompt":"CMU真人走路与拳击，原Beta和CC0原速素材适配验证","character_policy":"library_beta"}',encoding='utf-8')
    actors=[];results=[]
    for index,profile in enumerate(PROFILES):
        actor_ensure(job,profile,character_id=profile,position=((index-.5)*1.6,0,0))
        actor=resolve_actor(job,profile);actors.append(actor)
        for identifier in (() if args.default_window_only else IDS):
            result=inspect_source(actor,identifier);result['character_id']=profile;results.append(result)
    scene.render.fps,scene.render.fps_base=30,1.
    clips=(('walking','cmu.02_01'),) if args.default_window_only else (('walking','cmu.02_01'),('boxing','cmu.79_08'))
    for label,identifier in clips:
        # Separate clips test adaptation, without pretending a walk→fight cut is
        # a validated action transition. Every action begins after calibration.
        end=round(prepare_motion(actors[0]['armature'],identifier)['duration_s']*30)
        if args.default_window_only:end=2
        scene.frame_start,scene.frame_end=1,end
        origins={profile:list(actor['root'].location) for profile,actor in zip(PROFILES,actors)}
        for actor,profile in zip(actors,PROFILES):
            measured=prepare_motion(actor['armature'],identifier)
            assert measured['default_source_window']==[1/30,measured['duration_s']]
            register_event(job,profile,'cmu_source',{'source':'library','motion_id':identifier,
                'start_frame':1,'end_frame':end,
                **({'source_window':[1/30,end/30]} if label!='walking' else {}),
                'time_scale':1.,'fit':False,'root_strategy':'source'})
            motion_commit(job,profile,1,end)
        scene['cmu_default_window_checked']=label=='walking'
        if label=='walking':
            scene['cmu_default_start_origins']=json.dumps(origins)
            values=check_default_action_start(actors,origins)
            print('CMU_DEFAULT_WINDOW_START_PASS '+json.dumps(values),flush=True)
        if not args.default_window_only:setup_view(actors,end)
        expected={}
        for frame in range(1,end+1):
            scene.frame_set(frame);bpy.context.view_layer.update()
            expected[str(frame)]={profile:{n:list(p) for n,p in points(actor).items()} for profile,actor in zip(PROFILES,actors)}
        scene['cmu_expected']=json.dumps(expected)
        frames=output/(label+'_frames');frames.mkdir()
        selected=[] if args.default_window_only else (range(1,end+1) if args.render else sorted({1,end//2,end}))
        for frame in selected:
            scene.frame_set(frame);scene.render.filepath=str(frames/('frame_%04d.png'%frame));bpy.ops.render.render(write_still=True)
        destination=output/(label+'.blend');bpy.ops.wm.save_as_mainfile(filepath=str(destination),check_existing=False)
        child=subprocess.run([bpy.app.binary_path,'-b',str(destination),'-t','2','--python-exit-code','1','--python',str(Path(__file__).resolve()),'--','--reload','--output',str(output)],capture_output=True,text=True)
        print(child.stdout[-1000:],flush=True);assert child.returncode==0,child.stderr[-2000:]
        if args.render and not args.default_window_only:
            subprocess.run([str(PROJECT/'ffmpeg'),'-hide_banner','-loglevel','error','-framerate','30','-i',str(frames/'frame_%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(output/(label+'_original_speed.mp4'))],check=True)
    if args.default_window_only:
        print('CMU_DEFAULT_WINDOW_ACCEPTANCE '+json.dumps({'passed':True,'checks':values,'output':str(output)}),flush=True)
    else:
        print('CMU_SAMPLER_ACCEPTANCE '+json.dumps({'passed':True,'checks':results,'output':str(output)}),flush=True)


if __name__=='__main__':main()
