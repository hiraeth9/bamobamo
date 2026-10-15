"""Original-skin, independent instances, adoption and true reload/render proof.

Run in an isolated headless process, never the shared GUI scene.
"""
import json
import math
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import duel_plan
from agent_character import (actor_ensure, actor_get, actor_adopt, resolve_actor,
                             scene_actor_facts, rig_facts, binding_facts)


def evaluated_vertices(actor):
    graph = bpy.context.evaluated_depsgraph_get()
    result = []
    for obj in actor['meshes']:
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        try:
            result.extend(tuple(evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices)
        finally:
            evaluated.to_mesh_clear()
    return result


def maximum_error(first, second):
    return max((Vector(a)-Vector(b)).length for a,b in zip(first,second))


def assert_raises(call, fragment=None):
    try:
        call()
    except ValueError as exc:
        if fragment and fragment not in str(exc):
            raise AssertionError(str(exc))
        return str(exc)
    raise AssertionError('Expected actual validation failure')


def render_scene(folder):
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 32
    scene.cycles.use_denoising = True
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 4
    scene.render.resolution_x, scene.render.resolution_y = 960, 540
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.world = bpy.data.worlds.new('IdentityWorld')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value = (.08,.1,.12,1)
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value = .25
    for name, position, energy in [('Key',(-3,-5,7),800), ('Fill',(4,-2,5),500), ('Rim',(0,4,6),700)]:
        light = bpy.data.lights.new(name, 'AREA')
        light.energy, light.shape, light.size = energy, 'DISK', 5
        obj = bpy.data.objects.new(name, light)
        scene.collection.objects.link(obj)
        obj.location = position
        obj.rotation_euler = (Vector((0,0,1))-obj.location).to_track_quat('-Z','Y').to_euler()
    camera = bpy.data.objects.new('IdentityCamera', bpy.data.cameras.new('IdentityCamera'))
    scene.collection.objects.link(camera)
    camera.location = (5,-11,4)
    camera.rotation_euler = (Vector((0,0,.9))-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 8
    scene.camera = camera
    bpy.ops.mesh.primitive_plane_add(size=200)
    bpy.context.object.name = 'Ground'
    ground = bpy.data.materials.new('DarkGround')
    ground.diffuse_color = (.06,.08,.1,1)
    ground.use_nodes = True
    ground.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (.06,.08,.1,1)
    ground.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value = .9
    bpy.context.object.data.materials.append(ground)
    scene.render.filepath = str(folder / 'three_original_robots.png')
    bpy.ops.render.render(write_still=True)


def main():
    if not bpy.app.background:
        raise RuntimeError('This validation must run in isolated headless Blender')
    output = ROOT / 'reports/robot_character_20261014'
    output.mkdir(exist_ok=True)
    checks = []
    with tempfile.TemporaryDirectory(prefix='bamo_character_jobs_') as temporary:
        duel_plan.JOBS = Path(temporary)
        job = 'robot_character_test'
        (duel_plan.JOBS / job).mkdir()
        (duel_plan.JOBS / job / 'request.json').write_text(json.dumps({'prompt':'three original robots'}))
        other = 'other_task'
        (duel_plan.JOBS / other).mkdir()
        (duel_plan.JOBS / other / 'request.json').write_text(json.dumps({'prompt':'different scene'}))
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.render.fps, scene.render.fps_base = 24, 1.001
        scene.frame_set(11)
        colors = ((.65,.68,.72,1),(.6,.6,.62,1),(.72,.74,.76,1))
        for actor_id, x, yaw, color in zip(('A','B','C'),(-2,0,2),(.4,.6,-.7),colors):
            actor_ensure(job, actor_id, position=(x,0,0),yaw=yaw,color=color)
        actors = {name: resolve_actor(job,name) for name in ('A','B','C')}
        assert scene.render.fps == 24 and abs(scene.render.fps_base-1.001)<1e-6 and scene.frame_current == 11
        assert len({a['armature'].data.as_pointer() for a in actors.values()}) == 3
        assert len({m.data.as_pointer() for a in actors.values() for m in a['meshes']}) == 6
        assert all(len(a['meshes']) == 2 for a in actors.values())
        assert all(sum(len(m.data.vertices) for m in a['meshes']) == 24746 for a in actors.values())
        checks.append({'name':'three_original_bound_independent_skins','passed':True,'vertices_per_actor':24746})
        original_count = len(scene.objects)
        original_root = actors['A']['root'].location.copy()
        same = actor_ensure(job,'A',position=(99,0,0))
        assert len(scene.objects) == original_count and (actors['A']['root'].location-original_root).length == 0
        checks.append({'name':'idempotency_preserves_existing_motion_and_position','passed':True})
        expected = {name:evaluated_vertices(actor) for name,actor in actors.items()}
        hand = actors['A']['armature'].pose.bones['mixamorig:RightHand']
        head = actors['A']['armature'].pose.bones['mixamorig:Head']
        assert hand.head.z < head.head.z - 30  # retained actual Idle, centimetre rig space
        checks.append({'name':'default_pose_actual_idle_not_rest_tpose','passed':True})
        rig = actors['A']['armature']
        bone = rig.pose.bones['mixamorig:RightArm']
        bone.rotation_mode = 'QUATERNION'
        bone.rotation_quaternion = Vector((0,1,0)).to_track_quat('Y','Z')
        bone.keyframe_insert('rotation_quaternion',frame=11)
        actors['A']['root'].location.x -= .3
        actors['A']['root'].keyframe_insert('location',frame=11)
        bpy.context.view_layer.update()
        after = {name:evaluated_vertices(actor) for name,actor in actors.items()}
        untouched = max(maximum_error(expected[name],after[name]) for name in ('B','C'))
        assert untouched < 1e-7 and maximum_error(expected['A'],after['A']) > .25
        assert actors['B']['armature'].animation_data is None
        checks.append({'name':'A_fk_and_root_do_not_move_B_C','passed':True,'untouched_error_m':untouched})
        modifier = next(m for m in actors['A']['meshes'][0].modifiers if m.type == 'ARMATURE')
        modifier.object = actors['B']['armature']
        failure = assert_raises(lambda:actor_get(job,'A'),'蒙皮')
        modifier.object = actors['A']['armature']
        checks.append({'name':'cross_actor_modifier_rejected','passed':True,'diagnostic':failure})
        mesh = actors['A']['meshes'][0]
        inverse = mesh.matrix_parent_inverse.copy()
        mesh.matrix_parent_inverse.translation.x += 12
        bpy.context.view_layer.update()
        assert_raises(lambda:actor_get(job,'A'),'蒙皮')
        mesh.matrix_parent_inverse = inverse
        bpy.context.view_layer.update()
        checks.append({'name':'wrong_parent_inverse_bind_transform_rejected','passed':True})
        assert_raises(lambda:actor_ensure(other,'D'),'属于任务')
        checks.append({'name':'foreign_job_rejected','passed':True})
        facts = scene_actor_facts(job)
        assert not facts['errors'] and facts['rendered_actor_count'] == 3
        for actor in actors.values():
            for mesh in actor['meshes']:
                mesh.hide_render = True
        hidden_facts = scene_actor_facts(job)
        assert not hidden_facts['errors'] and hidden_facts['visibility_review_required']
        for actor in actors.values():
            for mesh in actor['meshes']:
                mesh.hide_render = False
        checks.append({'name':'temporarily_hidden_actor_identity_not_confused_with_visibility','passed':True})
        bpy.ops.object.armature_add()
        proxy = bpy.context.object
        bpy.ops.mesh.primitive_cube_add()
        proxy_mesh = bpy.context.object
        proxy_mesh.parent = proxy
        assert any('未通过标准' in item for item in scene_actor_facts(job)['errors'])
        proxy_mesh.hide_render = True
        assert not scene_actor_facts(job)['errors']
        bpy.data.objects.remove(proxy_mesh,do_unlink=True)
        bpy.data.objects.remove(proxy,do_unlink=True)
        checks.append({'name':'unregistered_rendered_proxy_rejected_hidden_source_ignored','passed':True})
        # Save real animated skin, independently reopen, revalidate data and geometry.
        before_reload = {name:evaluated_vertices(actor) for name,actor in actors.items()}
        render_scene(output)
        blend = output / 'three_original_robots.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(blend),check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(blend),use_scripts=False)
        after_reload = {name:evaluated_vertices(resolve_actor(job,name)) for name in ('A','B','C')}
        error = max(maximum_error(before_reload[name],after_reload[name]) for name in ('A','B','C'))
        assert error < 1e-5 and not scene_actor_facts(job)['errors']
        checks.append({'name':'saved_scene_reload_original_skin_identity_and_pose','passed':True,'maximum_error_m':error})
        # A fresh recipe-style Mma Idle is accepted by its real rest/data signature.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(ROOT/'mixamo_fbx/combat_melee/Mma Idle.fbx'))
        armature = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
        source = {'armature':armature,'meshes':meshes}
        before = evaluated_vertices(source)
        adopted = actor_adopt(job,'Recipe',armature.name)
        bpy.context.view_layer.update()
        after = evaluated_vertices(resolve_actor(job,'Recipe'))
        adoption_error = maximum_error(before,after)
        assert adoption_error < 1e-7 and adopted['adopted'] and not scene_actor_facts(job)['errors']
        checks.append({'name':'mma_idle_real_skin_adoption_preserves_animation_and_transform',
                       'passed':True,'maximum_error_m':adoption_error})
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_cube_add()
        assert scene_actor_facts(job,allow_empty=True)['status'] == 'not_applicable'
        assert scene_actor_facts(job)['errors']
        checks.append({'name':'object_only_scene_has_no_forced_actor','passed':True})
    report = {'passed':True,'checks':checks,'scope':'Original Beta geometry, binding, task ownership, '
              'independent instances, headless rendered appearance and scene reload. No naturalness claim.',
              'render':str((output/'three_original_robots.png').relative_to(ROOT)),
              'scene':str((output/'three_original_robots.blend').relative_to(ROOT))}
    (output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('CHARACTER_VALIDATION',json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
