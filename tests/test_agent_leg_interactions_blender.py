"""Real Beta front kick with a planted support foot and grounded recovery.

./blender -b -t 2 --python-exit-code 1 --python tests/test_agent_leg_interactions_blender.py
Optional: -- --foot LeftFoot --angle 35 --render --output /tmp/new-directory
"""
import argparse
import math
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'scripts'))
sys.path.insert(0, str(PROJECT/'tests'))


def main():
    import bpy
    from mathutils import Vector
    import duel_plan
    from agent_character import actor_ensure, resolve_actor
    from agent_interactions import guard_events, register_interaction, verify_interaction
    from agent_leg_interactions import leg_strike_events
    from agent_motion_timeline import motion_commit
    from test_agent_interactions_blender import camera_and_floor, skin_point, vertex_world
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--foot', choices=('RightFoot', 'LeftFoot'), default='RightFoot')
    parser.add_argument('--angle', type=float, default=0)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output = Path(args.output) if args.output else Path(tempfile.mkdtemp(prefix='bamo_p4_kick_'))
    output.mkdir(parents=True, exist_ok=True)
    duel_plan.JOBS = output/'jobs'
    if args.reload:
        bpy.ops.wm.open_mainfile(filepath=str(output/'scene.blend'))
        result = verify_interaction('p4_kick')
        print('P4_KICK_RELOAD', result['passed'], result['hit_confirmed'], flush=True)
        assert result['passed'] and result['hit_confirmed'], result
        return
    assert not (output/'scene.blend').exists(), 'Choose a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.world = bpy.data.worlds.new('KickWorld')
    scene.frame_start, scene.frame_end, scene.render.fps = 1, 90, 30
    job = 'p4_kick'
    folder = duel_plan.JOBS/job
    folder.mkdir(parents=True)
    (folder/'request.json').write_text('{"prompt":"原Beta双人平地前踢，抬膝、伸击腹部、收腿落地，完整人物入画","character_policy":"library_beta"}', encoding='utf8')
    angle = math.radians(args.angle)
    forward = Vector((math.cos(angle), math.sin(angle), 0))
    # Beta's ready stance has a leading left leg and a trailing right leg.
    separation = 1.04 if args.foot == 'LeftFoot' else .96
    actor_ensure(job, 'Kicker', position=list(-forward*separation/2), yaw=math.pi/2+angle,
                 color=(.64, .70, .79, 1))
    actor_ensure(job, 'Partner', position=list(forward*separation/2), yaw=-math.pi/2+angle,
                 color=(.79, .73, .66, 1))
    actors = {aid: resolve_actor(job, aid) for aid in ('Kicker', 'Partner')}
    for aid in actors:
        guard = guard_events(job, aid, 'guard', 1, 90)
        register_interaction(job, guard)
    motion_commit(job, ['Kicker', 'Partner'], 1, 90)
    scene.frame_set(1)
    partner = actors['Partner']
    arm = partner['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    torso = arm.matrix_world @ arm.pose.bones['mixamorig:Spine'].head
    target, target_position = skin_point(partner, 'Spine', torso-forward*.08+Vector((0, 0, .03)))
    print('P4_KICK_TARGET', list(target_position), flush=True)
    root_before = actors['Kicker']['root'].matrix_world.copy()
    too_far = leg_strike_events(job, 'Kicker', 'out_of_reach',
        {'object':partner['root'].name,'local_point':[3,0,1.1]}, start_frame=8,
        impact_frame=30, end_frame=68, foot=args.foot, direction_world=list(forward))
    assert not too_far['ok'] and too_far['status'] == 'approach_required', too_far
    assert scene.frame_current == 1 and actors['Kicker']['root'].matrix_world == root_before
    plan = leg_strike_events(job, 'Kicker', 'front_kick', target, start_frame=8,
                            impact_frame=30, end_frame=68, foot=args.foot,
                            direction_world=list(forward), chamber_height_m=.43)
    print('P4_KICK_COMPILE', {key: value for key,value in plan.items() if key not in ('events','expectations')}, flush=True)
    assert plan['ok'], plan
    register_interaction(job, plan)
    motion_commit(job, ['Kicker', 'Partner'], 1, 90)
    verification = verify_interaction(job)
    print('P4_KICK_CONTACT', verification, flush=True)
    assert verification['passed'] and verification['hit_confirmed'], verification
    timing = plan['expectations']['timing']
    contact = plan['expectations']['effector']['effector']
    support = plan['expectations']['support_foot']
    leg_metrics = {}
    end_samples = []
    for frame in range(1, 91):
        scene.frame_set(frame)
        graph = bpy.context.evaluated_depsgraph_get()
        arm = actors['Kicker']['armature'].evaluated_get(graph)
        if frame in (timing['chamber'], timing['impact'], timing['recover']):
            side = args.foot.removesuffix('Foot')
            hip, knee, ankle = [arm.matrix_world @ arm.pose.bones['mixamorig:'+side+part].head
                                for part in ('UpLeg', 'Leg', 'Foot')]
            extension = (ankle-hip).length/((knee-hip).length+(ankle-knee).length)
            leg_metrics[frame] = {'hip':list(hip),'knee':list(knee),'ankle':list(ankle),'extension':extension,
                                  'toe':list(vertex_world(contact['object'],contact['vertex_index']))}
        if frame >= timing['end']:
            end_samples.append([tuple(arm.matrix_world @ bone.head) for bone in arm.pose.bones])
    assert .82 < leg_metrics[timing['impact']]['extension'] < .999, leg_metrics
    chamber_knee = leg_metrics[timing['chamber']]['knee']
    assert chamber_knee[2] > .62, leg_metrics
    final_drift = max(math.dist(a,b) for sample in end_samples[1:] for a,b in zip(end_samples[0],sample))
    print('P4_KICK_METRICS', {'legs':leg_metrics,'final_drift_m':final_drift}, flush=True)
    camera_and_floor()
    # A broadside view makes the lift, knee extension, and stance leg visible.
    lateral = Vector((-forward.y, forward.x, 0))
    scene.camera.location = lateral*-5+Vector((0,0,1.8))
    scene.camera.rotation_euler = (Vector((0,0,.95))-scene.camera.location).to_track_quat('-Z','Y').to_euler()
    scene.camera.data.ortho_scale = 4.2
    from bpy_extras.object_utils import world_to_camera_view
    for frame in range(1, 91):
        scene.frame_set(frame)
        graph = bpy.context.evaluated_depsgraph_get()
        for actor in actors.values():
            for mesh in actor['meshes']:
                evaluated = mesh.evaluated_get(graph)
                for corner in evaluated.bound_box:
                    point = world_to_camera_view(scene, scene.camera, evaluated.matrix_world @ Vector(corner))
                    assert .015 < point.x < .985 and .015 < point.y < .985 and point.z > 0, (frame, mesh.name, tuple(point))
    frames = output/'keyframes'
    frames.mkdir()
    for frame in (1, timing['chamber'], timing['impact'], timing['recover'], timing['land'], 90):
        scene.frame_set(frame)
        scene.render.filepath = str(frames/('frame_%03d.png'%frame))
        bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'scene.blend'), check_existing=False)
    assert final_drift < .001, 'Final guard/stance drift: '+str(final_drift)
    if args.render:
        frames = output/'frames'
        frames.mkdir()
        scene.render.filepath = str(frames/'frame_')
        bpy.ops.render.render(animation=True)
        subprocess.run([str(PROJECT/'ffmpeg'),'-hide_banner','-loglevel','error','-framerate','30',
                        '-i',str(frames/'frame_%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',
                        str(output/'kick.mp4')], check=True)
    print('P4_KICK_PASS', output, flush=True)


if __name__ == '__main__':
    main()
