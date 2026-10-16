"""Real library transition: dynamic outgoing root, heading/scale and local edits.

No render or model call. Outputs only a new /tmp scene and reload script.
./blender -b -t 2 --python-exit-code 1 --python tests/test_agent_motion_transition_blender.py
"""
import math
from pathlib import Path
import sys
import tempfile

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))


def snapshot(actor):
    import bpy
    scene=bpy.context.scene
    rows={}
    for frame in range(1,66):
        scene.frame_set(frame); bpy.context.view_layer.update()
        arm=actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        rows[frame]={'root':list(value for row in actor['root'].matrix_basis for value in row),
                     'bones':{bone.name:list(value for row in arm.matrix_world @ bone.matrix for value in row)
                              for bone in arm.pose.bones}}
    return rows


def maximum_error(before,after,frames,names=None):
    maximum=0.
    for frame in frames:
        maximum=max(maximum,max(abs(a-b) for a,b in zip(before[frame]['root'],after[frame]['root'])))
        for name,values in before[frame]['bones'].items():
            if names is None or name in names:
                maximum=max(maximum,max(abs(a-b) for a,b in zip(values,after[frame]['bones'][name])))
    return maximum


def main():
    import bpy
    from mathutils import Vector,Quaternion
    import duel_plan
    from agent_character import actor_ensure,resolve_actor
    from agent_motion import prepare_motion,sample_motion
    from agent_motion_timeline import register_event,motion_commit
    from agent_motion_quality import rotation_delta_degrees
    output=Path(tempfile.mkdtemp(prefix='bamo_library_root_transition_'))
    print('LIBRARY_ROOT_OUTPUT',output,flush=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene
    scene.frame_start,scene.frame_end,scene.render.fps=1,65,30
    duel_plan.JOBS=output/'jobs'
    job='root_transition'
    folder=duel_plan.JOBS/job
    folder.mkdir(parents=True)
    (folder/'request.json').write_text('{"prompt":"原人物原速走路过渡跑步，根朝向与尺度平滑变化；局部编辑保留其他动画","character_policy":"library_beta"}',encoding='utf8')
    actor_ensure(job,'Walker',position=(.2,-.1,0),yaw=.23)
    actor=resolve_actor(job,'Walker')
    old_rotation=actor['root'].matrix_basis.to_quaternion()
    new_rotation=Quaternion((0,0,1),.23+math.pi/2)
    start=actor['root'].location.copy()
    new_scale=Vector((1.1,.9,1))
    old_motion,new_motion='mixamo.basic.walk','mixamo.basic.run'
    duration={name:prepare_motion(actor['armature'],name)['duration_s'] for name in (old_motion,new_motion)}
    def local_root(name,elapsed):
        cycles=math.floor(elapsed/duration[name])
        local=Vector(sample_motion(actor['armature'],name,elapsed-cycles*duration[name])['root'])
        return local+Vector(sample_motion(actor['armature'],name,duration[name])['root'])*cycles
    def outgoing(frame):
        return start+old_rotation @ local_root(old_motion,(frame-1)/30)
    incoming_origin=outgoing(25)
    def incoming(frame):
        travel=local_root(new_motion,(frame-25)/30)
        return incoming_origin+new_rotation @ Vector(tuple(a*b for a,b in zip(travel,new_scale)))
    register_event(job,'Walker','walk',{'source':'library','motion_id':old_motion,'start_frame':1,'end_frame':40,
                   'root_strategy':'source','loop':True,'time_scale':1})
    register_event(job,'Walker','run',{'source':'library','motion_id':new_motion,'start_frame':25,'end_frame':65,
                   'root_strategy':'source','loop':True,'time_scale':1,'blend_in_frames':12,
                   'keyframes':[{'frame':25,'root_rotation_quaternion':list(new_rotation),'root_scale':list(new_scale)}]})
    motion_commit(job,'Walker',1,65)
    expected_positions={}
    errors=[]
    for frame in range(1,66):
        scene.frame_set(frame)
        if frame<25:
            weight=0.; position=outgoing(frame); rotation=old_rotation; scale=Vector((1,1,1))
        else:
            t=min(1,(frame-25)/12)
            weight=t*t*(3-2*t)
            position=outgoing(min(frame,40)).lerp(incoming(frame),weight)
            rotation=old_rotation.slerp(new_rotation,weight)
            scale=Vector((1,1,1)).lerp(new_scale,weight)
        expected_positions[frame]=position
        errors.append((actor['root'].location-position).length)
        assert errors[-1]<2e-5,(frame,errors[-1],list(actor['root'].location),list(position))
        assert rotation_delta_degrees(actor['root'].rotation_quaternion,rotation)<.02,frame
        assert (actor['root'].scale-scale).length<2e-6,frame
    # At the midpoint, freezing the outgoing start would lose real walking travel.
    frame=31; scene.frame_set(frame)
    frozen=incoming_origin.lerp(incoming(frame),.5)
    assert (actor['root'].location-frozen).length>.025
    before_velocity=expected_positions[25]-expected_positions[24]
    after_velocity=expected_positions[26]-expected_positions[25]
    assert (after_velocity-before_velocity).length<.012
    print('LIBRARY_ROOT_BLEND',{'maximum_position_error_m':max(errors),
          'transition_entry_velocity_change_m_per_frame':(after_velocity-before_velocity).length,
          'dynamic_outgoing_vs_frozen_difference_m':(actor['root'].location-frozen).length},flush=True)
    before=snapshot(actor)
    scene.frame_set(45)
    head=actor['armature'].pose.bones['mixamorig:Head']
    rotation=list(head.rotation_quaternion @ Quaternion((1,0,0),.18))
    register_event(job,'Walker','head_only',{'source':'procedural','start_frame':45,'end_frame':50,
        'mask':['Head'],'priority':3,'root_strategy':'trajectory',
        'keyframes':[{'frame':45,'root':[9,8,7],'root_rotation_quaternion':[0,0,0,1],
                      'root_scale':[.2,.2,.2],'pose':{head.name:{'rotation_quaternion':rotation}}}]})
    motion_commit(job,'Walker',45,50)
    masked=snapshot(actor)
    legs=[name for name in before[1]['bones'] if any(part in name for part in ('Hips','Leg','Foot','Toe'))]
    assert maximum_error(before,masked,range(1,66),legs)<2e-5
    assert maximum_error(before,masked,list(range(1,45))+list(range(51,66)))<2e-5
    assert maximum_error(before,masked,range(45,51))>.01
    # Direct local edit followed by capture keeps the evaluated transition
    # and masked animation outside this separate four-frame edit window.
    for frame in range(12,16):
        scene.frame_set(frame)
        head.rotation_quaternion=head.rotation_quaternion @ Quaternion((1,0,0),.12)
        head.keyframe_insert('rotation_quaternion',frame=frame)
    edited=snapshot(actor)
    motion_commit(job,'Walker',12,15,capture=True,event_id='captured_local_head')
    captured=snapshot(actor)
    capture_error=maximum_error(edited,captured,range(1,66))
    assert capture_error<2e-5,capture_error
    print('LIBRARY_ROOT_LOCAL_EDITS',{'masked_preserved_leg_and_root_error':maximum_error(before,masked,range(1,66),legs),
          'local_capture_all_bone_and_root_error':capture_error},flush=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'scene.blend'),check_existing=False)
    # Reopen in the same process to verify persisted curves, without new renders.
    bpy.ops.wm.open_mainfile(filepath=str(output/'scene.blend'))
    reopened=snapshot(resolve_actor(job,'Walker'))
    assert maximum_error(captured,reopened,range(1,66))<2e-5
    print('LIBRARY_ROOT_TRANSITION_PASS',output,flush=True)


if __name__=='__main__':
    main()
