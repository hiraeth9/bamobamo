"""Read-only historical recipe copies -> adopted Beta -> captured -> new FK.

Checks all frames, all bones and every evaluated skin vertex.  Historical files
are hashed before/after and never saved.  Only new report-directory artifacts
and a temporary job registry are written.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    import bpy
    import numpy as np
    from mathutils import Quaternion
    import duel_plan
    from agent_character import actor_adopt, resolve_actor, binding_facts
    from agent_motion_controls import read_pose
    from agent_motion_timeline import motion_commit, register_event

    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default=str(PROJECT / 'reports/robot_recipe_adapters_20261014.json'))
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    report_path = Path(args.report).resolve()
    artifact_dir = report_path.with_suffix('')
    artifact_dir.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix='bamo_recipe_adapters_')
    duel_plan.JOBS = Path(temporary.name)
    cases = []

    def snapshot(actors):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        result = {}
        for actor_id, actor in actors.items():
            arm = actor['armature'].evaluated_get(depsgraph)
            bones = np.asarray([list(row) for bone in arm.pose.bones
                                for row in arm.matrix_world @ bone.matrix], dtype=np.float64)
            root = np.asarray(actor['root'].evaluated_get(depsgraph).matrix_world, dtype=np.float64)
            meshes = {}
            for obj in actor['meshes']:
                evaluated = obj.evaluated_get(depsgraph)
                mesh = evaluated.to_mesh()
                try:
                    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
                    mesh.vertices.foreach_get('co', coords)
                    matrix = np.asarray(evaluated.matrix_world, dtype=np.float64)
                    meshes[obj.name] = coords.reshape((-1, 3)) @ matrix[:3, :3].T + matrix[:3, 3]
                finally:
                    evaluated.to_mesh_clear()
            result[actor_id] = {'bones': bones, 'root': root, 'meshes': meshes}
        return result

    def error(a, b):
        maxima = {'bone_matrix_max': 0., 'root_matrix_max': 0., 'skin_world_vertex_max_m': 0.}
        for actor_id, expected in a.items():
            actual = b[actor_id]
            maxima['bone_matrix_max'] = max(maxima['bone_matrix_max'], float(np.max(np.abs(expected['bones'] - actual['bones']))))
            maxima['root_matrix_max'] = max(maxima['root_matrix_max'], float(np.max(np.abs(expected['root'] - actual['root']))))
            for name, vertices in expected['meshes'].items():
                maxima['skin_world_vertex_max_m'] = max(maxima['skin_world_vertex_max_m'], float(np.max(np.abs(vertices - actual['meshes'][name]))))
        return maxima

    def compare(reference, actors):
        maximum = {'bone_matrix_max': 0., 'root_matrix_max': 0., 'skin_world_vertex_max_m': 0.}
        for frame, expected in reference.items():
            bpy.context.scene.frame_set(frame)
            bpy.context.view_layer.update()
            values = error(expected, snapshot(actors))
            for key, value in values.items():
                maximum[key] = max(maximum[key], value)
        return maximum

    def within(values, tolerance=3e-5):
        return all(value <= tolerance for value in values.values())

    for label, source in (
            ('vault', PROJECT / 'output/jobs/f5afb90d9ff445ea/scene.blend'),
            ('duel', PROJECT / 'output/jobs/motion_v3_punch/scene.blend')):
        original_hash = sha(source)
        copied = Path(temporary.name) / (label + '_source.blend')
        shutil.copyfile(source, copied)
        bpy.ops.wm.open_mainfile(filepath=str(copied), use_scripts=False)
        scene = bpy.context.scene
        job_id = 'recipe_adapter_' + label
        (duel_plan.JOBS / job_id).mkdir()
        (duel_plan.JOBS / job_id / 'request.json').write_text(json.dumps({'prompt': '复用真实Beta原动作后追加右手招手'}), encoding='utf-8')
        # These old scenes predate this registry.  Removing copied metadata is
        # explicitly test-only and does not waive rig/skin identity checks.
        for key in ('wm_actor_job_id', 'wm_actor_registry'):
            if key in scene:
                del scene[key]
        rigs = sorted((obj for obj in scene.objects if obj.type == 'ARMATURE'), key=lambda obj: obj.name)
        actors = {}
        for index, rig in enumerate(rigs):
            actor_id = chr(ord('A') + index)
            actor_adopt(job_id, actor_id, rig.name)
            actors[actor_id] = resolve_actor(job_id, actor_id)
        assert len(actors) == (1 if label == 'vault' else 2), 'Unexpected actual actor rigs'
        start, end = scene.frame_start, scene.frame_end
        retained = {'objects': set(scene.objects.keys()), 'camera': scene.camera.name if scene.camera else None,
                    'cameras': {obj.name for obj in scene.objects if obj.type == 'CAMERA'},
                    'lights': {obj.name for obj in scene.objects if obj.type == 'LIGHT'}}
        identity = {actor_id: binding_facts(actor['meshes'], actor['armature'])['signature'] for actor_id, actor in actors.items()}
        before = {}
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            before[frame] = snapshot(actors)
        print('RECIPE_BASELINE ' + label + ' ' + str(len(before)), flush=True)
        captured = []
        for actor_id in actors:
            captured.append(motion_commit(job_id, actor_id, capture=True, event_id='existing_recipe',
                                           frame_start=start, frame_end=end))
        capture_error = compare(before, actors)
        scene.frame_set(end)
        bpy.context.view_layer.update()
        actor = actors['A']
        pose = read_pose(actor['armature'], evaluated=True)
        from copy import deepcopy
        raised = deepcopy(pose)
        wave = deepcopy(pose)
        # Original local rest axes are used; no helper skeleton or geometry.
        raised['mixamorig:RightArm']['rotation_quaternion'] = list(Quaternion((0, 1, 0), -.9))
        raised['mixamorig:RightForeArm']['rotation_quaternion'] = list(Quaternion((0, 0, 1), -1.25))
        wave = deepcopy(raised)
        wave['mixamorig:RightHand']['rotation_quaternion'] = list(Quaternion((1, 0, 0), .55))
        append_end = end + 45
        register_event(job_id, 'A', 'new_right_hand_wave', {
            'source': 'procedural', 'intent': '原动作完成后右手招手',
            'start_frame': end + 1, 'end_frame': append_end, 'mask': 'right_arm',
            'blend_in_frames': 8, 'blend_out_frames': 8,
            'keyframes': [{'frame': end + 1, 'pose': pose}, {'frame': end + 12, 'pose': raised},
                          {'frame': end + 22, 'pose': wave}, {'frame': end + 30, 'pose': raised},
                          {'frame': append_end, 'pose': pose}],
        })
        scene.frame_end = append_end
        motion_commit(job_id, 'A', frame_start=start, frame_end=append_end)
        append_preserved_error = compare(before, actors)
        scene.frame_set(end)
        initial_hand = actor['armature'].matrix_world @ actor['armature'].pose.bones['mixamorig:RightHand'].head
        scene.frame_set(end + 22)
        changed_hand = actor['armature'].matrix_world @ actor['armature'].pose.bones['mixamorig:RightHand'].head
        hand_displacement = (changed_hand - initial_hand).length
        after = {}
        for frame in range(start, append_end + 1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            after[frame] = snapshot(actors)
        structures_retained = (retained['objects'] == set(scene.objects.keys()) and
                               retained['camera'] == (scene.camera.name if scene.camera else None) and
                               retained['cameras'] == {obj.name for obj in scene.objects if obj.type == 'CAMERA'} and
                               retained['lights'] == {obj.name for obj in scene.objects if obj.type == 'LIGHT'})
        identity_retained = all(binding_facts(a['meshes'], a['armature'])['signature'] == identity[key] for key, a in actors.items())
        destination = artifact_dir / (label + '_captured_then_wave.blend')
        bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        bpy.ops.wm.open_mainfile(filepath=str(destination), use_scripts=False)
        actors = {key: resolve_actor(job_id, key) for key in actors}
        reload_error = compare(after, actors)
        scene = bpy.context.scene
        # Representative diagnostic frames use the retained camera and actual
        # skin; these are visual evidence, not a substitute for full-frame data.
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.render.resolution_x, scene.render.resolution_y = 640, 360
        scene.render.resolution_percentage = 100
        scene.display.shading.light = 'STUDIO'
        scene.display.shading.color_type = 'MATERIAL'
        renders = []
        for frame in (min(end, start + (end - start) // 2), end + 22):
            scene.frame_set(frame)
            path = artifact_dir / (label + '_frame_' + str(frame) + '.png')
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            renders.append(str(path))
        unchanged_history = sha(source) == original_hash
        passed = (within(capture_error) and within(append_preserved_error) and within(reload_error)
                  and hand_displacement > .15 and structures_retained and identity_retained and unchanged_history)
        case = {'kind': label, 'passed': passed, 'source_scene': str(source), 'source_sha256': original_hash,
                'historical_scene_unchanged': unchanged_history, 'actor_count': len(actors),
                'original_frame_range': [start, end], 'extended_frame_range': [start, append_end],
                'capture_error': capture_error, 'append_original_segment_error': append_preserved_error,
                'reload_error': reload_error, 'new_right_hand_displacement_m': hand_displacement,
                'camera_lights_props_objects_retained': structures_retained, 'original_skin_identity_retained': identity_retained,
                'full_bone_and_skin_all_frame_check': True, 'result_scene': str(destination), 'renders': renders}
        cases.append(case)
        print('RECIPE_ADAPTER ' + json.dumps(case, ensure_ascii=False), flush=True)
        report_path.write_text(json.dumps({'passed': all(x['passed'] for x in cases), 'cases': cases}, ensure_ascii=False, indent=2) + '\n')
        del before, after
    report = {'passed': all(case['passed'] for case in cases), 'cases': cases,
              'scope': 'Same Beta recipe adoption, all-frame/all-bone/all-skin-vertex capture, new FK overlay and persisted reload. New hand motion is a technical FK proof; naturalness requires separate review.'}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.cleanup()
    if not report['passed']:
        raise RuntimeError('Robot recipe adapter regression failed')


if __name__ == '__main__':
    main()
