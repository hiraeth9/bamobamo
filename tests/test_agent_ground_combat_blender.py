"""Real Beta punch → stagger/fall → floor hold → get-up → guard.

This is an isolated integration example, not a production scene recipe.
Known failing continuity reproducer: the procedural recovery guard can change
the elbow plane abruptly, and the retargeted root has an isolated motion peak.
It has not passed acceptance; preserve the failures while improving the
library-first workflow separately.
./blender -b -t 2 --python-exit-code 1 --python tests/test_agent_ground_combat_blender.py -- --render
All outputs are written to a new /tmp directory unless --output is supplied.
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
FPS, LAST = 12, 140
IMPACT, GROUND_FIRST, GROUND_LAST, SETTLED = 15, 16, 111, 119
SOURCE_WINDOW = [7.8, 20.4]


def body_state(actor):
    import bpy
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    heads = {bone.name.split(':')[-1]: arm.matrix_world @ bone.head for bone in arm.pose.bones}
    return {'points': heads, 'root': actor['root'].matrix_world.translation.copy(),
            'hip_height': heads['Hips'].z, 'head_height': heads['Head'].z,
            'torso_up': (heads['Head']-heads['Hips']).normalized().z}


def reload_check(output):
    import bpy
    import duel_plan
    from agent_ground_motion import check_ground_motion
    from agent_interactions import verify_interaction
    duel_plan.JOBS = output/'jobs'
    bpy.ops.wm.open_mainfile(filepath=str(output/'scene.blend'))
    interaction = verify_interaction('p4_ground_combat')
    ground = check_ground_motion('p4_ground_combat', 'defender')
    assert interaction['passed'] and interaction['hit_confirmed'], interaction
    assert ground['passed'], ground
    print('GROUND_COMBAT_RELOAD_PASS', flush=True)


def main():
    import bpy
    import duel_plan
    from mathutils import Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_motion_timeline import motion_commit
    from agent_interactions import guard_events, hand_strike_events, register_interaction, verify_interaction
    from agent_ground_motion import ground_motion_events, check_ground_motion
    from test_agent_interactions_blender import camera_and_floor, skin_point
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='bamo_p4_ground_combat_'))
    output.mkdir(parents=True, exist_ok=True)
    if args.reload:
        return reload_check(output)
    assert not (output/'scene.blend').exists(), 'Choose a new output directory'
    print('GROUND_COMBAT_OUTPUT', output, flush=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.world = bpy.data.worlds.new('CombatWorld')
    scene.frame_start, scene.frame_end, scene.render.fps = 1, LAST, FPS
    duel_plan.JOBS = output/'jobs'
    job = 'p4_ground_combat'
    folder = duel_plan.JOBS/job
    folder.mkdir(parents=True)
    (folder/'request.json').write_text('{"prompt":"原Beta白膜人物，进攻者一拳真实击中防守者头部；防守者踉跄失衡倒地，躺地片刻，撑地起身，最后恢复护架。固定全景。","character_policy":"library_beta"}', encoding='utf-8')
    actor_ensure(job, 'attacker', position=(-.33, 0, 0), yaw=math.pi/2, color=(.65,.72,.82,1))
    actor_ensure(job, 'defender', position=(.42, 0, 0), yaw=-math.pi/2, color=(.82,.72,.63,1))
    actors = {aid: resolve_actor(job, aid) for aid in ('attacker', 'defender')}
    initial_guards = {}
    for aid in actors:
        plan = guard_events(job, aid, 'initial_guard', 1, LAST)
        register_interaction(job, plan)
        initial_guards[aid] = plan
    motion_commit(job, ['attacker','defender'], 1, LAST)
    scene.frame_set(1)
    arm = actors['defender']['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    head = arm.matrix_world @ arm.pose.bones['mixamorig:Head'].head
    binding, _ = skin_point(actors['defender'], 'Head', head+Vector((-.065,0,.055)))
    strike = hand_strike_events(job, 'attacker', 'opening_hit', binding,
        start_frame=5, impact_frame=IMPACT, end_frame=26, intensity=.9,
        windup_m=.06, torso_twist_deg=8, hip_twist_deg=4, contact_frames=1)
    assert strike['ok'], strike
    register_interaction(job, strike)
    motion_commit(job, ['attacker','defender'], 1, LAST)
    hit = verify_interaction(job)
    assert hit['passed'] and hit['hit_confirmed'], hit
    print('GROUND_COMBAT_HIT_BEFORE_FALL', hit['impact_contact_ids'], flush=True)
    # Only add the reaction after measuring the actual hit. The final saved
    # scene checks the same hit again, rather than trusting this earlier pass.
    ground = ground_motion_events(job, 'defender', 'hit_fall_recover',
        PROJECT/'rokoko_staging/mixamo/KnockOut_Loser_mixamo.fbx',
        source_window=SOURCE_WINDOW, start_frame=GROUND_FIRST, end_frame=GROUND_LAST,
        supports=[{'chain':'LeftHand','start_frame':92,'end_frame':93}],
        settle_frames=8, expect_standing=True, blend_in_frames=6)
    ground['expectations']['required_impact_contact_ids'] = hit['impact_contact_ids']
    register_interaction(job, ground)
    # The original guard must stop at the hit; leaving its IK active would
    # pin raised hands to the head throughout the fall and the floor support.
    for event in initial_guards['defender']['events']:
        event['end_frame'] = IMPACT
    register_interaction(job, initial_guards['defender'])
    motion_commit(job, ['attacker','defender'], 1, LAST)
    recovery = guard_events(job, 'defender', 'recovered_guard', SETTLED+1, LAST, raise_frames=14)
    register_interaction(job, recovery)
    motion_commit(job, ['attacker','defender'], 1, LAST)
    interaction = verify_interaction(job)
    ground_check = check_ground_motion(job, 'defender')
    print('GROUND_COMBAT_CHECK', {'hit':interaction['hit_confirmed'],
          'interaction_passed':interaction['passed'], 'ground_passed':ground_check['passed'],
          'interaction_failures':interaction['contact_report'].get('failures',[])[:4],
          'ground_failures':ground_check.get('failures',[])[:4]}, flush=True)
    assert interaction['passed'] and interaction['hit_confirmed'], interaction
    assert ground_check['passed'], ground_check
    states = {}
    for frame in range(1, LAST+1):
        scene.frame_set(frame); bpy.context.view_layer.update()
        states[frame] = body_state(actors['defender'])
    down = [frame for frame,state in states.items() if state['hip_height'] < .38 and abs(state['torso_up']) < .4]
    first_down = min(down) if down else None
    maximum_hold = run = 0
    for frame in range(1, LAST+1):
        run = run+1 if frame in down else 0
        maximum_hold = max(maximum_hold, run)
    boundaries = {}
    for a,b in ((IMPACT,GROUND_FIRST),(SETTLED,SETTLED+1)):
        changes = sorted(((name,(states[a]['points'][name]-point).length) for name,point in states[b]['points'].items()),
                         key=lambda item:-item[1])
        bones = changes[0][1]
        root = (states[a]['root']-states[b]['root']).length
        boundaries[str(a)+'-'+str(b)] = {'maximum_joint_step_m':bones,'root_step_m':root,'joints':changes[:4]}
    root_steps = {f:(states[f]['root']-states[f-1]['root']).length for f in range(2,LAST+1)}
    maximum_root_step = max(root_steps.values())
    peak = max(root_steps,key=root_steps.get)
    final = states[LAST]
    print('GROUND_COMBAT_PHASES', {'first_down_frame':first_down,'floor_hold_frames':maximum_hold,
          'boundaries':boundaries,'maximum_root_step_m':maximum_root_step,
          'peak_root_frame':peak,'peak_root_neighbors':{f:root_steps[f] for f in range(max(2,peak-2),min(LAST,peak+2)+1)},
          'final_hip_height_m':final['hip_height'],'final_head_height_m':final['head_height'],
          'final_torso_up':final['torso_up'],'support_windows':ground['support_windows']}, flush=True)
    final_drift = max((state['points'][name]-point).length for state in
                      (states[frame] for frame in range(LAST-4,LAST)) for name,point in final['points'].items())
    camera_and_floor()
    scene.camera.location = (1,-7,2.3)
    # Fit a fixed overview against the actual motion, including both actors.
    corners = []
    for frame in range(1,LAST+1):
        scene.frame_set(frame)
        graph = bpy.context.evaluated_depsgraph_get()
        for actor in actors.values():
            for mesh in actor['meshes']:
                evaluated = mesh.evaluated_get(graph)
                corners.extend(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)
    low = Vector(tuple(min(point[i] for point in corners) for i in range(3)))
    high = Vector(tuple(max(point[i] for point in corners) for i in range(3)))
    center = (low+high)/2
    scene.camera.location = center+Vector((.8,-7,2.2))
    scene.camera.rotation_euler = (center-scene.camera.location).to_track_quat('-Z','Y').to_euler()
    bpy.context.view_layer.update()
    camera_inverse = scene.camera.matrix_world.inverted()
    local = [camera_inverse @ point for point in corners]
    width = max(point.x for point in local)-min(point.x for point in local)
    height = max(point.y for point in local)-min(point.y for point in local)
    offset = Vector(((max(point.x for point in local)+min(point.x for point in local))/2,
                     (max(point.y for point in local)+min(point.y for point in local))/2,0))
    scene.camera.location += scene.camera.matrix_world.to_quaternion() @ offset
    scene.camera.data.ortho_scale = max(width,height*960/540)*1.15
    bpy.context.view_layer.update()
    from bpy_extras.object_utils import world_to_camera_view
    projected = [world_to_camera_view(scene,scene.camera,point) for point in corners]
    assert all(.02 < point.x < .98 and .02 < point.y < .98 and point.z > 0 for point in projected)
    keyframes = output/'keyframes'
    keyframes.mkdir()
    previews = sorted({1,IMPACT,GROUND_FIRST,GROUND_FIRST+6,first_down,first_down+maximum_hold//2,92,GROUND_LAST,SETTLED,SETTLED+1,LAST})
    for frame in previews:
        scene.frame_set(frame)
        scene.render.filepath = str(keyframes/('frame_%04d.png'%frame))
        bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'scene.blend'), check_existing=False)
    assert first_down is not None and first_down > IMPACT
    assert maximum_hold >= 6, 'Require a visible half-second floor hold'
    assert all(item['maximum_joint_step_m'] < .04 and item['root_step_m'] < .02 for item in boundaries.values()), boundaries
    assert maximum_root_step < .2, 'Root teleport: '+str(maximum_root_step)
    assert final['head_height'] > 1.2 and final['hip_height'] > .7 and final['torso_up'] > .8
    for side in ('Left','Right'):
        wrist = final['points'][side+'Hand']
        assert wrist.z > final['head_height']-.30 and (wrist-final['points']['Head']).length < .5
    assert final_drift < .001, 'Recovered guard must settle: '+str(final_drift)
    child = subprocess.run([bpy.app.binary_path,'-b','-t','2','--python-exit-code','1','--python',__file__,
                            '--','--reload','--output',str(output)],capture_output=True,text=True)
    print(child.stdout[-1200:],flush=True)
    assert child.returncode == 0, child.stderr[-1800:]
    if args.render:
        frames = output/'frames'
        frames.mkdir()
        scene.render.filepath = str(frames/'frame_')
        bpy.ops.render.render(animation=True)
        subprocess.run([str(PROJECT/'ffmpeg'),'-hide_banner','-loglevel','error','-framerate',str(FPS),
                        '-i',str(frames/'frame_%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',
                        str(output/'combat.mp4')],check=True)
    print('GROUND_COMBAT_PASS',output,flush=True)


if __name__ == '__main__':
    main()
