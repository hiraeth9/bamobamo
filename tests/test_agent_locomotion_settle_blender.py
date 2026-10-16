"""Real zero→walk→pause→turn→walk foot/toe settling and saved-channel reload.

Run with Blender --background --python this_file -- --output /tmp/new-directory.
No model calls, production jobs, or independent reports are involved.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))


def main():
    import bpy
    from mathutils import Vector, Quaternion
    import duel_plan
    from agent_character import actor_ensure, resolve_actor
    from agent_locomotion import apply_locomotion
    from agent_motion_timeline import timeline_info
    from duel_scene import point
    import locomotion_gait

    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--reload')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])

    def snapshot(actor):
        arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        return {bone.name: (arm.matrix_world @ bone.matrix).copy() for bone in arm.pose.bones}

    def metrics(actor, frames):
        samples = []
        knee_angles, lean_angles, heading_errors = [], [], []
        for frame in frames:
            bpy.context.scene.frame_set(frame)
            data = snapshot(actor)
            p = lambda name: data['mixamorig:' + name].translation
            forward = actor['root'].matrix_world.to_quaternion() @ Vector((0, -1, 0))
            for side in ('Left', 'Right'):
                knee_angles.append(math.degrees((p(side+'UpLeg')-p(side+'Leg')).angle(p(side+'Foot')-p(side+'Leg'))))
                direction = p(side+'ToeBase')-p(side+'Foot');direction.z = 0
                heading_errors.append(math.degrees(direction.angle(forward)))
            lean_angles.append(math.degrees((p('Head')-p('Hips')).angle(Vector((0, 0, 1)))))
            graph = bpy.context.evaluated_depsgraph_get()
            skin = {side: [] for side in ('Left', 'Right')}
            for obj in actor['meshes']:
                groups = {g.index: g.name for g in obj.vertex_groups}
                evaluated = obj.evaluated_get(graph)
                for vertex in obj.data.vertices:
                    for side in skin:
                        if any(groups[w.group].startswith('mixamorig:' + side) and
                               any(part in groups[w.group] for part in ('Foot', 'Toe')) and w.weight >= .3
                               for w in vertex.groups):
                            skin[side].append(evaluated.matrix_world @ evaluated.data.vertices[vertex.index].co)
            samples.append((data, skin))
        first = samples[0]
        rotation = {part: max(math.degrees(first[0]['mixamorig:' + part].to_quaternion().rotation_difference(
            sample[0]['mixamorig:' + part].to_quaternion()).angle) for sample in samples)
                    for part in ('Hips', 'LeftFoot', 'RightFoot', 'LeftToeBase', 'RightToeBase', 'Head')}
        skin_motion = max((p - q).length for sample in samples for side in skin
                          for p, q in zip(first[1][side], sample[1][side]))
        floors = [min(p.z for p in sample[1][side]) for sample in samples for side in skin]
        return {'rotation_deg': rotation, 'foot_skin_motion_m': skin_motion,
                'skin_floor_range_m': [min(floors), max(floors)],
                'knee_interior_range_deg': [min(knee_angles), max(knee_angles)],
                'trunk_lean_range_deg': [min(lean_angles), max(lean_angles)],
                'maximum_toe_heading_error_deg': max(heading_errors)}

    if args.reload:
        bpy.ops.wm.open_mainfile(filepath=str(Path(args.reload).resolve()), use_scripts=False)
        out = Path(args.reload).parent
        duel_plan.JOBS = out / 'jobs'
        actor = resolve_actor('settle', 'New')
        for frames in (range(111, 120), range(232, 250)):
            value = metrics(actor, frames)
            assert max(value['rotation_deg'][part] for part in ('Hips', 'LeftFoot', 'RightFoot', 'LeftToeBase', 'RightToeBase')) < .05, value
            assert value['foot_skin_motion_m'] < .0001, value
            assert -.002 < value['skin_floor_range_m'][0] < .012, value
        print('FRESH_RELOAD_STANDING_PASSED', flush=True)
        return

    out = Path(args.output or tempfile.mkdtemp(prefix='bamo_locomotion_settle_')).resolve()
    out.mkdir(parents=True, exist_ok=True)
    duel_plan.JOBS = out / 'jobs'
    folder = duel_plan.JOBS / 'settle'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'request.json').write_text(json.dumps({'prompt': 'Isolated zero-walk-pause-turn-walk standing support regression'}))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 250
    scene.render.fps, scene.render.fps_base = 30, 1
    actor_ensure('settle', 'New', character_id='beta')
    actor_ensure('settle', 'Old', character_id='beta')
    new, old = resolve_actor('settle', 'New'), resolve_actor('settle', 'Old')

    def ease(value):
        value = max(0., min(1., value))
        return value * value * (3 - 2 * value)

    def speed(time):
        for start, end in ((.7, 2.7), (4.6, 6.3)):
            if start <= time < end:
                return min(ease((time - start) / .3), ease((end - time) / .3))
        return 0.

    path, xy, previous_speed, previous_yaw = [], Vector((0., 0., 0.)), 0., 0.
    for index in range(250):
        time = index / 30
        yaw = math.pi / 2 * ease((time - 4) / .6)
        velocity = speed(time)
        delta = (previous_speed + velocity) / 60 if index else 0.
        xy += Vector((math.sin(yaw), -math.cos(yaw), 0)) * delta
        moving = delta > 1e-8 or abs(yaw - previous_yaw) > 1e-8
        path.append({'frame': index + 1, 'root': list(xy), 'yaw': yaw, 'gait': 'walk' if moving else 'idle'})
        previous_speed, previous_yaw = velocity, yaw
    new_result = apply_locomotion('settle', 'New', path)
    print('NEW_SEQUENCE_FINISHED', json.dumps({'unreachable': new_result['unreachable_foot_targets'],
           'standing_reference': new_result['controller']['standing_reference']}), flush=True)
    assert not new_result['unreachable_foot_targets'], new_result['unreachable_foot_targets'][:5]

    # Use the actual controller without standing calibration/restart snapshots
    # as the untreated source-Idle comparison. No historical revision is needed.
    current_class = locomotion_gait.LocomotionGait
    class Baseline(current_class):
        def _calibrate_standing(self, root_yaw):
            pass
        def remember_pose(self, pose):
            pass
    locomotion_gait.LocomotionGait = Baseline
    try:
        old_result = apply_locomotion('settle', 'Old', path)
    finally:
        locomotion_gait.LocomotionGait = current_class
    new_event = next(e for e in timeline_info('settle', 'New')['events'] if e['event_id'] == 'locomotion_base')
    old_event = next(e for e in timeline_info('settle', 'Old')['events'] if e['event_id'] == 'locomotion_base')
    for a, b in zip(new_event['locomotion_measurements'], old_event['locomotion_measurements']):
        for key in ('gait_phase', 'gait_cycle', 'run_weight', 'stride_scale', 'motion_weight'):
            assert a[key] == b[key], (a['frame'], key, a[key], b[key])
    path_error = 0.
    walking_error = 0.
    saved = {}
    for frame in range(1, 251):
        scene.frame_set(frame)
        expected = Vector(path[frame - 1]['root'])
        actual = new['root'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        path_error = max(path_error, (actual.matrix_world.translation - expected).length)
        n, o = snapshot(new), snapshot(old)
        if frame in (52, 53, 54, 55, 181, 182, 183, 184):
            # Compare local rotation of the actual moving torso and lowerbody
            # after both source contact clocks have had a full replacement step.
            for name in ('Hips', 'Spine', 'LeftUpLeg', 'RightUpLeg', 'LeftToeBase', 'RightToeBase'):
                walking_error = max(walking_error, math.degrees(n['mixamorig:' + name].to_quaternion().rotation_difference(o['mixamorig:' + name].to_quaternion()).angle))
        if frame in (1, 22, 23, 100, 119, 121, 122, 139, 180, 232, 250):
            saved[frame] = n
    assert path_error < 1e-6, path_error
    assert walking_error < .05, walking_error
    new_stops = [metrics(new, frames) for frames in (range(1, 20), range(111, 120), range(232, 250))]
    old_stop = metrics(old, range(232, 250))
    for value in new_stops:
        assert max(value['rotation_deg'][part] for part in ('Hips', 'LeftFoot', 'RightFoot', 'LeftToeBase', 'RightToeBase')) < .05, value
        assert value['foot_skin_motion_m'] < .0001, value
        assert -.002 < value['skin_floor_range_m'][0] < .012, value
        assert value['rotation_deg']['Head'] > .05, 'Upper body was frozen'
        assert value['trunk_lean_range_deg'][1] < 5, value
    assert 165 < new_stops[0]['knee_interior_range_deg'][0] < 175, new_stops[0]
    assert new_stops[0]['maximum_toe_heading_error_deg'] < .1, new_stops[0]
    print('ACTUAL_SETTLING', json.dumps({'new_stops': new_stops, 'old_terminal': old_stop,
          'root_path_error_m': path_error, 'moving_pose_difference_deg': walking_error}), flush=True)

    # A full-frame geometry jump is reported separately from the intentional
    # walking motion; onset must begin from the last actually evaluated stance.
    for before, after in ((22, 23), (121, 122)):
        scene.frame_set(before); a = snapshot(new)
        scene.frame_set(after); b = snapshot(new)
        jump = max((a[name].translation - b[name].translation).length for name in a)
        print('RESTART_BONE_INTERVALS_M', before, after, sorted(
            ((name, (a[name].translation-b[name].translation).length) for name in a),
            key=lambda item:item[1], reverse=True)[:5], flush=True)
        assert jump < .01, (before, after, jump)
        print('RESTART_FIRST_INTERVAL_M', before, after, jump, flush=True)

    old['root'].hide_render = True
    for obj in [old['armature'], *old['meshes']]:
        obj.hide_render = True
    bpy.ops.mesh.primitive_plane_add(size=20)
    bpy.context.object.name = 'Ground'
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.world = bpy.data.worlds.new('World')
    scene.world.color = (.32, .32, .32)
    camera_data = bpy.data.cameras.new('StandingReview')
    camera = bpy.data.objects.new('StandingReview', camera_data)
    scene.collection.objects.link(camera);scene.camera = camera
    camera_data.type = 'ORTHO';camera_data.ortho_scale = 2.7
    scene.render.resolution_x, scene.render.resolution_y = 480, 540
    scene.render.resolution_percentage = 100
    for frame in (1, 119, 250):
        scene.frame_set(frame)
        center = new['root'].matrix_world.translation + Vector((0, 0, .9))
        camera.location = center + Vector((3, -6, 1.6))
        camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = str(out / ('standing_%03d.png' % frame))
        bpy.ops.render.render(write_still=True)
    saved_scene = out / 'scene.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved_scene))
    bpy.ops.wm.open_mainfile(filepath=str(saved_scene), use_scripts=False)
    new = resolve_actor('settle', 'New')
    reload_error = 0.
    for frame, expected in saved.items():
        bpy.context.scene.frame_set(frame)
        actual = snapshot(new)
        reload_error = max(reload_error, max(max(abs(expected[n][r][c] - actual[n][r][c]) for r in range(4) for c in range(4)) for n in expected))
    assert reload_error < 1e-6, reload_error
    command = [bpy.app.binary_path, '-b', '-t', '2', '--python-exit-code', '1', '--python', str(Path(__file__).resolve()), '--', '--reload', str(saved_scene)]
    subprocess.run(command, cwd=PROJECT, check=True)
    print('SETTLING_VALIDATION_PASSED', str(saved_scene), 'reload_error', reload_error, flush=True)


if __name__ == '__main__':
    main()
