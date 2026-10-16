"""Limited real target validation of the next CMU core batch.

Three basic actions get semantic measurements, ordinary timeline/reload and
three grounded preview frames. Long repeated combat sources get sparse motion
samples and a single candidate preview window, not full-clip certification.
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
sys.path.insert(0,str(PROJECT/'tests'))
from test_agent_cmu_motion_blender import PROFILES,points,mesh_floor,setup_view
IDS=('cmu.16_17','cmu.16_33','cmu.22_14','cmu.69_16','cmu.76_11','cmu.76_01',
     'cmu.144_05','cmu.144_07','cmu.144_26')
PRIMARY=IDS[:3]
# Seconds in the actual clip; these are candidates reviewed below, not a new
# default crop or an assertion that the rest of the repeated performance fails.
WINDOWS={'cmu.144_05':([4.1,5.6],4+25/30),
         'cmu.144_07':([.6,2.],1.3),
         'cmu.144_26':([61/30,103/30],82/30)}


def pose_metrics(actor):
    from mathutils import Vector
    p=points(actor)
    across=p['mixamorig:LeftArm']-p['mixamorig:RightArm'];across.z=0;across.normalize()
    front=across.cross(Vector((0,0,1)))
    knees={side:math.degrees((p['mixamorig:'+side+'UpLeg']-p['mixamorig:'+side+'Leg']).angle(
        p['mixamorig:'+side+'Foot']-p['mixamorig:'+side+'Leg'])) for side in ('Left','Right')}
    return {'forward_xy':list(front)[:2],'heading':math.atan2(across.y,across.x),
            'hip_z':p['mixamorig:Hips'].z,'knee_degrees':knees,'skin_floor_m':mesh_floor(actor),
            'foot_z':{side:p['mixamorig:'+side+'Foot'].z for side in ('Left','Right')},
            'hand_over_shoulder_m':{side:p['mixamorig:'+side+'Hand'].z-p['mixamorig:'+side+'Arm'].z for side in ('Left','Right')}}


def sample_checks(actor,identifier):
    import bpy
    from mathutils import Vector
    from agent_motion import prepare_motion,sample_motion
    from agent_motion_controls import set_pose
    from agent_character import binding_facts
    initial=actor['root'].location.copy();signature=binding_facts(actor['meshes'],actor['armature'])['signature']
    before=set(bpy.data.objects),set(bpy.data.actions)
    info=prepare_motion(actor['armature'],identifier);duration=info['duration_s']
    assert info['key_timebase_fps']==30 and info['source_fps']==30
    assert info['compatibility']['source_bone_count']==43 and not info['skin_replaced']
    assert info['default_source_window']==[1/30,duration]
    if identifier in (*PRIMARY,'cmu.69_16','cmu.76_11','cmu.76_01'):
        times=[i/30 for i in range(1,round(duration*30)+1,3)]+[duration]
    else:
        window,peak=WINDOWS[identifier]
        times=sorted({1/30,duration*.25,duration*.5,duration*.75,duration,*window,peak})
    rows=[];maximum_error=0.
    for time in times:
        extracted=sample_motion(actor['armature'],identifier,time)
        preserved=sample_motion(actor['armature'],identifier,time,root_mode='preserve')
        actor['root'].location=initial;set_pose(actor['armature'],preserved['pose']);bpy.context.view_layer.update()
        reference=points(actor)
        actor['root'].location=initial+Vector(extracted['root'])
        set_pose(actor['armature'],extracted['pose']);bpy.context.view_layer.update()
        actual=points(actor)
        maximum_error=max(maximum_error,max((value-reference[name]).length for name,value in actual.items() if name!='MotionRoot'))
        row=pose_metrics(actor);row.update(time=time,root=extracted['root']);rows.append(row)
        assert abs(extracted['source_frame']-(1+time*30))<1e-5
    assert maximum_error<2e-5,maximum_error
    assert before==(set(bpy.data.objects),set(bpy.data.actions))
    assert signature==binding_facts(actor['meshes'],actor['armature'])['signature']
    headings=[rows[0]['heading']]
    for row in rows[1:]:headings.append(headings[-1]+(row['heading']-headings[-1]+math.pi)%(2*math.pi)-math.pi)
    heading_change=math.degrees(headings[-1]-headings[0])
    delta=Vector(rows[-1]['root'])-Vector(rows[0]['root'])
    velocities=[(Vector(b['root'])-Vector(a['root'])).length/(b['time']-a['time'])
                for a,b in zip(rows,rows[1:]) if b['time']>a['time']]
    heights=[row['hip_z'] for row in rows]
    if identifier=='cmu.16_17':
        assert 75<heading_change<125,('Left turn lost',heading_change)
        assert rows[-1]['forward_xy'][0]>.8 and delta.x>1 and delta.y<-.5
    elif identifier=='cmu.16_33':
        assert abs(heading_change)<20 and delta.y<-1
        assert sum(velocities[-3:])/3<sum(velocities[:3])/3*.2,('Walk did not stop',velocities)
    elif identifier=='cmu.22_14':
        assert rows[0]['forward_xy'][0]<-.8,('Source initial -X heading was discarded',rows[0]['forward_xy'])
        assert max(heights)-min(heights)>.5,('Crouch pelvis drop lost',heights)
        assert min(min(row['knee_degrees'].values()) for row in rows)<100
        assert abs(heights[-1]-heights[0])<.08 and delta.length<.12
    elif identifier=='cmu.69_16':assert 300<heading_change<360,('Whole-body turn lost',heading_change)
    elif identifier in ('cmu.76_01','cmu.76_11'):
        assert rows[0]['forward_xy'][0]<-.8
        assert delta.dot(Vector((*rows[0]['forward_xy'],0)))<-.5,('Backward source became forward',list(delta),rows[0]['forward_xy'])
    elif identifier=='cmu.144_05':assert max(row['foot_z']['Right'] for row in rows)>.9,'Kicking leg was forced to ground'
    elif identifier=='cmu.144_07':assert max(row['hand_over_shoulder_m']['Left'] for row in rows)>.1
    elif identifier=='cmu.144_26':assert max(row['hand_over_shoulder_m']['Right'] for row in rows)>.1
    actor['root'].location=initial
    return {'motion_id':identifier,'duration_s':duration,'retarget_scale':info['retarget_scale'],'sample_count':len(rows),
        'heading_change_deg':heading_change,'first_forward_xy':rows[0]['forward_xy'],'last_forward_xy':rows[-1]['forward_xy'],
        'root_motion_start_to_end_m':list(delta),'hip_z_range_m':[min(heights),max(heights)],
        'lowest_hip_source_time_seconds':min(rows,key=lambda row:row['hip_z'])['time'],
        'minimum_knee_degrees':min(min(row['knee_degrees'].values()) for row in rows),
        'first_mean_root_speed_m_s':sum(velocities[:3])/3,'last_mean_root_speed_m_s':sum(velocities[-3:])/3,
        'sampled_skin_floor_range_m':[min(row['skin_floor_m'] for row in rows),max(row['skin_floor_m'] for row in rows)],
        'extraction_error_m':maximum_error,
        **({'candidate_preview_window_seconds':WINDOWS[identifier][0],'sample_times_seconds':times} if identifier in WINDOWS else {})}


def main():
    import bpy
    from mathutils import Vector
    import duel_plan
    from agent_character import actor_ensure,resolve_actor,scene_actor_facts
    from agent_motion import prepare_motion
    from agent_motion_timeline import register_event,motion_commit
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);parser.add_argument('--reload',action='store_true')
    parser.add_argument('--only',nargs='+',choices=IDS,help='Validate only selected sources without repeating the batch')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output=args.output or Path(tempfile.mkdtemp(prefix='bamo_cmu_core_'));output.mkdir(parents=True,exist_ok=True)
    duel_plan.JOBS=output/'jobs';job='cmu_core'
    if args.reload:
        actors={profile:resolve_actor(job,profile) for profile in PROFILES}
        for frame,references in json.loads(bpy.context.scene['cmu_core_expected']).items():
            bpy.context.scene.frame_set(int(frame));bpy.context.view_layer.update()
            for profile,reference in references.items():
                actual=points(actors[profile])
                assert max((actual[name]-Vector(value)).length for name,value in reference.items())<2e-5
        assert not scene_actor_facts(job)['errors']
        assert len([o for o in bpy.context.scene.objects if o.type=='ARMATURE'])==2
        print('CMU_CORE_RELOAD_PASS '+bpy.context.scene['cmu_core_source'],flush=True);return
    assert not (output/'16_17.blend').exists(),'Use a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
    scene.render.fps,scene.render.fps_base=30,1.
    folder=duel_plan.JOBS/job;folder.mkdir(parents=True)
    (folder/'request.json').write_text('{"prompt":"CMU真人核心素材：转弯、走停、蹲起及局部招式候选验证","character_policy":"library_beta"}')
    actors=[]
    for index,profile in enumerate(PROFILES):
        actor_ensure(job,profile,character_id=profile,position=((index-.5)*1.8,0,0));actors.append(resolve_actor(job,profile))
    selected_ids=args.only or IDS;measurements={}
    for profile,actor in zip(PROFILES,actors):
        for identifier in selected_ids:
            result=sample_checks(actor,identifier);result['character_id']=profile
            measurements[(profile,identifier)]=result
            print('CMU_CORE_SAMPLE '+json.dumps(result),flush=True)
    for identifier in (identifier for identifier in (*PRIMARY,*WINDOWS) if identifier in selected_ids):
        duration=prepare_motion(actors[0]['armature'],identifier)['duration_s']
        window,peak=WINDOWS.get(identifier,([1/30,duration],None))
        if identifier=='cmu.22_14':peak=measurements[(PROFILES[0],identifier)]['lowest_hip_source_time_seconds']
        end=1+round((window[1]-window[0])*30);scene.frame_start,scene.frame_end=1,end
        for profile in PROFILES:
            event={'source':'library','motion_id':identifier,'start_frame':1,'end_frame':end,
                   'time_scale':1.,'fit':False,'root_strategy':'source'}
            if identifier in WINDOWS:event['source_window']=window
            register_event(job,profile,'core_source',event);motion_commit(job,profile,1,end)
        setup_view(actors,end)
        selected=sorted({1,end,1+round((peak-window[0])*30) if peak is not None else (end+1)//2})
        expected={}
        frames=output/identifier.split('.')[-1];frames.mkdir()
        for frame in selected:
            scene.frame_set(frame);bpy.context.view_layer.update()
            expected[str(frame)]={profile:{name:list(p) for name,p in points(actor).items()} for profile,actor in zip(PROFILES,actors)}
            scene.render.filepath=str(frames/('frame_%04d.png'%frame));bpy.ops.render.render(write_still=True)
        scene['cmu_core_expected']=json.dumps(expected);scene['cmu_core_source']=identifier
        destination=output/(identifier.split('.')[-1]+'.blend');bpy.ops.wm.save_as_mainfile(filepath=str(destination),check_existing=False)
        child=subprocess.run([bpy.app.binary_path,'-b',str(destination),'-t','2','--python-exit-code','1','--python',str(Path(__file__).resolve()),'--','--reload','--output',str(output)],capture_output=True,text=True)
        print(child.stdout[-800:],flush=True);assert child.returncode==0,child.stderr[-2000:]
    print('CMU_CORE_ACCEPTANCE_PASS '+json.dumps({'sources':list(selected_ids),'output':str(output)}),flush=True)


if __name__=='__main__':main()
