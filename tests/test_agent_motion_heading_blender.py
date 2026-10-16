"""Original-speed source turns followed by aligned walking on Beta and CC0.

Checks real FK heading, raw body-pose retention, both moving root sources,
single/group composition and cold reload. New /tmp outputs only; no paid model.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'scripts'))
JOB = 'source_heading_alignment'
PROFILES = ('beta', 'quaternius_mannequin_f')
FIRST, SEAM, OVERLAP, LAST = 1, 123, 6, 153
STORE = 'wm_heading_alignment_test'


def wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def snapshot(actor):
    import bpy
    from agent_motion_controls import read_pose
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    across = arm.matrix_world@arm.pose.bones['mixamorig:LeftUpLeg'].head-arm.matrix_world@arm.pose.bones['mixamorig:RightUpLeg'].head
    return {'root': [float(v) for row in actor['root'].matrix_basis for v in row],
            'heading': math.atan2(across.y, across.x),
            'pose': read_pose(actor['armature'], evaluated=True),
            'world': {bone.name: list(arm.matrix_world@bone.head) for bone in arm.pose.bones}}


def compare(actual, expected):
    root = max(abs(a-b) for a, b in zip(actual['root'], expected['root']))
    point = max(math.dist(actual['world'][name], position) for name, position in expected['world'].items())
    assert root < 3e-5 and point < 3e-5, (root, point)


def inspect(actor, profile, origin, initial_yaw):
    import bpy
    from mathutils import Quaternion, Vector
    from agent_motion import sample_motion
    from agent_motion_features import pose_heading
    from agent_motion_timeline import event_weight, timeline_info
    scene = bpy.context.scene
    events = [event for event in timeline_info(JOB, profile)['events'] if not event.get('mask')]
    first, second = events[:2]
    def source(event, frame):
        time = event['source_window'][0]+(frame-event['start_frame'])/30
        return sample_motion(actor['armature'], event['motion_id'], time, source_window=event['source_window'])
    first_start = source(first, FIRST)
    rotation_a = Quaternion((0, 0, 1), initial_yaw-pose_heading(actor['armature'], first_start['pose']))
    start_a = Vector(first_start['root'])
    def root_a(frame):
        return Vector(origin)+rotation_a@(Vector(source(first, frame)['root'])-start_a)
    outgoing = source(first, SEAM)
    second_start = source(second, SEAM)
    rotation_b = rotation_a@Quaternion((0, 0, 1),
        wrap(pose_heading(actor['armature'], outgoing['pose'])-pose_heading(actor['armature'], second_start['pose'])))
    origin_b, start_b = root_a(SEAM), Vector(second_start['root'])
    rows, root_error, body_error, heading_error = {}, 0., 0., 0.
    raw_before = {frame: source(first if frame < SEAM else second, frame)['pose']
                  for frame in (1, 60, 120, 135, LAST)}
    for frame in range(FIRST, LAST+1):
        scene.frame_set(frame); bpy.context.view_layer.update()
        actual = snapshot(actor); rows[frame] = actual
        event = first if frame < SEAM else second
        native = source(event, frame)
        expected_root = root_a(frame) if frame < SEAM else origin_b+rotation_b@(Vector(native['root'])-start_b)
        if SEAM <= frame <= SEAM+OVERLAP:
            expected_root = root_a(frame).lerp(expected_root, event_weight(second, frame))
        root_error = max(root_error, (actor['root'].location-expected_root).length)
        if frame < SEAM or frame >= SEAM+OVERLAP:
            for name, channels in native['pose'].items():
                if name.split(':')[-1].startswith(('LeftHandThumb', 'LeftHandIndex', 'LeftHandMiddle', 'LeftHandRing', 'LeftHandPinky')):
                    continue  # Deliberate local finger layer is tested separately.
                expected = Quaternion(channels['rotation_quaternion']).normalized()
                measured = Quaternion(actual['pose'][name]['rotation_quaternion']).normalized()
                body_error = max(body_error, 2*math.acos(min(1., abs(expected.dot(measured)))))
            rotation = rotation_a if frame < SEAM else rotation_b
            yaw = math.atan2((rotation@Vector((1, 0, 0))).y, (rotation@Vector((1, 0, 0))).x)
            expected_heading = yaw+pose_heading(actor['armature'], native['pose'])
            heading_error = max(heading_error, abs(wrap(actual['heading']-expected_heading)))
    assert root_error < 3e-5, (profile, root_error)
    assert body_error < .0015, (profile, body_error)
    assert heading_error < 2e-5, (profile, heading_error)
    assert abs(wrap(rows[1]['heading']-initial_yaw)) < 2e-5
    source_turn = wrap(pose_heading(actor['armature'], source(first, 120)['pose'])-
                       pose_heading(actor['armature'], first_start['pose']))
    actual_turn = wrap(rows[120]['heading']-rows[1]['heading'])
    assert abs(source_turn) > math.radians(70), ('turn was not actually exercised', profile, source_turn)
    assert abs(wrap(actual_turn-source_turn)) < 2e-5
    # Sampling after composition still returns the same source data.
    assert all(source(first if frame < SEAM else second, frame)['pose'] == value
               for frame, value in raw_before.items())
    provenance = timeline_info(JOB, profile)['last_commit']['sources']
    assert all(entry['heading_alignment']['source_turn_preserved'] for entry in provenance)
    print('HEADING_ALIGNMENT_REAL_FACTS', profile, json.dumps({
        'first_world_heading_deg': math.degrees(rows[1]['heading']),
        'original_turn_deg': math.degrees(source_turn), 'actual_turn_deg': math.degrees(actual_turn),
        'end_world_heading_deg': math.degrees(rows[LAST]['heading']),
        'root_source_max_error_m': root_error, 'body_pose_max_angle_error_deg': math.degrees(body_error),
        'body_heading_max_error_deg': math.degrees(heading_error),
        'support_or_naturalness_verified': False}), flush=True)
    return rows


def main():
    import bpy
    import duel_plan
    from mathutils import Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_hand_pose import hand_pose_events
    from agent_interactions import register_interaction
    from agent_motion_timeline import motion_commit, register_event, sample_keyframes
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--reload', action='store_true')
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output = args.output or Path(tempfile.mkdtemp(prefix='bamo_heading_alignment_'))
    output.mkdir(parents=True, exist_ok=True); duel_plan.JOBS = output/'jobs'
    if args.reload:
        data = json.loads(bpy.context.scene[STORE])
        for profile in PROFILES:
            actor = resolve_actor(JOB, profile)
            for frame, expected in data['expected'][profile].items():
                bpy.context.scene.frame_set(int(frame)); bpy.context.view_layer.update()
                compare(snapshot(actor), expected)
            inspect(actor, profile, data['origins'][profile], data['yaws'][profile])
        actor = resolve_actor(JOB, 'beta')
        for frame, expected_root in data['world_trajectory'].items():
            bpy.context.scene.frame_set(int(frame)); bpy.context.view_layer.update()
            assert (actor['root'].location-Vector(expected_root)).length < 3e-5
        print('HEADING_ALIGNMENT_COLD_RELOAD_PASS', output, flush=True)
        return
    assert not (output/'scene.blend').exists(), 'Choose a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene; scene.frame_start, scene.frame_end, scene.render.fps = FIRST, LAST, 30
    (duel_plan.JOBS/JOB).mkdir(parents=True)
    (duel_plan.JOBS/JOB/'request.json').write_text('{"prompt":"original speed turn and walk heading validation"}')
    actors, origins, yaws = {}, {}, {}
    for index, profile in enumerate(PROFILES):
        origins[profile], yaws[profile] = [(index-.5)*5, 0, 0], math.radians(35 if index == 0 else -25)
        actor_ensure(JOB, profile, character_id=profile, position=origins[profile], yaw=yaws[profile])
        actors[profile] = resolve_actor(JOB, profile)
        for name, identifier, window, begin, finish, blend in [
            ('original_turn', 'cmu.16_17', [1/30, 4.3], FIRST, 129, 0),
            ('following_walk', 'cmu.02_01', [1/30, 1+1/30], SEAM, LAST, OVERLAP)]:
            register_event(JOB, profile, name, {'event_id': name, 'source': 'library',
                'motion_id': identifier, 'source_window': window, 'time_scale': 1., 'fit': False,
                'start_frame': begin, 'end_frame': finish, 'blend_in_frames': blend,
                'root_strategy': 'source', 'align_heading': True})
        register_interaction(JOB, hand_pose_events(JOB, profile, 'left_fist', hands='Left',
            start_frame=FIRST, end_frame=LAST, blend_in_frames=0, blend_out_frames=0))
        motion_commit(JOB, profile, FIRST, LAST)
    singles = {profile: inspect(actor, profile, origins[profile], yaws[profile]) for profile, actor in actors.items()}
    coordinated = motion_commit(JOB, list(PROFILES), FIRST, LAST)
    assert coordinated['coordination'] == 'same_frame_base_reset_then_target_first_IK'
    for frame in range(FIRST, LAST+1):
        scene.frame_set(frame); bpy.context.view_layer.update()
        for profile, actor in actors.items():
            compare(snapshot(actor), singles[profile][frame])
    print('HEADING_ALIGNMENT_GROUP_EQUALS_SINGLE_PASS', flush=True)
    # An authored world-space trajectory remains world-space even when its
    # library body's entry heading is aligned to the preceding performance.
    scene.frame_end = LAST+12
    actor = actors['beta']; scene.frame_set(LAST); bpy.context.view_layer.update()
    start_position = actor['root'].location.copy()
    path = [{'frame': LAST+1, 'root': list(start_position), 'interpolation': 'linear'},
            {'frame': LAST+12, 'root': list(start_position+Vector((.6, .25, 0)))}]
    register_event(JOB, 'beta', 'world_path', {'event_id': 'world_path', 'source': 'library',
        'motion_id': 'cmu.02_01', 'source_window': [1/30, .4], 'time_scale': 1., 'fit': False,
        'start_frame': LAST+1, 'end_frame': LAST+12, 'root_strategy': 'trajectory',
        'root_path': path, 'align_heading': True})
    motion_commit(JOB, 'beta', LAST+1, LAST+12)
    for frame in range(FIRST, LAST+1):
        scene.frame_set(frame); bpy.context.view_layer.update()
        compare(snapshot(actor), singles['beta'][frame])
    trajectory = {}
    for frame in range(LAST+1, LAST+13):
        scene.frame_set(frame); bpy.context.view_layer.update()
        expected_root = sample_keyframes(path, frame)['root']
        assert (actor['root'].location-Vector(expected_root)).length < 3e-5
        trajectory[str(frame)] = expected_root
    print('HEADING_ALIGNMENT_WORLD_TRAJECTORY_UNCHANGED_PASS', flush=True)
    expected = {profile: {str(frame): rows[frame] for frame in (1, 60, 120, SEAM, 126, 129, 140, LAST)}
                for profile, rows in singles.items()}
    if args.render:
        scene.world = bpy.data.worlds.new('HeadingWorld'); scene.world.color = (.3, .3, .3)
        scene.render.engine = 'BLENDER_WORKBENCH'; scene.display.shading.color_type = 'MATERIAL'
        scene.display.shading.show_cavity = True; scene.display.shading.background_type = 'WORLD'
        scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 640, 480, 100
        bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, -.015))
        data = bpy.data.cameras.new('HeadingInspect'); camera = bpy.data.objects.new('HeadingInspect', data)
        scene.collection.objects.link(camera); scene.camera = camera; data.type = 'ORTHO'; data.ortho_scale = 3.2
        for profile, actor in actors.items():
            for other in actors.values():
                for mesh in other['meshes']:
                    mesh.hide_render = other is not actor
            for frame in (1, 120, LAST):
                scene.frame_set(frame); bpy.context.view_layer.update()
                center = Vector(snapshot(actor)['world']['mixamorig:Hips'])
                camera.location = center+Vector((3, -5, 1.6))
                camera.rotation_euler = (center-camera.location).to_track_quat('-Z', 'Y').to_euler()
                scene.render.filepath = str(output/('%s_%03d.png' % (profile, frame)))
                bpy.ops.render.render(write_still=True)
            for other in actors.values():
                for mesh in other['meshes']:
                    mesh.hide_render = False
    scene[STORE] = json.dumps({'origins': origins, 'yaws': yaws, 'expected': expected,
                              'world_trajectory': trajectory})
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'scene.blend'), check_existing=False)
    subprocess.run([str(PROJECT/'blender'), '-b', str(output/'scene.blend'), '-t', '2',
                    '--python-exit-code', '1', '--python', str(Path(__file__).resolve()),
                    '--', '--reload', '--output', str(output)], check=True)
    print('HEADING_ALIGNMENT_REAL_PASS', output, flush=True)


if __name__ == '__main__':
    main()
