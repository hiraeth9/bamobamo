"""Create small real scenes for independent agent-scene reload/media checks.

Run in Blender with --report /.../fixture_report.json.  This does not touch
old generated jobs or the locomotion, door, and vault regression artifacts.
"""
import argparse
import json
from pathlib import Path
import sys
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))


def main():
    import bpy
    import agent_scene
    import duel_plan
    import preload_helpers

    parser = argparse.ArgumentParser()
    parser.add_argument('--report', required=True)
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    args = parser.parse_args(argv)
    suffix = uuid.uuid4().hex[:8]
    cases = []
    handlers = []
    for kind in ('fractional_fps', 'invalid_driver', 'unbaked_handler', 'missing_camera'):
        for handler in handlers:
            if handler in bpy.app.handlers.frame_change_post:
                bpy.app.handlers.frame_change_post.remove(handler)
        handlers.clear()
        bpy.ops.wm.read_factory_settings(use_empty=False)
        job_id = 'agent_scene_' + kind + '_20261013_' + suffix
        folder = duel_plan.job_directory(job_id)
        folder.mkdir(parents=True, exist_ok=False)
        duel_plan.atomic_json(folder / 'request.json', {'prompt': '任意场景动画与镜头，不依赖动作类别', 'mode': 'agent'})
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end = 11, 18
        scene.frame_step = 2
        scene.render.fps, scene.render.fps_base = 24, 1.001
        scene.render.resolution_x, scene.render.resolution_y = 128, 72
        scene.render.resolution_percentage = 100
        scene.render.engine = 'BLENDER_EEVEE'
        cube = bpy.data.objects['Cube']
        cube.location.x = 0
        cube.keyframe_insert(data_path='location', index=0, frame=11)
        cube.location.x = .35
        cube.keyframe_insert(data_path='location', index=0, frame=18)
        if kind == 'invalid_driver':
            cube.animation_data_clear()
            bpy.app.driver_namespace['agent_probe_ephemeral'] = lambda: .23
            cube.driver_add('location', 0).driver.expression = 'agent_probe_ephemeral()'
        elif kind == 'unbaked_handler':
            def move_on_frame(current, *unused):
                current.objects['Cube'].location.y = current.frame_current * .04
            bpy.app.handlers.frame_change_post.append(move_on_frame)
            handlers.append(move_on_frame)
        elif kind == 'missing_camera':
            camera = scene.camera
            scene.camera = None
            bpy.data.objects.remove(camera, do_unlink=True)
        else:
            texture_path = folder / 'packed_texture.png'
            image = bpy.data.images.new('AgentTexture', width=2, height=2)
            image.pixels[:] = [.8, .2, .1, 1.] * 4
            image.filepath_raw, image.file_format = str(texture_path), 'PNG'
            image.save()
            bpy.data.images.remove(image)
            image = bpy.data.images.load(str(texture_path))
            material = bpy.data.materials.new('FreeMaterial')
            material.use_nodes = True
            texture = material.node_tree.nodes.new('ShaderNodeTexImage')
            texture.image = image
            material.node_tree.links.new(texture.outputs['Color'], material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
            cube.data.materials.clear()
            cube.data.materials.append(material)
        analysis = {'actor_count': 71, 'anything_not_in_old_schema': {'actions': ['free animation'],
                                                                    'purpose': 'metadata only'}}
        assert bpy.wm_agent_analyze(job_id, analysis)['ok']
        before = bpy.data.filepath
        result = bpy.wm_agent_scene(job_id)
        assert result['ok'], result
        assert bpy.data.filepath == before, 'Save-copy changed GUI filepath'
        manifest = json.loads((folder / 'agent_scene.json').read_text())
        assert manifest['frame_start'] == 11 and manifest['frame_count'] == 8
        assert manifest['actor_count'] == 71 and manifest['armature_count'] == 0
        assert manifest['fps_base'] > 1 and manifest['fps_nominal'] == 24
        assert 'passed' not in manifest and 'quality_passed' not in manifest
        archive_checked = False
        if kind == 'fractional_fps':
            texture_path.unlink()
            assert any(resource['kind'] == 'images' and resource['packed'] for resource in manifest['resources'])
            old_hash = manifest['scene_sha256']
            cube.scale = (.92, .92, .92)
            second = bpy.wm_agent_scene(job_id, {**analysis, 'revision': 'same scene, different scale'})
            assert second['ok'], second
            archive = folder / second['archive']
            assert agent_scene.sha256_file(archive / 'scene.blend') == old_hash
            assert agent_scene.sha256_file(folder / 'scene.blend') != old_hash
            assert (archive / 'agent_analysis.json').is_file() and (archive / 'agent_scene.json').is_file()
            archive_checked = True
        cases.append({'name': kind, 'job_id': job_id, 'manifest': str(folder / 'agent_scene.json'),
                      'folder': str(folder), 'expected_passed': kind == 'fractional_fps',
                      'gui_filepath_preserved': True, 'archive_checked': archive_checked})
    report = {'version': 1, 'stage': 'saved_fixtures_created', 'cases': cases,
              'helpers_registered': all(hasattr(bpy, name) for name in (
                  'wm_agent_analyze', 'wm_agent_scene', 'wm_agent_reference_info',
                  'wm_agent_build', 'wm_agent_build_status', 'wm_agent_load')),
              'source_sha256': {name: agent_scene.sha256_file(PROJECT / 'scripts' / name)
                                for name in ('agent_scene.py', 'agent_scene_probe.py',
                                             'render_saved_scene.py', 'preload_helpers.py')}}
    duel_plan.atomic_json(args.report, report)
    print('AGENT_FIXTURES_CREATED ' + args.report, flush=True)


if __name__ == '__main__':
    main()
