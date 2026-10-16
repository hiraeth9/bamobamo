"""Read a disposable saved scene and compare explicit ground/actor focus.

No job submission, source scene save, model call or report is performed. Camera
fitting changes only the in-memory copy; at most one EEVEE comparison is rendered.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))


def actor_points(scene,registry,frames):
    import bpy
    values={}
    for frame in frames:
        scene.frame_set(frame);bpy.context.view_layer.update()
        values[frame]={}
        for actor_id,item in registry.items():
            arm=bpy.data.objects[item['armature_object']].evaluated_get(bpy.context.evaluated_depsgraph_get())
            values[frame][actor_id]={bone.name:arm.matrix_world@bone.head for bone in arm.pose.bones}
    return values


def main():
    import bpy
    import duel_plan
    from agent_camera_quality import check_camera_visibility
    from agent_motion_cameras import camera_full
    parser=argparse.ArgumentParser();parser.add_argument('--scene',required=True,type=Path)
    parser.add_argument('--output',type=Path);parser.add_argument('--render',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output=args.output or Path(tempfile.mkdtemp(prefix='bamo_camera_framing_'));output.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(args.scene))
    scene=bpy.context.scene;job=scene['wm_actor_job_id'];registry=json.loads(scene['wm_actor_registry'])
    # Validation reads a new local request, never the historical failed job.
    duel_plan.JOBS=output/'jobs';folder=duel_plan.JOBS/job;folder.mkdir(parents=True)
    (folder/'request.json').write_text('{"prompt":"Isolated camera framing measurements from a disposable scene copy"}')
    assert scene.render.engine=='BLENDER_EEVEE'
    scene.render.resolution_x,scene.render.resolution_y=640,360;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'
    frames=(scene.frame_start,(scene.frame_start+scene.frame_end)//2,scene.frame_end)
    before=actor_points(scene,registry,frames)
    animated=[bpy.data.objects[item[key]] for item in registry.values() for key in ('root_object','armature_object')]
    actions=[obj.animation_data.action for obj in animated]
    environment=[obj for obj in scene.objects if obj.type=='LIGHT' or obj.name=='GroundPlane']
    environment_state={obj.name:obj.matrix_world.copy() for obj in environment}
    original_camera=scene.camera;original_transform=original_camera.matrix_world.copy()
    original=check_camera_visibility(job,frame_start=1,frame_end=3)
    assert original['passed'],original['failures']
    assert original['tested_frames']==3 and original['checked_frame_range']==[1,3]
    assert original['frame_sampling']=='every_frame_in_requested_interval'
    assert scene.camera==original_camera and original_camera.matrix_world==original_transform
    print('CAMERA_ORIGINAL_REQUESTED_INTERVAL '+json.dumps({'passed':original['passed'],'tested_frames':original['tested_frames'],
          'checked_frame_range':original['checked_frame_range'],'actor_framing':original['actor_framing']}),flush=True)
    spec=json.loads(scene['reference_camera_facts_json'])['requested']
    ground=camera_full(job,spec,actor_ids=list(registry),focus_objects=['GroundPlane'])
    actor=camera_full(job,spec,actor_ids=list(registry),focus_objects=[])
    for result,expected_focus in ((ground,['GroundPlane']),(actor,[])):
        visibility=result['visibility']
        assert result['focus_objects']==expected_focus,'Explicit focus changed'
        assert visibility['passed'] and visibility['tested_frames']==12
        assert visibility['frame_sampling']=='representative_frames' and len(visibility['helper_sample_frames'])==12
        assert all(item['sample_count']==12 for item in visibility['actor_framing'])
        assert 'Representative' in visibility['scope']
    small=ground['visibility']['actor_framing'][0]['height_fraction_range']
    large=actor['visibility']['actor_framing'][0]['height_fraction_range']
    assert max(small)<.15 and min(large)>.3,(small,large)
    after=actor_points(scene,registry,frames)
    maximum=max((point-after[frame][aid][bone]).length for frame,actors in before.items()
                for aid,bones in actors.items() for bone,point in bones.items())
    assert maximum<1e-6,maximum
    assert actions==[obj.animation_data.action for obj in animated]
    assert all(bpy.data.objects[name].matrix_world==matrix for name,matrix in environment_state.items())
    assert scene.render.engine=='BLENDER_EEVEE'
    if args.render:
        scene.frame_set(frames[1]);scene.render.filepath=str(output/'actor_focus_eevee.png')
        bpy.ops.render.render(write_still=True)
    def summary(result):
        return {key:result['visibility'][key] for key in ('passed','tested_frames','frame_sampling',
            'helper_sample_frames','actor_framing','framing_feedback','scope')}
    print('CAMERA_FRAMING_COMPARISON '+json.dumps({'passed':True,'ground_focus':summary(ground),
        'actor_focus':summary(actor),'maximum_actor_bone_change_m':maximum,
        'render':str(output/'actor_focus_eevee.png') if args.render else None,
        'scope':'In-memory focus comparison and representative frame measurements; no historical task submission or full-chain certification'}),flush=True)


if __name__=='__main__':main()
