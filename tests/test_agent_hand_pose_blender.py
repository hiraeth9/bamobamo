"""Real finger-only fades over moving library bodies, close-ups and cold reload.

./blender -b -t 2 --python-exit-code 1 --python tests/test_agent_hand_pose_blender.py
Uses a new /tmp directory. No model, live MCP or historical job is involved.
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
JOB = 'finger_pose_layers'
PROFILES = ('beta', 'quaternius_mannequin_f')
STORE = 'wm_finger_pose_test'


def geometry(actor):
    import bpy
    from agent_motion_controls import read_pose
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {'pose': read_pose(actor['armature'], evaluated=True),
            'world': {bone.name: [float(v) for row in arm.matrix_world@bone.matrix for v in row]
                      for bone in arm.pose.bones},
            'root': [float(v) for row in actor['root'].matrix_world for v in row]}


def maximum_error(left, right):
    return max(abs(a-b) for a, b in zip(left, right))


def finger_measurements(actor, side):
    import bpy
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    bones = {bone.name.split(':')[-1]: bone for bone in arm.pose.bones}
    rows = {}
    for digit in ('Index', 'Middle', 'Ring', 'Pinky'):
        names = [side+'Hand'+digit+str(i) for i in (1, 2, 3)]
        points = [arm.matrix_world@bones[name].head for name in names]
        # The actual terminal segment is available on both tested registered rigs.
        points.append(arm.matrix_world@bones[names[-1]].tail)
        segments = [b-a for a, b in zip(points, points[1:])]
        rows[digit] = {'chain_ratio': (points[-1]-points[0]).length/sum(v.length for v in segments),
                       'joint_bends_deg': [math.degrees(a.angle(b)) for a, b in zip(segments, segments[1:])]}
    return rows


def check_fades(actor, profile):
    """Compare actual intermediate frames to that frame's moving library hand."""
    import bpy
    from mathutils import Quaternion
    from agent_motion import sample_motion
    from agent_motion_controls import read_pose
    from agent_motion_timeline import _quat_mix, event_weight, timeline_info
    events = timeline_info(JOB, profile)['events']
    base = next(event for event in events if not event.get('mask'))
    errors = []
    for frame in (10, 26, 36, 61):
        event = next(event for event in events if event.get('mask') and
                     event['start_frame'] < frame < event['end_frame'])
        bpy.context.scene.frame_set(frame); bpy.context.view_layer.update()
        actual = read_pose(actor['armature'], evaluated=True)
        time = base['source_window'][0]+(frame-base['start_frame'])/30
        source = sample_motion(actor['armature'], base['motion_id'], time,
                               source_window=base['source_window'])['pose']
        for name, channels in event['keyframes'][0]['pose'].items():
            expected = Quaternion(_quat_mix(source[name]['rotation_quaternion'],
                                             channels['rotation_quaternion'], event_weight(event, frame)))
            measured = Quaternion(actual[name]['rotation_quaternion'])
            error = 2*math.acos(min(1., abs(expected.normalized().dot(measured.normalized()))))
            errors.append(error)
    assert max(errors) < .0015, (profile, 'actual fade differs from moving source slerp', max(errors))
    print('HAND_POSE_ACTUAL_FADE_MAX_ANGLE_ERROR_RAD', profile, max(errors), flush=True)


def render_hand(actor, profile, side, frame, output, actors):
    import bpy
    from mathutils import Vector
    scene = bpy.context.scene
    scene.frame_set(frame); bpy.context.view_layer.update()
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    bones = {bone.name.split(':')[-1]: bone for bone in arm.pose.bones}
    def point(name):
        return arm.matrix_world@bones[side+'Hand'+name].head
    wrist, middle, index, pinky = point(''), point('Middle1'), point('Index1'), point('Pinky1')
    forward, across = (middle-wrist).normalized(), (index-pinky).normalized()
    palmar = (across*(1 if side == 'Right' else -1)).cross(forward).normalized()
    center = wrist.lerp(middle, .8)
    outward = center-(arm.matrix_world@bones['Hips'].head)
    if palmar.dot(outward) < 0:
        palmar = -palmar
    camera = scene.camera
    camera.location = center+palmar*.45+forward*.10+across*.07
    camera.rotation_euler = (center-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type, camera.data.ortho_scale = 'ORTHO', .27
    for other in actors.values():
        for mesh in other['meshes']:
            mesh.hide_render = other is not actor
    scene.render.filepath = str(output/('%s_%s_%03d.png' % (profile, side, frame)))
    bpy.ops.render.render(write_still=True)
    for other in actors.values():
        for mesh in other['meshes']:
            mesh.hide_render = False


def main():
    import bpy
    import duel_plan
    from mathutils import Quaternion
    from agent_character import actor_ensure, resolve_actor
    from agent_hand_pose import hand_pose_events
    from agent_interactions import register_interaction
    from agent_motion_timeline import motion_commit, register_event, timeline_info
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output = args.output or Path(tempfile.mkdtemp(prefix='bamo_hand_pose_'))
    output.mkdir(parents=True, exist_ok=True)
    duel_plan.JOBS = output/'jobs'
    if args.reload:
        data = json.loads(bpy.context.scene[STORE])
        for profile in PROFILES:
            actor = resolve_actor(JOB, profile)
            assert len(timeline_info(JOB, profile)['events']) == 3
            for frame, expected in data[profile].items():
                bpy.context.scene.frame_set(int(frame)); bpy.context.view_layer.update()
                actual = geometry(actor)
                assert maximum_error(actual['root'], expected['root']) < 3e-5
                assert max(maximum_error(actual['world'][name], matrix)
                           for name, matrix in expected['world'].items()) < 3e-5
            bpy.context.scene.frame_set(40); bpy.context.view_layer.update()
            for side in ('Left', 'Right'):
                measures = finger_measurements(actor, side)
                assert all(v['joint_bends_deg'][0] > 75 and v['joint_bends_deg'][1] > 45
                           for v in measures.values()), (profile, side, measures)
            check_fades(actor, profile)
        print('HAND_POSE_COLD_RELOAD_PASS', output, flush=True)
        return

    assert not (output/'scene.blend').exists(), 'Choose a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end, scene.render.fps = 1, 64, 30
    (duel_plan.JOBS/JOB).mkdir(parents=True)
    (duel_plan.JOBS/JOB/'request.json').write_text('{"prompt":"isolated finger overlay validation"}')
    actors = {}
    for index, profile in enumerate(PROFILES):
        actor_ensure(JOB, profile, character_id=profile, position=((index-.5)*2, 0, 0), yaw=(index-.5)*.6)
        actor = resolve_actor(JOB, profile); actors[profile] = actor
        source = 'mixamo.combat_melee.jab_cross' if profile == 'beta' else 'cmu.79_08'
        start = 0 if profile == 'beta' else 1/30
        register_event(JOB, profile, 'original_body', {
            'event_id': 'original_body', 'source': 'library', 'motion_id': source,
            'source_window': [start, start+2.1], 'time_scale': 1, 'fit': False,
            'start_frame': 1, 'end_frame': 64, 'root_strategy': 'source'})
        motion_commit(JOB, profile, 1, 64)

    baseline = {}
    for profile, actor in actors.items():
        baseline[profile] = {}
        for frame in range(1, 65):
            scene.frame_set(frame); bpy.context.view_layer.update()
            baseline[profile][frame] = geometry(actor)
        for event_id, first, last, hands in [('left_only', 7, 29, 'LeftHand'),
                                              ('both_hands', 33, 64, ('Left', 'Right'))]:
            plan = hand_pose_events(JOB, profile, event_id, start_frame=first, end_frame=last, hands=hands)
            register_interaction(JOB, plan)
        motion_commit(JOB, profile, 1, 64)

    expected, greatest_body_error = {}, 0
    for profile, actor in actors.items():
        expected[profile] = {}
        mask = {name for event in timeline_info(JOB, profile)['events'] if event.get('mask') for name in event['mask']}
        finger_changes = []
        for frame in range(1, 65):
            scene.frame_set(frame); bpy.context.view_layer.update()
            actual, original = geometry(actor), baseline[profile][frame]
            greatest_body_error = max(greatest_body_error, maximum_error(actual['root'], original['root']))
            for name in original['world']:
                short = name.split(':')[-1]
                is_finger = any(short.startswith(side+'Hand'+digit) for side in ('Left', 'Right')
                                for digit in ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky'))
                if not is_finger:
                    greatest_body_error = max(greatest_body_error, maximum_error(actual['world'][name], original['world'][name]))
                if frame <= 32 and short.startswith('RightHand'):
                    assert maximum_error(actual['world'][name], original['world'][name]) < 3e-5
                if name in mask:
                    # Finger translations/scales stay from the per-frame source.
                    for channel in ('location', 'scale'):
                        # Beta's centimetre local channels are recovered from
                        # evaluated matrices; 1e-4 is one micrometre at its scale.
                        assert maximum_error(actual['pose'][name][channel], original['pose'][name][channel]) < 1e-4, (
                            profile, frame, name, channel, actual['pose'][name][channel], original['pose'][name][channel])
                    if frame in (7, 29, 33, 64):
                        assert maximum_error(actual['world'][name], original['world'][name]) < 3e-5
            if frame in (16, 40):
                for side in (('Left',) if frame == 16 else ('Left', 'Right')):
                    measures = finger_measurements(actor, side)
                    print('ACTUAL_CLOSED_FINGERS', profile, side, frame, json.dumps(measures), flush=True)
                    assert all(v['joint_bends_deg'][0] > 75 and v['joint_bends_deg'][1] > 45
                               for v in measures.values()), (profile, side, measures)
                    name = next(n for n in mask if n.split(':')[-1] == side+'HandIndex2')
                    finger_changes.append(Quaternion(actual['pose'][name]['rotation_quaternion']).rotation_difference(
                        Quaternion(original['pose'][name]['rotation_quaternion'])).angle)
            if frame in (1, 7, 10, 16, 29, 33, 36, 40, 58, 61, 64):
                expected[profile][str(frame)] = actual
        assert max(finger_changes) > .1, (profile, 'finger overlay changed the source by less than .1 radians', finger_changes)
        print('HAND_POSE_SOURCE_TO_FIST_JOINT_CHANGE_RADIANS', profile, finger_changes, flush=True)
        # Dynamic full-body source has real hand movement, independent of the overlay.
        wrist = next(n for n in baseline[profile][1]['world'] if n.split(':')[-1] == 'LeftHand')
        assert max(maximum_error(baseline[profile][1]['world'][wrist], row['world'][wrist])
                   for row in baseline[profile].values()) > .1
        check_fades(actor, profile)
    assert greatest_body_error < 3e-5, greatest_body_error
    print('HAND_POSE_NON_FINGER_AND_ROOT_MAX_ERROR', greatest_body_error, flush=True)

    scene.world = bpy.data.worlds.new('HandPoseWorld'); scene.world.color = (.32, .32, .32)
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light, scene.display.shading.color_type = 'STUDIO', 'MATERIAL'
    scene.display.shading.show_cavity = True
    scene.display.shading.show_specular_highlight = False
    scene.display.shading.background_type = 'WORLD'
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    data = bpy.data.cameras.new('HandCloseup'); camera = bpy.data.objects.new('HandCloseup', data)
    scene.collection.objects.link(camera); scene.camera = camera
    for profile, actor in actors.items():
        for side, frame in [('Left', 7), ('Left', 16), ('Left', 40), ('Right', 40), ('Left', 64)]:
            render_hand(actor, profile, side, frame, output, actors)
    scene[STORE] = json.dumps(expected)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'scene.blend'), check_existing=False)
    command = [str(PROJECT/'blender'), '-b', str(output/'scene.blend'), '-t', '2',
               '--python-exit-code', '1', '--python', str(Path(__file__).resolve()),
               '--', '--reload', '--output', str(output)]
    subprocess.run(command, check=True)
    print('HAND_POSE_REAL_PASS', output, flush=True)


if __name__ == '__main__':
    main()
