"""Actual original-Beta snapshot, independent process render, and reload proof."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import agent_preview as preview
from agent_character import actor_ensure, resolve_actor, scene_actor_facts


def main():
    assert bpy.app.background, 'Never run this test in the shared GUI'
    job_id = 'preview_beta_' + uuid.uuid4().hex[:16]
    job = ROOT / 'output/jobs' / job_id
    job.mkdir()
    (job / 'request.json').write_text(json.dumps({'prompt': 'isolated original Beta asynchronous preview validation'}))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    actor_ensure(job_id, 'A', position=(.2, -.3, 0), yaw=.4, color=(.75, .35, .15, 1))
    actor = resolve_actor(job_id, 'A')
    assert sum(len(obj.data.vertices) for obj in actor['meshes']) == 24746
    material = actor['meshes'][0].active_material
    material.use_nodes = True
    roughness = material.node_tree.nodes.get('Principled BSDF').inputs['Roughness']
    roughness.default_value = .731
    camera = bpy.data.objects.new('PreviewOriginalCamera', bpy.data.cameras.new('PreviewOriginalCamera'))
    scene.collection.objects.link(camera)
    camera.location = (3, -6, 3)
    camera.rotation_euler = (Vector((.2, -.3, .85)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 2.9
    scene.camera = camera
    bpy.ops.mesh.primitive_plane_add(size=200)
    bpy.context.object.name = 'OriginalGround'
    ground = bpy.data.materials.new('OriginalGroundMaterial')
    ground.diffuse_color = (.035, .06, .095, 1)
    bpy.context.object.data.materials.append(ground)
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_shadows = True
    scene.display.render_aa = 'FXAA'
    scene.render.fps, scene.render.fps_base = 24, 1.001
    scene.frame_start, scene.frame_end = 1, 3
    scene.frame_set(2)
    scene.render.resolution_x, scene.render.resolution_y = 800, 450
    scene.render.resolution_percentage = 75
    scene.render.filepath = '//original_unchanged.png'
    original = job / 'original.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(original), check_existing=False)
    before = {'filepath': bpy.data.filepath, 'frame': scene.frame_current,
              'render_filepath': scene.render.filepath, 'summary': preview._summary(scene),
              'roughness': roughness.default_value,
              'root': [list(row) for row in actor['root'].matrix_world]}
    started = time.monotonic()
    result = preview.preview_start(job_id, frames=[1, 3], resolution=[320, 180])
    returned_elapsed = time.monotonic() - started
    assert result['ok'] and result['preview_id']
    state_at_return = preview.preview_status(job_id, result['preview_id'])
    assert state_at_return['status'] != 'complete', 'Start must return before actual child rendering'
    assert state_at_return['process']['pid'] != os.getpid()
    assert bpy.data.filepath == before['filepath']
    assert scene.frame_current == before['frame']
    assert scene.render.filepath == before['render_filepath']
    assert preview._summary(scene) == before['summary']
    assert roughness.default_value == before['roughness']
    assert [list(row) for row in actor['root'].matrix_world] == before['root']
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        state = preview.preview_status(job_id, result['preview_id'])
        if state['status'] in ('complete', 'failed'):
            break
        time.sleep(.15)
    assert state['ok'] and state['status'] == 'complete', state
    assert state['completed_frames'] == 2
    assert all(item['available'] and item['resolution'] == [320, 180] for item in state['images'])
    assert all(item['render_engine'] == 'BLENDER_WORKBENCH' and item['camera'] == camera.name for item in state['images'])
    assert bpy.data.filepath == before['filepath'] and scene.frame_current == before['frame']
    assert scene.render.filepath == before['render_filepath'] and preview._summary(scene) == before['summary']
    preview._RUNNING.clear()
    recovered = preview.preview_status(job_id, result['preview_id'])
    assert recovered['ok'] and recovered['process_exit_code'] == 0
    snapshot = job / 'agent_previews' / result['preview_id'] / 'snapshot.blend'
    bpy.ops.wm.open_mainfile(filepath=str(snapshot), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    assert preview._summary(scene) == before['summary']
    actual = scene_actor_facts(job_id)
    assert not actual['errors'] and len(actual['actors']) == 1, actual
    actor = resolve_actor(job_id, 'A')
    shader = actor['meshes'][0].active_material.node_tree.nodes['Principled BSDF']
    assert shader.inputs['Roughness'].default_value == before['roughness']
    assert [list(row) for row in actor['root'].matrix_world] == before['root']
    report = {'passed': True, 'job_id': job_id, 'source_sha256': preview._sha(Path(preview.__file__)),
              'test_sha256': preview._sha(Path(__file__)), 'request_sha256': preview._sha(job / 'request.json'),
              'parent_pid': os.getpid(), 'child_pid': state_at_return['process']['pid'],
              'start_return_elapsed_seconds': returned_elapsed, 'state_at_return': state_at_return['status'],
              'render_completed_elapsed_seconds': time.monotonic() - started,
              'snapshot': str(snapshot), 'snapshot_sha256': preview._sha(snapshot),
              'original_scene': str(original), 'original_scene_sha256': preview._sha(original),
              'actual_skin_vertices': 24746, 'identity_reload': actual,
              'original_engine': before['summary']['render_engine'], 'original_summary': before['summary'],
              'actual_preview_images': state['images'],
              'checks': ['nonblocking_copy_and_spawn', 'parent_filepath_scene_frame_and_render_path_unchanged',
                         'real_original_skin_and_binding_reload', 'original_shader_camera_engine_fps_preserved',
                         'requested_pngs_saved_by_independent_child', 'durable_status_after_process_cache_loss'],
              'scope': 'Independent original-Beta Workbench snapshot and render proof. No automatic engine downgrade; no naturalness certification.'}
    output = ROOT / 'reports/agent_preview_validation_20261014.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'passed': True, 'job_id': job_id, 'report': str(output), 'preview_id': result['preview_id'],
                      'start_return_elapsed_seconds': returned_elapsed}, ensure_ascii=False))


if __name__ == '__main__':
    main()
