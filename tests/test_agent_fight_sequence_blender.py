"""Fifteen-second procedural Beta fight, with measured contacts and full render.

./blender -b -t 2 --python-exit-code 1 --python tests/test_agent_fight_sequence_blender.py
The test creates a new temporary output folder and never opens a user job.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / 'scripts'), str(PROJECT / 'tests')]
from test_agent_interactions_blender import camera_and_floor, register_plan, skin_point, vertex_world


def root_path(origin, samples):
    return [{'frame': frame, 'root': [origin.x + shift, origin.y, origin.z]}
            for frame, shift in samples]


def foot_steps(actor, rows):
    import bpy
    bpy.context.scene.frame_set(1)
    feet = {foot: skin_point(actor, foot, lowest=True)[1]
            for foot in ('LeftFoot', 'RightFoot')}
    return [{'foot': foot, 'lift_frame': lift, 'land_frame': land,
             'position': [feet[foot].x + shift, feet[foot].y, 0]}
            for foot, lift, land, shift in rows]


def face_binding(actor, frame, toward):
    import bpy
    from mathutils import Vector
    bpy.context.scene.frame_set(frame)
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    head = arm.matrix_world @ arm.pose.bones['mixamorig:Head'].head
    return skin_point(actor, 'Head', head + Vector((toward * .07, 0, .06)))[0]


def impact_facts(actors, attack, target):
    import bpy
    scene = bpy.context.scene
    frame = attack['expectations']['impact_frame']
    scene.frame_set(frame)
    aid = attack['actor_id']
    # The actual target event carries the rig chain; masks only locate layers.
    chain = next(iter(attack['events'][2]['targets']))
    side = chain.removesuffix('Hand')
    arm = actors[aid]['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    shoulder, elbow, wrist = [arm.matrix_world @ arm.pose.bones['mixamorig:' + side + part].head
                              for part in ('Arm', 'ForeArm', 'Hand')]
    effector = attack['expectations']['effector']['effector']
    target_position = vertex_world(target['object'], target['vertex_index'])
    return {'actor_id': aid, 'frame': frame, 'outcome': attack['expectations']['outcome'],
            'arm_extension': (wrist - shoulder).length / ((elbow - shoulder).length + (wrist - elbow).length),
            'elbow_degrees': math.degrees((shoulder - elbow).angle(wrist - elbow)),
            'surface_distance_m': (vertex_world(effector['object'], effector['vertex_index']) - target_position).length}


def frame_visibility(actors):
    import bpy
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector
    scene = bpy.context.scene
    minimum, maximum = [1., 1.], [0., 0.]
    for frame in range(1, 451):
        scene.frame_set(frame)
        graph = bpy.context.evaluated_depsgraph_get()
        for actor in actors.values():
            for obj in actor['meshes']:
                mesh = obj.evaluated_get(graph)
                for corner in mesh.bound_box:
                    p = world_to_camera_view(scene, scene.camera, mesh.matrix_world @ Vector(corner))
                    assert p.z > 0 and .015 < p.x < .985 and .015 < p.y < .985, (frame, obj.name, list(p))
                    minimum = [min(minimum[0], p.x), min(minimum[1], p.y)]
                    maximum = [max(maximum[0], p.x), max(maximum[1], p.y)]
    return {'checked_frames': 450, 'minimum_xy': minimum, 'maximum_xy': maximum}


def recovery_facts(actors):
    import bpy
    scene = bpy.context.scene
    rows = []
    for aid, first, last in (('B', 150, 181), ('A', 252, 284)):
        scene.frame_set(first)
        before = actors[aid]['root'].matrix_world.translation.copy()
        scene.frame_set(last)
        after = actors[aid]['root'].matrix_world.translation.copy()
        scene.frame_set(430)
        feet = [skin_point(actors[aid], foot, lowest=True)
                for foot in ('LeftFoot', 'RightFoot')]
        scene.frame_set(450)
        slide = max((vertex_world(binding['object'], binding['vertex_index']) - position).length
                    for binding, position in feet)
        rows.append({'actor_id': aid, 'retreat_x_m': after.x-before.x,
                     'final_20_frames_foot_slide_m': slide})
        assert (after-before).length > .15 and slide < .005, rows[-1]
    return rows


def main():
    import bpy
    from mathutils import Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_motion_timeline import motion_commit
    from agent_interactions import (guard_events, hand_strike_events, recoil_events,
                                    support_step_events, verify_interaction)
    import duel_plan
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    output = Path(args.output).resolve() if args.output else Path(tempfile.mkdtemp(prefix='bamo_fight_15s_'))
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'scene.blend').exists(), 'Choose a new output directory'
    print('FIGHT15_OUTPUT', output, flush=True)
    started = time.monotonic()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end, scene.render.fps = 1, 450, 30
    scene.world = bpy.data.worlds.new('FightWorld')
    duel_plan.JOBS = output / 'jobs'
    job = 'procedural_fight_15s'
    folder = duel_plan.JOBS / job
    folder.mkdir(parents=True)
    (folder / 'request.json').write_text(json.dumps({
        'prompt': '两名原Beta白膜人物，15秒固定全身：护架、格挡、落空，A命中B后退，B反击命中A后退，恢复站稳。',
        'character_policy': 'library_beta'}, ensure_ascii=False), encoding='utf-8')
    actor_ensure(job, 'A', position=(-.43, 0, 0), yaw=math.pi / 2, color=(.64, .70, .79, 1))
    actor_ensure(job, 'B', position=(.43, 0, 0), yaw=-math.pi / 2, color=(.79, .73, .66, 1))
    actors = {aid: resolve_actor(job, aid) for aid in ('A', 'B')}
    guards = []
    for aid in actors:
        guard = guard_events(job, aid, 'guard_' + aid, 1, 1)
        register_plan(job, guard)
        motion_commit(job, aid, 1, 1)
        for event in guard['events']:
            event['end_frame'] = 450
        register_plan(job, guard)
        guards.append(guard)
    scene.frame_set(1)
    origins = {aid: actor['root'].location.copy() for aid, actor in actors.items()}
    a_steps = foot_steps(actors['A'], [
        ('LeftFoot', 8, 24, .12), ('RightFoot', 25, 36, .06),
        ('LeftFoot', 182, 196, .29), ('RightFoot', 197, 212, .27),
        ('LeftFoot', 254, 268, .05), ('RightFoot', 269, 284, .07),
        ('LeftFoot', 326, 340, .21), ('RightFoot', 341, 355, .17)])
    b_steps = foot_steps(actors['B'], [
        ('RightFoot', 152, 165, .22), ('LeftFoot', 166, 181, .18),
        ('RightFoot', 326, 339, .11), ('LeftFoot', 340, 354, .09)])
    supports = [
        support_step_events(job, 'A', 'all_A_steps', a_steps, root_path(origins['A'], [
            (1, 0), (8, 0), (36, .10), (181, .10), (212, .28),
            (252, .28), (284, .06), (325, .06), (355, .19), (450, .19)])),
        support_step_events(job, 'B', 'all_B_steps', b_steps, root_path(origins['B'], [
            (1, 0), (150, 0), (181, .18), (325, .18), (354, .09), (450, .09)]))]
    for plan in supports:
        register_plan(job, plan)
    print('FIGHT15_STAGE', 'base guard and all supported root trajectories', flush=True)
    motion_commit(job, ['A', 'B'], 1, 450)
    # Compile from the actual established stance at each attack, not a paired clip.
    target_b = face_binding(actors['B'], 150, -1)
    target_a = face_binding(actors['A'], 252, 1)
    scene.frame_set(62)
    arm = actors['B']['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    wrist = arm.matrix_world @ arm.pose.bones['mixamorig:RightHand'].head
    blocking_hand = skin_point(actors['B'], 'RightHand', wrist)[0]
    attack_specs = [
        ('A', 'A_jab_blocked', blocking_hand, 46, 62, 80, 'LeftHand', 'block', None),
        ('B', 'B_swing_misses', target_a, 94, 108, 127, 'RightHand', 'miss', (0, .18, -.08)),
        ('A', 'A_straight_hit', target_b, 135, 150, 174, 'RightHand', 'hit', None),
        ('B', 'B_counter_hit', target_a, 236, 252, 277, 'RightHand', 'hit', None),
        ('A', 'A_final_feint_miss', target_b, 379, 395, 418, 'LeftHand', 'miss', (0, .18, -.08))]
    attacks, responses, targets = [], [], []
    for aid, event_id, target, first, impact, last, hand, outcome, offset in attack_specs:
        print('FIGHT15_COMPILE', event_id, 'impact', impact, flush=True)
        plan = hand_strike_events(job, aid, event_id, target, start_frame=first,
            impact_frame=impact, end_frame=last, hand=hand, intensity=.85,
            torso_twist_deg=8, hip_twist_deg=3, contact_frames=1,
            outcome=outcome, aim_offset_world=offset, intent=event_id.replace('_', ' '))
        assert plan['ok'], plan
        register_plan(job, plan)
        attacks.append(plan); targets.append(target)
        if outcome == 'hit':
            defender = 'B' if aid == 'A' else 'A'
            response = recoil_events(job, defender, 'response_to_' + event_id,
                impact_frame=impact, end_frame=last+10, direction_world=(1 if aid == 'A' else -1, 0, 0),
                amount=.8, contact_id=plan['expectations']['impact_contact_ids'][0])
            register_plan(job, response)
            responses.append(response)
    print('FIGHT15_STAGE', 'same-frame attack, counterattack and recovery solve', flush=True)
    committed = motion_commit(job, ['A', 'B'], 1, 450)
    verification = verify_interaction(job, [*supports, *attacks, *responses])
    facts = [impact_facts(actors, attack, target) for attack, target in zip(attacks, targets)]
    print('FIGHT15_ACTIONS', json.dumps(facts), flush=True)
    for row in facts:
        if row['outcome'] == 'hit':
            assert .85 <= row['arm_extension'] <= .985, ('Impact arm extension', row)
    print('FIGHT15_RECOVERY', json.dumps(recovery_facts(actors)), flush=True)
    print('FIGHT15_CONTACTS', json.dumps({
        'passed': verification['passed'], 'hit_confirmed': verification['hit_confirmed'],
        'block_confirmed': verification['block_confirmed'], 'miss_confirmed': verification['miss_confirmed'],
        'failures': verification['failures'],
        'contacts': [{key: row.get(key) for key in ('id', 'kind', 'frame_start', 'frame_end', 'max_distance_m', 'passed')}
                     for row in verification['contact_report'].get('contacts', [])],
        'geometry_failures': verification['contact_report'].get('failures', [])[:8],
        'dependency_windows': len(committed['dependency_windows'])}), flush=True)
    assert verification['passed'], verification['contact_report'].get('failures', [])[:8]
    assert verification['hit_confirmed'] and verification['block_confirmed'] and verification['miss_confirmed']
    camera_and_floor()
    scene.camera.location = (.1, -5, 1.9)
    scene.camera.rotation_euler = (Vector((.2, 0, .95))-scene.camera.location).to_track_quat('-Z', 'Y').to_euler()
    print('FIGHT15_CAMERA', json.dumps(frame_visibility(actors)), flush=True)
    keyframes = output / 'keyframes'
    keyframes.mkdir()
    for frame in (1, 62, 108, 150, 153, 171, 212, 252, 255, 275, 355, 395, 450):
        scene.frame_set(frame)
        scene.render.filepath = str(keyframes / ('frame_%03d.png' % frame))
        bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / 'scene.blend'), check_existing=False)
    print('FIGHT15_STAGE', 'render 450 frames', 'elapsed_s', round(time.monotonic()-started, 2), flush=True)
    frames = output / 'frames'
    frames.mkdir()
    scene.render.filepath = str(frames / 'frame_')
    bpy.ops.render.render(animation=True)
    video = output / 'fight_15s.mp4'
    subprocess.run([str(PROJECT / 'ffmpeg'), '-hide_banner', '-loglevel', 'error', '-framerate', '30',
        '-i', str(frames / 'frame_%04d.png'), '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', str(video)], check=True)
    duration = float(subprocess.check_output([str(PROJECT / 'ffprobe'), '-v', 'error',
        '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(video)], text=True))
    assert abs(duration-15) < .04, duration
    print('FIGHT15_PASS', json.dumps({'output': str(output), 'video': str(video), 'duration_s': duration,
        'elapsed_s': round(time.monotonic()-started, 2), 'paired_clip_used': False,
        'naturalness': 'visual review required'}), flush=True)


if __name__ == '__main__':
    main()
