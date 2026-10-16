"""Original-speed full-body library combination, without procedural attacks.

Blender --python this_file -- --output /new/directory --render
The new scene retains the original Beta skin. No model/API calls, live scene,
historical tasks or budgets are involved. Numeric checks and true frames are
printed/rendered locally; no separate audit report is produced.
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
JOB = 'library_sequence_original_speed'
ACTOR = 'performer-27'
STORE = 'wm_library_sequence_demo'


def setup_render():
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
    scene.render.resolution_x, scene.render.resolution_y = 640, 360
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, -.015))
    floor = bpy.context.object
    floor.name = 'SequenceFloor'
    material = bpy.data.materials.new('SequenceFloorGray')
    material.diffuse_color = (.32, .32, .32, 1)
    floor.data.materials.append(material)
    data = bpy.data.cameras.new('SequenceFullBody')
    camera = bpy.data.objects.new('SequenceFullBody', data)
    scene.collection.objects.link(camera)
    camera.location = (3.6, -6, 2.6)
    camera.rotation_euler = (Vector((0, -.1, .95))-camera.location).to_track_quat('-Z', 'Y').to_euler()
    data.type, data.ortho_scale = 'ORTHO', 5.2
    scene.camera = camera


def scene_samples(actor):
    import bpy
    from mathutils import Vector
    from bpy_extras.object_utils import world_to_camera_view
    scene = bpy.context.scene
    rows, t_frames = {}, []
    margin = 1.
    for frame in range(scene.frame_start, scene.frame_end+1):
        scene.frame_set(frame); bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        arm = actor['armature'].evaluated_get(graph)
        root = actor['root'].evaluated_get(graph)
        points = {side+part: arm.matrix_world @ arm.pose.bones['mixamorig:'+side+part].head
                  for side in ('Left', 'Right') for part in ('Arm', 'ForeArm', 'Hand', 'Foot')}
        rows[frame] = {'root': root.matrix_world.translation.copy(), 'points': points, 'elbows': {}}
        both_horizontal = True
        for side in ('Left', 'Right'):
            shoulder, elbow, wrist = [points[side+part] for part in ('Arm', 'ForeArm', 'Hand')]
            angle = math.degrees((shoulder-elbow).angle(wrist-elbow))
            reach = (wrist-shoulder).length / ((elbow-shoulder).length+(wrist-elbow).length)
            rows[frame]['elbows'][side] = {'angle_deg': angle, 'reach_ratio': reach}
            both_horizontal = both_horizontal and angle > 165 and abs(wrist.z-shoulder.z) < .12 and abs(wrist.y-shoulder.y) < .12
        if both_horizontal:
            t_frames.append(frame)
        for obj in actor['meshes']:
            evaluated = obj.evaluated_get(graph)
            for corner in evaluated.bound_box:
                uv = world_to_camera_view(scene, scene.camera, evaluated.matrix_world @ Vector(corner))
                assert uv.z > 0
                margin = min(margin, uv.x, 1-uv.x, uv.y, 1-uv.y)
    assert not t_frames, ('unexpected horizontal T posture', t_frames)
    assert margin > .02, ('actor leaves full-body framing', margin)
    return rows, margin


def measure(actor, sequence, events):
    import bpy
    from mathutils import Matrix, Quaternion, Vector
    from agent_motion import sample_motion
    from agent_motion_controls import read_pose
    rows, margin = scene_samples(actor)
    origins = {events[0]['event_id']: Vector((0, 0, 0))}

    def source_root(event, frame):
        start = event['source_window'][0]
        time = start+(frame-event['start_frame'])/30*event['time_scale']
        first = sample_motion(actor['armature'], event['motion_id'], start, source_window=event['source_window'])
        current = sample_motion(actor['armature'], event['motion_id'], time, source_window=event['source_window'])
        return Vector(current['root'])-Vector(first['root'])

    def source_pose(event, frame):
        time = event['source_window'][0]+(frame-event['start_frame'])/30*event['time_scale']
        return sample_motion(actor['armature'], event['motion_id'], time, source_window=event['source_window'])['pose']

    def source_points(event, frame):
        pose, matrices = source_pose(event, frame), {}
        for bone in actor['armature'].data.bones:
            channels = pose[bone.name]
            basis = Matrix.LocRotScale(Vector(channels['location']), Quaternion(channels['rotation_quaternion']), Vector(channels['scale']))
            parents = ({'parent_matrix': matrices[bone.parent.name], 'parent_matrix_local': bone.parent.matrix_local}
                       if bone.parent else {})
            matrices[bone.name] = bone.convert_local_to_pose(basis, bone.matrix_local, **parents)
        root = origins[event['event_id']]+source_root(event, frame)
        return {part: (actor['armature'].matrix_basis @ matrices['mixamorig:'+part]).translation+root
                for part in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot')}

    for outgoing, incoming in zip(events, events[1:]):
        origins[incoming['event_id']] = origins[outgoing['event_id']]+source_root(outgoing, incoming['start_frame'])
    by_id = {event['event_id']: event for event in events}
    transitions = []
    for transition in sequence['transitions']:
        first, last = transition['frame_start'], transition['frame_end']
        measurements, actual_peak_frames = {}, {}
        for part in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot'):
            peak_frame = max(range(max(2, first), last+2),
                             key=lambda frame: (rows[frame]['points'][part]-rows[frame-1]['points'][part]).length)
            measurements[part] = (rows[peak_frame]['points'][part]-rows[peak_frame-1]['points'][part]).length
            actual_peak_frames[part] = peak_frame
        root_step = max((rows[frame]['root']-rows[frame-1]['root']).length
                        for frame in range(max(2, first), last+2))
        outgoing = by_id[transition['from_event_id']]
        incoming = by_id[transition['to_event_id']]
        # A source punch may step forward by centimetres in one frame. Preserve
        # that motion; continuity means matching the moving source at the seam.
        first_expected = origins[outgoing['event_id']]+source_root(outgoing, first)
        last_expected = origins[incoming['event_id']]+source_root(incoming, last)
        root_error = max((rows[first]['root']-first_expected).length,
                         (rows[last]['root']-last_expected).length)
        assert root_error < 2e-5, ('root blend does not meet its moving source endpoints', transition, root_error)
        from agent_motion_timeline import blend_pose, event_weight
        dynamic_root_error, dynamic_pose_error = 0., 0.
        for frame in range(first, last+1):
            weight = event_weight(incoming, frame)
            old_root = origins[outgoing['event_id']]+source_root(outgoing, frame)
            new_root = origins[incoming['event_id']]+source_root(incoming, frame)
            dynamic_root_error = max(dynamic_root_error, (rows[frame]['root']-old_root.lerp(new_root, weight)).length)
            expected_pose = blend_pose(source_pose(outgoing, frame), source_pose(incoming, frame), weight)
            bpy.context.scene.frame_set(frame); bpy.context.view_layer.update()
            actual_pose = read_pose(actor['armature'], evaluated=True)
            for bone, channels in expected_pose.items():
                angle = Quaternion(channels['rotation_quaternion']).rotation_difference(
                    Quaternion(actual_pose[bone]['rotation_quaternion'])).angle
                dynamic_pose_error = max(dynamic_pose_error, math.degrees(min(angle, 2*math.pi-angle)))
        assert dynamic_root_error < 2e-5, ('dynamic root crossfade differs from both moving sources', dynamic_root_error)
        assert dynamic_pose_error < .1, ('dynamic pose crossfade differs from both moving sources', dynamic_pose_error)
        source_steps = {part: 0. for part in measurements}
        source_peak_frames = {}
        for event in (outgoing, incoming):
            previous = source_points(event, max(1, first-1))
            for frame in range(max(2, first), last+2):
                current = source_points(event, frame)
                for part in measurements:
                    displacement = (current[part]-previous[part]).length
                    if displacement > source_steps[part]:
                        source_steps[part] = displacement
                        source_peak_frames[part] = {'event_id': event['event_id'], 'scene_frame': frame,
                            'source_time_s': event['source_window'][0]+(frame-event['start_frame'])/30*event['time_scale']}
                previous = current
        transitions.append({'start_frame': first, 'end_frame': last,
                            'max_root_frame_step_m': root_step, 'max_limb_frame_step_m': measurements,
                            'native_source_max_limb_frame_step_m': source_steps,
                            'actual_peak_step_frame': actual_peak_frames,
                            'native_peak_step_frame': source_peak_frames,
                            'root_endpoint_source_error_m': root_error,
                            'dynamic_root_max_error_m': dynamic_root_error,
                            'dynamic_pose_max_angle_error_deg': dynamic_pose_error})
    # Outside overlap windows, the baked full-body channels must still come
    # directly from the original asset. No custom guard or attack may cover it.
    angular_error = 0.
    for index, segment in enumerate(sequence['segments']):
        first = segment['frame_start']+segment['blend_in_frames']+1
        last = segment['frame_end']
        if index+1 < len(sequence['segments']):
            last = min(last, sequence['segments'][index+1]['frame_start']-1)
        if first > last:
            continue
        frame = (first+last)//2
        event = events[index]
        source_time = event['source_window'][0]+(frame-event['start_frame'])/30*event['time_scale']
        expected = sample_motion(actor['armature'], event['motion_id'], source_time, source_window=event['source_window'])['pose']
        bpy.context.scene.frame_set(frame); bpy.context.view_layer.update()
        actual = read_pose(actor['armature'], evaluated=True)
        for bone, channels in expected.items():
            angle = Quaternion(channels['rotation_quaternion']).rotation_difference(
                Quaternion(actual[bone]['rotation_quaternion'])).angle
            angular_error = max(angular_error, math.degrees(min(angle, 2*math.pi-angle)))
    assert angular_error < .1, ('source pose was overridden', angular_error)
    jab = next(segment for segment in sequence['segments'] if segment['motion_id'].endswith('.jab_cross'))
    peaks = {}
    for side, source_time in (('Left', 13/30), ('Right', 21/30)):
        frame = jab['frame_start']+round(source_time*30)
        peak = rows[frame]['elbows'][side]
        assert peak['reach_ratio'] > .94, (side, peak)
        peaks[side] = {'frame': frame, **peak}
    kick = next(segment for segment in sequence['segments'] if segment['motion_id'].endswith('.mma_kick'))
    highest = max(rows[frame]['points']['RightFoot'].z for frame in range(kick['frame_start'], kick['frame_end']+1))
    assert highest > 1., ('original high kick missing', highest)
    return {'frame_count': len(rows), 'fps': 30, 'all_frame_framing_margin': margin,
            'transitions': transitions, 'source_pose_max_angle_error_deg': angular_error,
            'original_jab_cross_extension': peaks, 'original_kick_ankle_max_z_m': highest,
            'horizontal_T_frames': 0, 'contact_or_dynamics_claim': False}


def render_video(output):
    import bpy
    scene = bpy.context.scene
    frames = output/'frames'
    frames.mkdir()
    scene.render.filepath = str(frames/'frame_')
    bpy.ops.render.render(animation=True)
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-framerate', '30', '-i', str(frames/'frame_%04d.png'),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', str(output/'preview.mp4')], check=True)


def main():
    import bpy
    import duel_plan
    from agent_character import actor_ensure, resolve_actor
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--scene', type=Path)
    parser.add_argument('--transition-frames', type=int, default=6)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='bamo_library_sequence_'))
    duel_plan.JOBS = output / 'jobs'
    if args.scene:
        bpy.ops.wm.open_mainfile(filepath=str(args.scene))
        saved = json.loads(bpy.context.scene[STORE])
        result = measure(resolve_actor(JOB, ACTOR), saved['sequence'], saved['events'])
        print('LIBRARY_SEQUENCE_RELOAD_PASS', json.dumps(result), flush=True)
        if args.render:
            render_video(output)
        return
    from agent_interactions import register_interaction
    from agent_motion_sequence import library_sequence_events
    from agent_motion_timeline import motion_commit
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'scene.blend').exists(), 'Choose a new output directory'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end, scene.render.fps = 1, 300, 30
    scene.world = bpy.data.worlds.new('LibrarySequenceWorld')
    folder = duel_plan.JOBS / JOB
    folder.mkdir(parents=True)
    (folder/'request.json').write_text(json.dumps({'prompt': '单人全身原速：格斗待机，一二连拳接勾拳，高踢，回到待机；使用原始素材全身发力，镜头包含全身',
        'character_policy': 'library_beta'}, ensure_ascii=False), encoding='utf-8')
    actor_ensure(JOB, ACTOR, color=(.72, .75, .80, 1))
    plan = library_sequence_events(JOB, ACTOR, 'whole_body_source_combo', [
        {'motion_id': 'mixamo.combat_melee.mma_idle', 'source_window': [0, .6]},
        {'motion_id': 'mixamo.combat_melee.jab_cross'},
        {'motion_id': 'mixamo.combat_melee.hook', 'source_window': [0, 2.4]},
        {'motion_id': 'mixamo.combat_melee.mma_kick'},
        {'motion_id': 'mixamo.combat_melee.mma_idle', 'source_window': [0, .6]}], transition_frames=args.transition_frames)
    assert plan['ok'], plan
    sequence = plan['sequence']
    assert all(event['source'] == 'library' and not event.get('mask') and
               not event.get('targets') and not event.get('keyframes') and event['time_scale'] == 1
               for event in plan['events']), 'The demo must retain original full-body library animation'
    scene.frame_end = sequence['frame_end']
    register_interaction(JOB, plan)
    print('LIBRARY_SEQUENCE_LAYOUT', json.dumps(sequence), flush=True)
    motion_commit(JOB, ACTOR, scene.frame_start, scene.frame_end)
    setup_render()
    actor = resolve_actor(JOB, ACTOR)
    result = measure(actor, sequence, plan['events'])
    scene[STORE] = json.dumps({'sequence': sequence, 'events': plan['events']})
    blend = output/'scene.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    with (output/'reload.log').open('w') as log:
        run = subprocess.run([str(PROJECT/'blender'), '-b', '-t', '2', '--python-exit-code', '1', '--python',
             str(Path(__file__).resolve()), '--', '--scene', str(blend), '--output', str(output)],
             stdout=log, stderr=subprocess.STDOUT, timeout=120)
    assert run.returncode == 0, 'Saved reload failed; see '+str(output/'reload.log')
    selected = {1, scene.frame_end}
    for transition in sequence['transitions']:
        selected.update((transition['frame_start'], transition['frame_end']))
    selected.update(peak['frame'] for peak in result['original_jab_cross_extension'].values())
    for segment in sequence['segments']:
        if segment['motion_id'].endswith('.hook'):
            selected.add(segment['frame_start']+29)
        if segment['motion_id'].endswith('.mma_kick'):
            selected.add(segment['frame_start']+20)
    images = output/'keyframes'
    images.mkdir()
    for frame in sorted(selected):
        scene.frame_set(frame)
        scene.render.filepath = str(images/('frame_%03d.png'%frame))
        bpy.ops.render.render(write_still=True)
    if args.render:
        render_video(output)
    print('LIBRARY_SEQUENCE_PASS', json.dumps({'output': str(output), **result}), flush=True)


if __name__ == '__main__':
    main()
