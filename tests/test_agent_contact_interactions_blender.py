"""Three real Beta actors, two independently bound grips, one animated prop.

Run with Blender --python this_file -- --output /new/directory --render.
Writes only its new scene/output. No Hermes/model call or historical job edit.
"""
import argparse
from array import array
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
LAST = 72
CONTACT_FIRST, CONTACT_LAST, RELEASED = 17, 48, 64


def vertex_world(name, index):
    import bpy
    obj = bpy.data.objects[name].evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = obj.to_mesh()
    try:
        return obj.matrix_world @ mesh.vertices[index].co
    finally:
        obj.to_mesh_clear()


def geometry(actor):
    import bpy
    graph = bpy.context.evaluated_depsgraph_get()
    arm = actor['armature'].evaluated_get(graph)
    values = [v for bone in arm.pose.bones for row in bone.matrix for v in row]
    for obj in actor['meshes']:
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        try:
            values.extend(v for vertex in mesh.vertices for v in evaluated.matrix_world @ vertex.co)
        finally:
            evaluated.to_mesh_clear()
    return array('f', values)


def arm_pose(actor, side):
    import bpy
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    return [v for bone in arm.pose.bones if bone.name.split(':')[-1].startswith(
        (side+'Shoulder', side+'Arm', side+'ForeArm', side+'Hand')) for row in arm.matrix_world @ bone.matrix for v in row]


def arm_points(actor, side):
    import bpy
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {part: list(arm.matrix_world @ arm.pose.bones['mixamorig:'+side+part].head)
            for part in ('Arm', 'ForeArm', 'Hand')}


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
    scene.display.shading.background_type = 'WORLD'
    scene.world.color = (.28, .28, .28)
    scene.render.resolution_x, scene.render.resolution_y = 960, 540
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, -.012))
    bpy.context.object.name = 'Floor'
    material = bpy.data.materials.new('FloorGrey')
    material.diffuse_color = (.32, .32, .32, 1)
    bpy.context.object.data.materials.append(material)
    data = bpy.data.cameras.new('P5Overview')
    camera = bpy.data.objects.new('P5Overview', data)
    scene.collection.objects.link(camera)
    camera.location = (2.6, -6, 2.8)
    camera.rotation_euler = (Vector((.40, .35, 1.05))-camera.location).to_track_quat('-Z', 'Y').to_euler()
    data.type, data.ortho_scale = 'ORTHO', 5.5
    scene.camera = camera


def framing_margin(actors):
    import bpy
    from mathutils import Vector
    from bpy_extras.object_utils import world_to_camera_view
    scene = bpy.context.scene
    margin = 1.
    for frame in range(1, LAST+1):
        scene.frame_set(frame); bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        for actor in actors.values():
            for obj in actor['meshes']:
                evaluated = obj.evaluated_get(graph)
                for corner in evaluated.bound_box:
                    uv = world_to_camera_view(scene, scene.camera, evaluated.matrix_world @ Vector(corner))
                    assert uv.z > 0
                    margin = min(margin, uv.x, 1-uv.x, uv.y, 1-uv.y)
    assert margin > .02, margin
    return margin


def measurements(job, actors, plans):
    import bpy
    from mathutils import Vector
    scene = bpy.context.scene
    summaries = []
    for plan in plans:
        target = next(iter(plan['events'][0]['targets'].values()))
        binding, effector = target['binding'], target['effector']
        rows, palm_positions = [], []
        for frame in range(1, LAST+1):
            scene.frame_set(frame); bpy.context.view_layer.update()
            point = vertex_world(effector['object'], effector['vertex_index'])
            prop = bpy.data.objects[binding['object']].evaluated_get(bpy.context.evaluated_depsgraph_get())
            goal = prop.matrix_world @ Vector(binding['local_point'])
            palm_positions.append(point.copy())
            rows.append({'frame': frame, 'distance_m': (point-goal).length})
        contact = rows[CONTACT_FIRST-1:CONTACT_LAST]
        max_gap = max(row['distance_m'] for row in contact)
        release_gap = rows[RELEASED-1]['distance_m']
        assert max_gap <= target['contact']['tolerance_m'], (plan['actor_id'], max_gap)
        assert release_gap > .04, (plan['actor_id'], release_gap)
        jumps = [(b-a).length for a, b in zip(palm_positions, palm_positions[1:])]
        assert max(jumps) < .12, (plan['actor_id'], max(jumps))
        summaries.append({'actor_id': plan['actor_id'], 'max_contact_distance_m': max_gap,
                          'release_distance_m': release_gap, 'max_frame_displacement_m': max(jumps)})
    return summaries


def reload_check(args):
    import bpy
    import duel_plan
    from agent_interactions import verify_interaction
    from agent_character import actor_list, resolve_actor
    bpy.ops.wm.open_mainfile(filepath=str(args.scene))
    duel_plan.JOBS = args.output / 'jobs'
    result = verify_interaction('p5_cooperative_contact')
    assert result['passed'], result['failures']
    assert not result['hit_confirmed']
    actors = {item['actor_id']: resolve_actor('p5_cooperative_contact', item['actor_id'])
              for item in actor_list('p5_cooperative_contact')['actors']}
    margin = framing_margin(actors)
    roots = {}
    for aid, actor in actors.items():
        bpy.context.scene.frame_set(1)
        first = actor['root'].matrix_world.translation.copy()
        bpy.context.scene.frame_set(LAST)
        roots[aid] = list(actor['root'].matrix_world.translation-first)
    by_kind = {}
    for item in result['contact_report']['contacts']:
        kind = item['kind']
        group = by_kind.setdefault(kind, {'windows': 0, 'max_distance_m': 0.})
        group['windows'] += 1
        group['max_distance_m'] = max(group['max_distance_m'], item['max_distance_m'])
    print('P5_RELOAD_PASS', json.dumps({'contact_windows': by_kind, 'root_displacement_m': roots,
          'all_actor_framing_margin': margin, 'hit_confirmed': result['hit_confirmed']}), flush=True)


def main():
    import bpy
    import duel_plan
    from mathutils import Quaternion, Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_motion_controls import read_pose
    from agent_motion_timeline import motion_commit, register_event, sample_keyframes
    from agent_interactions import (guard_events, register_interaction, verify_interaction,
                                    support_step_events, _surface_effector, smooth)
    from agent_contact_interactions import contact_events
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--with-steps', action='store_true', help='Two short support steps while carrying the shared prop')
    parser.add_argument('--scene', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    args.output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='bamo_p5_contact_'))
    if args.scene:
        return reload_check(args)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'scene.blend').exists(), 'Choose a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end, scene.render.fps = 1, LAST, 24
    scene.world = bpy.data.worlds.new('P5World')
    duel_plan.JOBS = output / 'jobs'
    job = 'p5_cooperative_contact'
    folder = duel_plan.JOBS / job
    folder.mkdir(parents=True)
    (folder / 'request.json').write_text(json.dumps({'prompt': '两人跟随同一移动物体不同握点并松手，第三人保持自己的动作',
                           'character_policy': 'library_beta'}, ensure_ascii=False), encoding='utf-8')
    for aid, position, yaw, color in [
        ('porter-west', (-.58, 0, 0), math.pi/2, (.66, .72, .82, 1)),
        ('porter-east', (.58, 0, 0), -math.pi/2, (.82, .72, .62, 1)),
        ('observer-9', (1.35, 1.12, 0), 0, (.72, .80, .70, 1))]:
        actor_ensure(job, aid, position=position, yaw=yaw, color=color)
    actors = {aid: resolve_actor(job, aid) for aid in ('porter-west', 'porter-east', 'observer-9')}
    for aid in actors:
        guard = guard_events(job, aid, 'base_guard', 1, LAST)
        if aid != 'observer-9':
            # The underlying contact arm has a real, changing guard before the
            # grip overlay. Releasing must return to that frame's pose, not a
            # single frozen posture captured at the beginning of the scene.
            chain = 'RightHand' if aid == 'porter-west' else 'LeftHand'
            target = guard['events'][0]['targets'][chain]
            final = copy.deepcopy(target['keyframes'][0]); final['frame'] = LAST
            arm = actors[aid]['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
            bone = arm.pose.bones[final['binding']['bone']]
            local_delta = (arm.matrix_world @ bone.matrix).to_3x3().inverted() @ Vector((0, 0, -.04))
            final['binding']['local_point'] = list(Vector(final['binding']['local_point'])+local_delta)
            target['keyframes'].append(final)
        register_interaction(job, guard)
        motion_commit(job, aid, 1, LAST)
    # A separately animated third participant remains byte-for-byte geometrically
    # identical across the other two participants' commit; no source hash audit.
    scene.frame_set(1)
    baseline = read_pose(actors['observer-9']['armature'], evaluated=True)
    end_pose = copy.deepcopy(baseline)
    head = 'mixamorig:Head'
    end_pose[head]['rotation_quaternion'] = list(Quaternion(baseline[head]['rotation_quaternion']) @ Quaternion((0, 1, 0), .28))
    register_event(job, 'observer-9', 'independent_head_motion', {'event_id': 'independent_head_motion',
        'source': 'procedural', 'start_frame': 1, 'end_frame': LAST, 'mask': ['Head'], 'priority': 3,
        'keyframes': [{'frame': 1, 'pose': {head: baseline[head]}}, {'frame': 36, 'pose': {head: end_pose[head]}},
                      {'frame': LAST, 'pose': {head: baseline[head]}}]})
    motion_commit(job, 'observer-9', 1, LAST)
    transport_paths = {}
    if args.with_steps:
        for aid in ('porter-west', 'porter-east'):
            scene.frame_set(1); bpy.context.view_layer.update()
            actor = actors[aid]
            origin = actor['root'].location.copy()
            feet = {foot: Vector(_surface_effector(actor, foot)[1]) for foot in ('LeftFoot', 'RightFoot')}
            path = [{'frame': 1, 'root': list(origin)}, {'frame': 19, 'root': list(origin)},
                    {'frame': 31, 'root': list(origin+Vector((0, .12, 0)))},
                    {'frame': 45, 'root': list(origin+Vector((0, .24, 0)))},
                    {'frame': LAST, 'root': list(origin+Vector((0, .24, 0)))}]
            steps = support_step_events(job, aid, 'carry_side_steps', [
                {'foot': 'RightFoot', 'lift_frame': 20, 'land_frame': 31,
                 'position': [feet['RightFoot'].x, feet['RightFoot'].y+.24, 0]},
                {'foot': 'LeftFoot', 'lift_frame': 34, 'land_frame': 45,
                 'position': [feet['LeftFoot'].x, feet['LeftFoot'].y+.24, 0]}], path)
            register_interaction(job, steps)
            motion_commit(job, aid, 1, LAST)
            transport_paths[aid] = path
    third_before, base_arm, base_points, feet_before = {}, {}, {}, {}
    for frame in range(1, LAST+1):
        scene.frame_set(frame); bpy.context.view_layer.update()
        third_before[frame] = geometry(actors['observer-9'])
        for aid, side in [('porter-west', 'Right'), ('porter-east', 'Left')]:
            base_arm[aid, frame] = arm_pose(actors[aid], side)
            base_points[aid, frame] = arm_points(actors[aid], side)
            arm = actors[aid]['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
            feet_before[aid, frame] = [v for side2 in ('Left', 'Right') for part in ('UpLeg', 'Leg', 'Foot')
                                     for row in arm.matrix_world @ arm.pose.bones['mixamorig:'+side2+part].matrix for v in row]
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -.13, 1.28))
    prop = bpy.context.object
    prop.name = 'SharedBar'
    # Mesh coordinates carry actual dimensions, so each binding addresses an
    # end-face point on the real object rather than an unattached scene empty.
    for vertex in prop.data.vertices:
        vertex.co.x *= .58
        vertex.co.y *= .08
        vertex.co.z *= .06
    material = bpy.data.materials.new('PropGrey')
    material.diffuse_color = (.48, .50, .54, 1)
    prop.data.materials.append(material)
    for frame in range(1, LAST+1):
        amount = smooth((frame-CONTACT_FIRST)/(CONTACT_LAST-CONTACT_FIRST))
        if frame > CONTACT_LAST:
            amount += .4*smooth((frame-CONTACT_LAST)/(LAST-CONTACT_LAST))
        travel = sample_keyframes(transport_paths['porter-west'], frame)['root'][1] if args.with_steps else 0.
        prop.location = Vector((.025*amount, -.13+.055*amount+travel, 1.28+.10*amount))
        prop.rotation_euler = (.10*amount, -.06*amount, .20*amount)
        prop.keyframe_insert('location', frame=frame)
        prop.keyframe_insert('rotation_euler', frame=frame)
    scene.frame_set(1)
    plans = []
    for aid, chain, point in [('porter-west', 'RightHand', [-.29, -.04, .03]),
                              ('porter-east', 'LeftHand', [.29, -.04, .03])]:
        plan = contact_events(job, aid, 'shared_grip', {'object': prop.name, 'local_point': point},
             start_frame=1, contact_frame=CONTACT_FIRST, release_frame=CONTACT_LAST, end_frame=RELEASED,
             chain=chain, kind='cooperative hold', tolerance_m=.02)
        register_interaction(job, plan)
        plans.append(plan)
    motion_commit(job, ['porter-west', 'porter-east'], 1, LAST)
    result = verify_interaction(job, plans)
    assert result['passed'], result['failures']
    assert not result['hit_confirmed']
    max_third_change = max_feet_change = max_release_pose_change = 0.
    release_joint_changes = {'Arm': 0., 'ForeArm': 0., 'Hand': 0.}
    for frame in range(1, LAST+1):
        scene.frame_set(frame); bpy.context.view_layer.update()
        max_third_change = max(max_third_change, max(abs(a-b) for a, b in zip(
            third_before[frame], geometry(actors['observer-9']))))
        for aid, side in [('porter-west', 'Right'), ('porter-east', 'Left')]:
            arm = actors[aid]['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
            feet = [v for side2 in ('Left', 'Right') for part in ('UpLeg', 'Leg', 'Foot')
                    for row in arm.matrix_world @ arm.pose.bones['mixamorig:'+side2+part].matrix for v in row]
            max_feet_change = max(max_feet_change, max(abs(a-b) for a, b in zip(feet, feet_before[aid, frame])))
            if frame == 1 or frame >= RELEASED:
                max_release_pose_change = max(max_release_pose_change, max(abs(a-b) for a, b in zip(
                    arm_pose(actors[aid], side), base_arm[aid, frame])))
                points = arm_points(actors[aid], side)
                for part, point in points.items():
                    release_joint_changes[part] = max(release_joint_changes[part], math.dist(point, base_points[aid, frame][part]))
    print('P5_RELEASE_DIAGNOSTICS', json.dumps({'world_matrix_max_change': max_release_pose_change,
                                             'joint_displacement_m': release_joint_changes}), flush=True)
    assert max_third_change < 1e-5, max_third_change
    assert max_feet_change < 1e-5, max_feet_change
    assert release_joint_changes['Hand'] < .003, release_joint_changes
    assert max(release_joint_changes.values()) < .015, release_joint_changes
    summary = measurements(job, actors, plans)
    camera_and_floor()
    visible_margin = framing_margin(actors)
    blend = output / 'scene.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    with (output / 'reload.log').open('w') as log:
        run = subprocess.run([str(PROJECT/'blender'), '-b', '-t', '2', '--python-exit-code', '1', '--python',
             str(Path(__file__).resolve()), '--', '--scene', str(blend), '--output', str(output)],
             stdout=log, stderr=subprocess.STDOUT, timeout=120)
    assert run.returncode == 0, 'Saved reload failed; see '+str(output/'reload.log')
    images = output / 'keyframes'
    images.mkdir()
    for frame in (1, 9, 17, 34, 48, 57, 64, 72):
        scene.frame_set(frame)
        scene.render.filepath = str(images / ('frame_%03d.png' % frame))
        bpy.ops.render.render(write_still=True)
    if args.render:
        frames = output / 'frames'
        frames.mkdir()
        scene.render.filepath = str(frames / 'frame_')
        bpy.ops.render.render(animation=True)
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-framerate', '24', '-i', str(frames/'frame_%04d.png'),
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', str(output/'preview.mp4')], check=True)
    print('P5_CONTACT_PASS', json.dumps({'output': str(output), 'contacts': summary,
          'third_actor_max_change': max_third_change, 'feet_max_change': max_feet_change,
          'released_arm_max_matrix_change': max_release_pose_change, 'reload_passed': True,
          'released_joint_displacement_m': release_joint_changes,
          'all_actor_framing_margin': visible_margin,
          'with_support_steps': args.with_steps,
          'hit_confirmed': result['hit_confirmed']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
