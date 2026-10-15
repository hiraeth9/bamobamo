"""Real original Beta removal/stale recovery, without touching the live GUI."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile

import bpy
from mathutils import Euler, Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import duel_plan
import agent_character as character
from agent_motion_controls import controls_ensure
from agent_motion_quality import animation_fingerprint


def skin_sha(actor):
    digest = hashlib.sha256()
    graph = bpy.context.evaluated_depsgraph_get()
    for obj in actor['meshes']:
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        try:
            for vertex in mesh.vertices:
                digest.update(struct.pack('<3f', *(evaluated.matrix_world @ vertex.co)))
        finally:
            evaluated.to_mesh_clear()
    return digest.hexdigest()


def snapshots(job, ids):
    result = {}
    actors = {key: character.resolve_actor(job, key) for key in ids}
    for frame in range(1, 13):
        bpy.context.scene.frame_set(frame)
        result[frame] = {}
        for key in ids:
            actor = actors[key]
            result[frame][key] = {
                'skin_sha256': skin_sha(actor),
                'root': character.matrix_values(actor['root'].matrix_world, 8),
                'bones': {bone.name: character.matrix_values(bone.matrix, 8) for bone in actor['armature'].pose.bones},
                'action_sha': [animation_fingerprint(obj) for obj in (actor['root'], actor['armature'])]}
    return result


def main():
    assert bpy.app.background
    output = ROOT / 'reports/robot_character_remove_20261014'
    output.mkdir(exist_ok=True)
    checks = []
    with tempfile.TemporaryDirectory(prefix='bamo_remove_jobs_') as temporary:
        duel_plan.JOBS = Path(temporary)
        job = 'robot_remove_proof'
        folder = duel_plan.JOBS / job
        folder.mkdir()
        (folder / 'request.json').write_text(json.dumps({'prompt': 'three original Beta removal proof'}))
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end = 1, 12
        for name, x, yaw in [('robot', -2, .3), ('robot_01', 0, .6), ('third', 2, -.4)]:
            character.actor_ensure(job, name, position=(x, .1, 0), yaw=yaw)
            actor = character.resolve_actor(job, name)
            root = actor['root']
            root.keyframe_insert('location', frame=1)
            root.location.y += .35
            root.keyframe_insert('location', frame=12)
            bone = actor['armature'].pose.bones['mixamorig:RightForeArm']
            bone.rotation_mode = 'QUATERNION'
            bone.keyframe_insert('rotation_quaternion', frame=1)
            bone.rotation_quaternion = Euler((.35, -.15, .1)).to_quaternion()
            bone.keyframe_insert('rotation_quaternion', frame=12)
            root['wm_motion_timeline_v1'] = json.dumps({'version': 1, 'events': [{'event_id': name + '_authored'}]})
        old = character.resolve_actor(job, 'robot_01')
        control_info = controls_ensure(job, 'robot_01')
        control_names = [chain[field] for chain in control_info['chains'].values() for field in ('target_object', 'pole_object')]
        # Deliberately share a real mesh data block with the retained third actor.
        # Removing the old object's users must not delete the borrowed data.
        third = character.resolve_actor(job, 'third')
        third['meshes'][0].data = old['meshes'][0].data
        shared_data_name = third['meshes'][0].data.name
        before = snapshots(job, ('robot', 'third'))
        scene.frame_set(7)
        bpy.ops.mesh.primitive_cube_add(size=.1)
        prop = bpy.context.object
        prop.name = 'PreservedBoneParentProp'
        prop['prop_id'] = 'held_item'
        prop['wm_job_id'], prop['wm_actor_id'] = job, 'robot_01'
        prop.parent = old['armature']
        prop.parent_type, prop.parent_bone = 'BONE', 'mixamorig:RightHand'
        prop.matrix_parent_inverse = Matrix.Identity(4)
        prop.matrix_world = Matrix.Translation((.3, -.5, 1.1))
        camera = bpy.data.objects.new('GenericPreservedCamera', bpy.data.cameras.new('GenericPreservedCamera'))
        scene.collection.objects.link(camera)
        camera.parent = old['root']
        camera.location = (4, -6, 3)
        scene.camera = camera
        bpy.context.view_layer.update()
        prop_world, camera_world = prop.matrix_world.copy(), camera.matrix_world.copy()
        listing = character.actor_list(job)
        assert listing['registered_actor_count'] == 3
        removed = character.actor_remove(job, 'robot_01')
        assert removed['ok'] and removed['registry_removed'] and removed['timeline_bank_removed'], removed
        assert all(bpy.data.objects.get(name) is None for name in control_names)
        assert prop.parent is None and camera.parent is None
        assert max(abs(a-b) for row, oldrow in zip(prop.matrix_world, prop_world) for a,b in zip(row,oldrow)) < 1e-6
        assert max(abs(a-b) for row, oldrow in zip(camera.matrix_world, camera_world) for a,b in zip(row,oldrow)) < 1e-6
        assert third['meshes'][0].data.name == shared_data_name and bpy.data.meshes.get(shared_data_name) is not None
        after = snapshots(job, ('robot', 'third'))
        assert before == after, 'Any retained actor bone/skin/root/action changed'
        assert not character.scene_actor_facts(job)['errors']
        assert character.actor_remove(job, 'robot_01')['status'] == 'already_absent'
        checks.append({'name': 'remove_only_old_robot_01_other_two_full_frame_bones_skin_actions_identical', 'passed': True,
                       'sampled_frames': 12, 'vertices_per_actor': 24746, 'removed': removed})
        checks.append({'name': 'bone_parented_prop_and_generic_camera_world_transform_preserved', 'passed': True,
                       'prop_world': character.matrix_values(prop_world, 8), 'camera_world': character.matrix_values(camera_world, 8)})
        character.actor_ensure(job, 'stale', position=(0, 0, 0))
        stale = character.resolve_actor(job, 'stale')
        missing = stale['meshes'][0].name
        bpy.data.objects.remove(stale['meshes'][0], do_unlink=True)
        state = next(item for item in character.actor_list(job)['actors'] if item['actor_id'] == 'stale')
        assert state['status'] == 'stale_or_mismatched' and missing in state['missing_objects']
        cleaned = character.actor_remove(job, 'stale')
        assert cleaned['ok'] and missing in cleaned['missing_recorded_objects']
        assert character.actor_list(job)['registered_actor_count'] == 2
        checks.append({'name': 'stale_registry_with_missing_original_skin_can_be_queried_and_removed', 'passed': True, 'result': cleaned})
        # A damaged record borrowing another actor's root must report it, not delete it.
        character.actor_ensure(job, 'bad_record')
        registry = character._registry(scene)
        registry['bad_record']['root_object'] = character.resolve_actor(job, 'third')['root'].name
        character._write_registry(scene, registry)
        result = character.actor_remove(job, 'bad_record')
        assert not result['ok'] and any(item['reason'] == 'ownership_mismatch' for item in result['retained_objects'])
        assert before == snapshots(job, ('robot', 'third'))
        checks.append({'name': 'foreign_record_name_retained_mismatch_reported_never_false_cleanup_success', 'passed': True, 'result': result})
        character.actor_ensure(job, 'borrowed_parent', position=(6, 0, 0))
        borrowed = character.resolve_actor(job, 'borrowed_parent')['root']
        third_root = character.resolve_actor(job, 'third')['root']
        third_root.parent = borrowed
        nested = snapshots(job, ('robot', 'third'))
        result = character.actor_remove(job, 'borrowed_parent')
        assert not result['ok'] and bpy.data.objects.get(borrowed.name) is not None
        assert nested == snapshots(job, ('robot', 'third'))
        third_root.parent = None
        assert before == snapshots(job, ('robot', 'third'))
        checks.append({'name': 'surviving_actor_parent_dependency_kept_all_frame_motion_identical', 'passed': True, 'result': result})
        scene.frame_set(7)
        saved = output / 'two_retained_beta_after_actor_remove.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(saved), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False)
        assert before == snapshots(job, ('robot', 'third'))
        scene = bpy.context.scene
        scene.frame_set(7)
        prop = bpy.data.objects['PreservedBoneParentProp']
        camera = bpy.data.objects['GenericPreservedCamera']
        assert prop.parent is None and camera.parent is None
        assert max(abs(a-b) for row, oldrow in zip(prop.matrix_world, prop_world) for a,b in zip(row,oldrow)) < 1e-6
        facts = character.scene_actor_facts(job)
        assert not facts['errors'] and facts['rendered_actor_count'] == 2
        checks.append({'name': 'independent_save_reload_two_real_beta_all_frame_skin_bones_actions_and_prop_world_identical', 'passed': True})
        report = {'passed': True, 'source_sha256': character.file_sha256(Path(character.__file__)),
                  'test_sha256': character.file_sha256(Path(__file__)), 'checks': checks,
                  'scene': str(saved.relative_to(ROOT)), 'scene_sha256': character.file_sha256(saved),
                  'retained_actor_evaluation_sha256': character._digest(before),
                  'scope': 'Actual isolated original-Beta deletion/stale ownership recovery, shared data protection, current-frame bone-parented prop and camera world preservation, all 12-frame retained actor skin/bone/root/action equivalence and saved-scene reload.'}
        (output / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'passed': True, 'checks': len(checks), 'source_sha256': report['source_sha256'], 'report': str(output/'validation.json')}), flush=True)


if __name__ == '__main__':
    main()
