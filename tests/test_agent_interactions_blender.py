"""A short real-Beta punch, recoil and two-step recovery scene.

./blender -b -t 2 --python-exit-code 1 --python tests/test_agent_interactions_blender.py -- --render
Writes a new temporary directory by default. Keyframes are always rendered;
--render also creates a four-second MP4. No historical job is opened or edited.
"""
import argparse
import math
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))


def vertex_world(name, index):
    import bpy
    obj = bpy.data.objects[name].evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = obj.to_mesh()
    try:
        return obj.matrix_world @ mesh.vertices[index].co
    finally:
        obj.to_mesh_clear()


def skin_point(actor, group, desired=None, lowest=False):
    """Pick real geometry for the face target and sole landing coordinates."""
    import bpy
    candidates = []
    for obj in actor['meshes']:
        groups = {g.index for g in obj.vertex_groups if g.name.split(':')[-1].startswith(group)}
        if group.endswith('Foot'):
            groups |= {g.index for g in obj.vertex_groups if g.name.split(':')[-1].startswith(group[:-4] + 'Toe')}
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            for vertex in obj.data.vertices:
                if sum(g.weight for g in vertex.groups if g.group in groups) < .3:
                    continue
                point = evaluated.matrix_world @ mesh.vertices[vertex.index].co
                score = point.z if lowest else (point - desired).length_squared
                candidates.append((score, obj.name, vertex.index, point.copy()))
        finally:
            evaluated.to_mesh_clear()
    _, name, index, position = min(candidates, key=lambda item: item[0])
    return {'object': name, 'vertex_index': index}, position


def register_plan(job, plan):
    from agent_motion_timeline import register_event
    assert plan['ok'], plan
    for event in plan['events']:
        register_event(job, plan['actor_id'], event['event_id'], event)


def camera_and_floor():
    import bpy
    from mathutils import Vector
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = 'BOTH'
    scene.display.shading.show_specular_highlight = False
    scene.display.shading.background_type = 'WORLD'
    scene.world.color = (.28, .28, .28)
    scene.render.resolution_x, scene.render.resolution_y = 960, 540
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, -.012))
    plane = bpy.context.object
    plane.name = 'Floor'
    material = bpy.data.materials.new('FloorGray')
    material.diffuse_color = (.34, .34, .34, 1)
    plane.data.materials.append(material)
    data = bpy.data.cameras.new('FightWide')
    camera = bpy.data.objects.new('FightWide', data)
    scene.collection.objects.link(camera)
    camera.location = (1.4, -5, 2.3)
    camera.rotation_euler = (Vector((.2, 0, .95)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
    data.type, data.ortho_scale = 'ORTHO', 4.5
    scene.camera = camera


def step_check(job, output):
    """Eight frames isolate actual knee direction before a full fight render."""
    import bpy
    from mathutils import Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_motion_controls import controls_ensure
    from agent_motion_timeline import motion_commit
    from agent_interactions import support_step_events, verify_interaction
    scene = bpy.context.scene
    scene.frame_end = 8
    actor_ensure(job, 'B', yaw=-math.pi/2)
    actor = resolve_actor(job, 'B')
    controls_ensure(job, 'B')
    _, sole = skin_point(actor, 'RightFoot', lowest=True)
    plan = support_step_events(job, 'B', 'short_step', [
        {'foot': 'RightFoot', 'lift_frame': 2, 'land_frame': 8, 'position': [sole.x+.28, sole.y, 0]}],
        [{'frame': 1, 'root': [0, 0, 0]}, {'frame': 8, 'root': [.16, 0, 0]}])
    register_plan(job, plan)
    motion_commit(job, 'B', 1, 8)
    verification = verify_interaction(job, [plan])
    camera_and_floor()
    images = output / 'keyframes'
    images.mkdir()
    for frame in (1, 4, 8):
        scene.frame_set(frame)
        arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        forward = actor['root'].rotation_quaternion @ Vector((0, -1, 0))
        knees = {}
        for side in ('Left', 'Right'):
            hip, knee, ankle = [arm.matrix_world @ arm.pose.bones['mixamorig:' + side + part].head
                                for part in ('UpLeg', 'Leg', 'Foot')]
            axis = (ankle-hip).normalized()
            bend = knee-hip-axis*(knee-hip).dot(axis)
            projected = forward-axis*forward.dot(axis)
            knees[side] = {'bend_m': bend.length, 'forward_dot': bend.normalized().dot(projected.normalized())}
        print('P2_STEP', frame, knees, flush=True)
        scene.render.filepath = str(images / ('step_%03d.png' % frame))
        bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / 'scene.blend'), check_existing=False)
    assert verification['passed'], verification['contact_report'].get('failures', [])[:3]
    print('P2_STEP_PASS', output, flush=True)


def main():
    import bpy
    from mathutils import Vector
    import duel_plan
    from agent_character import actor_ensure, resolve_actor
    from agent_motion_timeline import motion_commit
    from agent_interactions import guard_events, hand_strike_events, recoil_events, support_step_events, verify_interaction
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--quick', action='store_true', help='48-frame, four-second pose preview')
    parser.add_argument('--guard-only', action='store_true')
    parser.add_argument('--step-only', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    output = Path(args.output).resolve() if args.output else Path(tempfile.mkdtemp(prefix='bamo_p2_fight_'))
    output.mkdir(parents=True, exist_ok=True)
    print('P2_OUTPUT', output, flush=True)
    assert not (output / 'scene.blend').exists(), 'Choose a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    end, fps = (48, 12) if args.quick else (120, 30)
    first, impact, recovered = (6, 13, 20) if args.quick else (16, 32, 48)
    recoil_end = 23 if args.quick else 57
    lift1, land1, lift2, land2 = (14, 20, 21, 27) if args.quick else (33, 46, 47, 62)
    scene.frame_start, scene.frame_end, scene.render.fps = 1, end, fps
    scene.world = bpy.data.worlds.new('FightWorld')
    duel_plan.JOBS = output / 'jobs'
    job = 'p2_punch_recovery'
    folder = duel_plan.JOBS / job
    folder.mkdir(parents=True)
    (folder / 'request.json').write_text('{"prompt":"两名原Beta白膜人物：A重拳命中B头部，B后退两步站稳，4秒固定全景","character_policy":"library_beta"}', encoding='utf-8')
    if args.step_only:
        return step_check(job, output)
    actor_ensure(job, 'A', position=(-.43, 0, 0), yaw=math.pi/2, color=(.64, .70, .79, 1))
    actor_ensure(job, 'B', position=(.43, 0, 0), yaw=-math.pi/2, color=(.79, .73, .66, 1))
    actors = {aid: resolve_actor(job, aid) for aid in ('A', 'B')}
    guards = []
    for aid in actors:
        print('P2_STAGE', 'guard', aid, flush=True)
        guard = guard_events(job, aid, 'guard_' + aid, 1, 1)
        register_plan(job, guard)
        motion_commit(job, aid, 1, 1)
        guards.append(guard)
    camera_and_floor()
    keyframes = output / 'keyframes'
    keyframes.mkdir()
    scene.frame_set(1)
    scene.render.filepath = str(keyframes / 'guard.png')
    bpy.ops.render.render(write_still=True)
    print('P2_GUARD_IMAGE', keyframes / 'guard.png', flush=True)
    if args.guard_only:
        bpy.ops.wm.save_as_mainfile(filepath=str(output / 'scene.blend'), check_existing=False)
        return
    for guard in guards:
        for event in guard['events']:
            event['end_frame'] = end
        register_plan(job, guard)
    print('P2_STAGE', 'A short supported approach', flush=True)
    _, a_lead = skin_point(actors['A'], 'LeftFoot', lowest=True)
    a_origin = actors['A']['root'].location.copy()
    approach_land = 8 if args.quick else 20
    approach = support_step_events(job, 'A', 'short_approach', [
        {'foot': 'LeftFoot', 'lift_frame': 3, 'land_frame': approach_land,
         'position': [a_lead.x+.12, a_lead.y, 0]}],
        [{'frame': 1, 'root': list(a_origin)},
         {'frame': approach_land, 'root': list(a_origin + Vector((.09, 0, 0)))},
         {'frame': end, 'root': list(a_origin + Vector((.09, 0, 0)))}])
    register_plan(job, approach)
    motion_commit(job, 'A', 1, end)
    scene.frame_set(1)
    graph = bpy.context.evaluated_depsgraph_get()
    b_arm = actors['B']['armature'].evaluated_get(graph)
    head = b_arm.matrix_world @ b_arm.pose.bones['mixamorig:Head'].head
    binding, _ = skin_point(actors['B'], 'Head', head + Vector((-.07, 0, .06)))
    strike = hand_strike_events(job, 'A', 'straight_punch', binding, start_frame=first,
        impact_frame=impact, end_frame=recovered, intensity=.9, torso_twist_deg=8, contact_frames=1)
    assert strike['ok'], strike
    contact_id = strike['expectations']['impact_contact_ids'][0]
    recoil = recoil_events(job, 'B', 'head_recoil', impact_frame=impact, end_frame=recoil_end,
                           direction_world=(1, 0, 0), amount=.85, contact_id=contact_id)
    _, right = skin_point(actors['B'], 'RightFoot', lowest=True)
    _, left = skin_point(actors['B'], 'LeftFoot', lowest=True)
    origin = actors['B']['root'].location.copy()
    retreat = support_step_events(job, 'B', 'retreat_two_steps', [
        {'foot': 'RightFoot', 'lift_frame': lift1, 'land_frame': land1, 'position': [right.x+.32, right.y, 0]},
        {'foot': 'LeftFoot', 'lift_frame': lift2, 'land_frame': land2, 'position': [left.x+.42, left.y, 0]}],
        [{'frame': impact, 'root': list(origin)}, {'frame': land1, 'root': list(origin + Vector((.16, 0, 0)))},
         {'frame': land2, 'root': list(origin + Vector((.35, 0, 0)))},
         {'frame': end, 'root': list(origin + Vector((.35, 0, 0)))}])
    for plan in (strike, recoil, retreat):
        register_plan(job, plan)
    print('P2_STAGE', 'coordinated punch and recovery bake', flush=True)
    motion_commit(job, ['A', 'B'], 1, end)
    verification = verify_interaction(job, [approach, strike, recoil, retreat])
    scene.frame_set(impact)
    effector = strike['expectations']['effector']['effector']
    error = (vertex_world(effector['object'], effector['vertex_index']) -
             vertex_world(binding['object'], binding['vertex_index'])).length
    a_arm = actors['A']['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    shoulder, elbow, wrist = [a_arm.matrix_world @ a_arm.pose.bones['mixamorig:Right' + part].head
                              for part in ('Arm', 'ForeArm', 'Hand')]
    extension = (wrist-shoulder).length / ((elbow-shoulder).length + (wrist-elbow).length)
    elbow_angle = math.degrees((shoulder-elbow).angle(wrist-elbow))
    scene.frame_set(impact+3)
    b_arm = actors['B']['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    forward = actors['B']['root'].rotation_quaternion @ Vector((0, -1, 0))
    knees = {}
    for side in ('Left', 'Right'):
        hip, knee, ankle = [b_arm.matrix_world @ b_arm.pose.bones['mixamorig:' + side + part].head
                            for part in ('UpLeg', 'Leg', 'Foot')]
        axis = (ankle-hip).normalized()
        bend = knee-hip-axis*(knee-hip).dot(axis)
        projected = forward-axis*forward.dot(axis)
        knees[side] = {'bend_m': bend.length, 'forward_dot': bend.normalized().dot(projected.normalized())}
    scene.frame_set(impact-1)
    before = actors['B']['root'].matrix_world.translation.copy()
    scene.frame_set(end)
    after = actors['B']['root'].matrix_world.translation.copy()
    retreat_distance = (after - before).length
    bpy.ops.wm.save_as_mainfile(filepath=str(output / 'scene.blend'), check_existing=False)
    for frame in (1, (first+impact)//2, impact, impact+3, recovered, land2, end):
        scene.frame_set(frame)
        scene.render.filepath = str(keyframes / ('frame_%03d.png' % frame))
        bpy.ops.render.render(write_still=True)
    print('P2_KEYFRAMES', keyframes, flush=True)
    print('P2_CONTACT', {'impact_error_m': error, 'retreat_m': retreat_distance,
          'arm_extension': extension, 'elbow_degrees': elbow_angle, 'knees': knees,
          'hit_confirmed': verification['hit_confirmed'], 'failures': verification['failures'],
          'contact_failures': verification['contact_report'].get('failures', [])[:5]}, flush=True)
    assert verification['passed'] and verification['hit_confirmed'], verification['contact_report'].get('failures', [])[:5]
    assert error < .02 and retreat_distance > .3
    assert .85 <= extension <= .985, 'Impact arm needs a meaningful extension: ' + str(extension)
    if args.render:
        frames = output / 'frames'
        frames.mkdir()
        scene.render.filepath = str(frames / 'frame_')
        bpy.ops.render.render(animation=True)
        video = output / 'fight.mp4'
        subprocess.run([str(PROJECT / 'ffmpeg'), '-hide_banner', '-loglevel', 'error', '-framerate', str(fps),
            '-i', str(frames / 'frame_%04d.png'), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(video)], check=True)
        print('P2_VIDEO', video, flush=True)
    print('P2_PASS', output, flush=True)


if __name__ == '__main__':
    main()
