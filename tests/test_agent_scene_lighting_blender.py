"""Real EEVEE diffuse-object regression; run with Blender --python this_file.

Only a new in-memory fixture and --output images are created. No live services,
tasks, assets, model calls or existing scenes are edited.
"""
import argparse
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))


def fixture():
    import bpy
    from mathutils import Vector
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x = scene.render.resolution_y = 128
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    world = bpy.data.worlds.new('NonblackWorld')
    world.use_nodes = True
    world.node_tree.nodes['Background'].inputs[0].default_value = (.05, .05, .05, 1.)
    world.node_tree.nodes['Background'].inputs[1].default_value = 1.
    scene.world = world
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, radius=.7, location=(0, 0, .8))
    body = bpy.context.object
    material = bpy.data.materials.new('DiffuseWhite')
    material.use_nodes = True
    shader = material.node_tree.nodes['Principled BSDF']
    shader.inputs['Base Color'].default_value = (.7, .7, .7, 1.)
    shader.inputs['Roughness'].default_value = 1.
    shader.inputs['Specular IOR Level'].default_value = 0.
    body.data.materials.append(material)
    camera = bpy.data.objects.new('LightingCamera', bpy.data.cameras.new('LightingCamera'))
    scene.collection.objects.link(camera)
    camera.location = (3, -5, 2.7)
    camera.rotation_euler = (Vector((0, 0, .8))-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type, camera.data.ortho_scale = 'ORTHO', 2.1
    scene.camera = camera
    return scene


def body_brightness(scene, output):
    import bpy
    import numpy as np
    scene.render.filepath = str(output)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(output), check_existing=False)
    pixels = np.array(image.pixels[:], dtype=float).reshape((-1, 4))
    body = pixels[pixels[:, 3] > .99, :3]
    assert len(body) > 2000, 'Fixture must actually show a diffuse object'
    return float(body.mean())


def main():
    import bpy
    from agent_scene import illumination_facts, ensure_scene_lighting
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    args.output.mkdir(parents=True, exist_ok=True)
    scene = fixture()
    initial_world = tuple(scene.world.node_tree.nodes['Background'].inputs[0].default_value)
    assert not illumination_facts(scene)['illuminated'], 'The default nonblack EEVEE World must not skip fallback lighting'
    before = body_brightness(scene, args.output/'eevee_before.png')
    applied = ensure_scene_lighting(scene)
    assert applied['applied'] and len(applied['created']) == 3, applied
    assert illumination_facts(scene)['render_enabled_light_count'] == 3
    assert tuple(scene.world.node_tree.nodes['Background'].inputs[0].default_value) == initial_world
    after = body_brightness(scene, args.output/'eevee_after.png')
    assert before < .4 and after > before+.1 and after > .4, (before, after)
    lights = tuple(obj.name for obj in scene.objects if obj.type == 'LIGHT')
    assert ensure_scene_lighting(scene) == {'applied':False, 'reason':'already_lit', 'created':[]}
    assert lights == tuple(obj.name for obj in scene.objects if obj.type == 'LIGHT')
    # These engines legitimately use World/display illumination without LIGHTs.
    scene = fixture()
    scene.render.engine = 'CYCLES'
    assert illumination_facts(scene)['illuminated']
    assert not ensure_scene_lighting(scene)['applied']
    scene.render.engine = 'BLENDER_WORKBENCH'
    assert illumination_facts(scene)['illuminated']
    assert ensure_scene_lighting(scene)['reason'] == 'engine_uses_display_lighting'
    print('LIGHTING_REGRESSION_PASS', {'eevee_body_mean_before':before, 'eevee_body_mean_after':after,
          'existing_lights_preserved':True, 'cycles_world_preserved':True, 'workbench_preserved':True,
          'images':str(args.output)}, flush=True)


if __name__ == '__main__':
    main()
