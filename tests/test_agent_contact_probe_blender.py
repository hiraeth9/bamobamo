"""Real Beta submit/reload contact checks in temporary background Blender jobs.

Run: ./blender -b -t 2 --python-exit-code 1 --python tests/test_agent_contact_probe_blender.py
No production jobs, GUI, persistent reports or source snapshots are used.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
sys.path.insert(0, str(PROJECT / 'tests'))
import test_agent_contacts_blender as fixture


def fresh_probe(folder, jobs):
    environment = dict(os.environ)
    for key in ('DISPLAY', 'XDG_RUNTIME_DIR', 'GHOST_BACKEND', 'WAYLAND_DISPLAY'):
        environment.pop(key, None)
    environment['LP_NUM_THREADS'] = '1'
    command = [str(PROJECT / 'blender'), '-b', '-t', '2', '--python-exit-code', '1',
               '--python', str(Path(__file__).resolve()), '--',
               '--probe-manifest', str(folder / 'agent_scene.json'), '--jobs-root', str(jobs)]
    run = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=180)
    if run.returncode:
        raise RuntimeError('Fresh Blender probe failed:\n' + run.stdout[-4000:] + run.stderr[-2000:])
    line = next(line for line in run.stdout.splitlines() if line.startswith('CONTACT_PROBE_RESULT '))
    return json.loads(line.removeprefix('CONTACT_PROBE_RESULT '))


def build_fixture(jobs):
    import bpy
    from mathutils import Quaternion, Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_motion_timeline import register_event, motion_commit
    from agent_camera_quality import camera_requirements

    job, folder = fixture.new_scene(jobs, 'submit_probe')
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x, scene.render.resolution_y = 320, 180
    scene.render.resolution_percentage = 100
    actor_ensure(job, 'actor_left')
    actor = resolve_actor(job, 'actor_left')
    effector, initial, _ = fixture.pick_hand_vertex(actor)
    bpy.ops.mesh.primitive_cube_add(size=.08)
    prop = bpy.context.object
    prop.name = 'ContactTarget'
    prop.rotation_mode = 'QUATERNION'
    arm = actor['armature']
    rotation = (arm.matrix_world @ arm.pose.bones['mixamorig:RightHand'].matrix).to_quaternion()
    origin = initial - rotation @ prop.data.vertices[0].co
    for frame, amount in ((1, 0), (12, .5), (fixture.FRAMES, 1)):
        prop.location = origin + Vector((.035 * amount, .015 * amount, .025 * amount))
        prop.rotation_quaternion = rotation @ Quaternion((0, 0, 1), .65 * amount)
        prop.keyframe_insert('location', frame=frame)
        prop.keyframe_insert('rotation_quaternion', frame=frame)
    fixture.linear_keys(prop)
    scene.frame_set(1)
    event = fixture.pose_event(actor, 'procedural_skin_contact')
    binding = {'object': prop.name, 'vertex_index': 0}
    event['targets'] = {'RightHand': {'binding': binding, 'effector': effector,
        'contact': {'start_frame': 5, 'end_frame': 20, 'tolerance_m': .02, 'kind': 'touch'},
        'keyframes': [{'frame': 1, 'influence': 0}, {'frame': 5, 'influence': 1},
                      {'frame': 20, 'influence': 1}, {'frame': fixture.FRAMES, 'influence': 0}]}}
    register_event(job, 'actor_left', event['event_id'], event)
    committed = motion_commit(job, 'actor_left', 1, fixture.FRAMES)
    assert committed['ok'], committed
    data = bpy.data.cameras.new('Camera')
    camera = bpy.data.objects.new('Camera', data)
    scene.collection.objects.link(camera)
    camera.location = (3, -6, 2.8)
    camera.rotation_euler = (Vector((0, 0, .9)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
    data.type, data.ortho_scale = 'ORTHO', 4.8
    scene.camera = camera
    camera_requirements(job, {'version': 1, 'shots': [{
        'camera': camera.name, 'frame_start': 1, 'frame_end': fixture.FRAMES,
        'actor_ids': ['actor_left'], 'framing': 'full_body', 'fixed_transform': True,
        'expected_projection': 'orthographic', 'margin': .02}]})
    return job, folder, prop


def run_acceptance(jobs):
    import bpy
    from agent_scene import submit_scene
    job, folder, prop = build_fixture(jobs)
    analysis = {'actor_count': 1, 'actors': ['actor_left'],
                'intent': '原 Beta 蒙皮点接触移动目标；24 帧程序动作'}
    submit_scene(job, analysis)
    accepted = fresh_probe(folder, jobs)
    assert accepted['passed'], accepted['errors']
    contact = accepted['registered_contacts']['contacts'][0]
    assert contact['coverage'] == 'full' and contact['tested_frames'] == 16
    assert contact['effector_scope'] == 'evaluated_actor_skin_vertex'

    # Move the target after baking the actor, then save the genuinely changed
    # scene. The normal reload path must detect its lost contact.
    scene = bpy.context.scene
    scene.frame_set(12)
    prop.location.x += .25
    prop.keyframe_insert('location', frame=12)
    fixture.linear_keys(prop)
    scene.frame_set(1)
    submit_scene(job, {**analysis, 'revision': 'target moved after contact bake'})
    broken = fresh_probe(folder, jobs)
    assert not broken['passed'], broken
    assert {error['code'] for error in broken['errors']} == {'contacts'}, broken['errors']
    failures = [failure for error in broken['errors'] for failure in error.get('failures', [])]
    assert any(failure.get('check') == 'contact_distance' and failure.get('frame') == 12
               and failure.get('distance_m', 0) > .02 for failure in failures), failures
    return {'passed': True, 'contact_max_distance_m': contact['max_distance_m'],
            'moved_target_detected': True, 'scene_frames': accepted['tested_frames']}


def main():
    import bpy
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe-manifest')
    parser.add_argument('--jobs-root')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    if not bpy.app.background:
        raise RuntimeError('Run this test in background Blender')
    if args.probe_manifest:
        fixture.configure_jobs(args.jobs_root)
        from agent_scene_probe import probe_scene
        result = probe_scene(args.probe_manifest)
        print('CONTACT_PROBE_RESULT ' + json.dumps({key: result[key] for key in
              ('passed', 'errors', 'tested_frames', 'registered_contacts')}, ensure_ascii=False), flush=True)
        return
    with tempfile.TemporaryDirectory(prefix='bamo-contact-probe-') as temporary:
        result = run_acceptance(Path(temporary) / 'jobs')
    print('CONTACT_PROBE_ACCEPTANCE ' + json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
