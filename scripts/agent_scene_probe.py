"""Headless reproducibility check for a job-scoped agent scene.

blender -b --python-exit-code 1 --python agent_scene_probe.py -- \
    --manifest /.../agent_scene.json --report /.../agent_scene_probe.json

This validates files, Blender evaluation and registered geometric contacts.
It does not certify natural-language completeness, naturalness or dynamics.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_scene import (appearance_warnings, compare_evaluation_sample, evaluation_sample,
                         resource_facts, robot_execution_facts, scene_facts, validate_manifest_files)
from duel_plan import atomic_json


def _finite_matrix(matrix):
    return all(math.isfinite(float(value)) for row in matrix for value in row)


def _animation_ids(bpy):
    result, seen = [], set()
    for item in bpy.data.user_map():
        for candidate in (item, getattr(item, 'node_tree', None)):
            if candidate is not None and candidate.as_pointer() not in seen:
                seen.add(candidate.as_pointer())
                if getattr(candidate, 'animation_data', None):
                    result.append(candidate)
    return result


def _invalid_drivers(items):
    return [{'owner': item.name, 'path': curve.data_path, 'index': curve.array_index}
            for item in items for curve in item.animation_data.drivers
            if not curve.driver.is_valid]


def _camera_for_frame(scene, frame):
    markers = [m for m in scene.timeline_markers if m.camera is not None and m.frame <= frame]
    return max(markers, key=lambda m: m.frame).camera if markers else scene.camera


def resolution_messages(resolution):
    """Odd source sizes are valid; the worker pads the H.264 output size."""
    if (not isinstance(resolution, (list, tuple)) or len(resolution) != 2
            or any(type(value) is not int or value <= 0 for value in resolution)):
        return [{'code': 'resolution', 'message': '视频出片需要正分辨率'}], []
    if any(value % 2 for value in resolution):
        return [], ['保存的分辨率含奇数宽高；H.264 输出由后台向上补1像素至偶数']
    return [], []


def probe_scene(manifest_path):
    report = {'version': 1, 'scope': 'saved_scene_reproducibility_render_runtime_and_registered_geometric_contacts',
              'passed': False, 'manifest_path': str(manifest_path), 'errors': [],
              'warnings': [], 'tested_frames': 0, 'created_at': time.time()}
    errors, warnings = report['errors'], report['warnings']
    try:
        manifest, folder = validate_manifest_files(manifest_path)
        report.update({'job_id': manifest['job_id'], 'submission_id': manifest['submission_id'],
                       'scene_sha256': manifest['scene_sha256'],
                       'request_sha256': manifest['request_sha256'],
                       'analysis_sha256': manifest['analysis_sha256']})
        warnings.extend(manifest.get('warnings', []))
        import bpy
        result = bpy.ops.wm.open_mainfile(filepath=str(folder / 'scene.blend'), load_ui=False, use_scripts=False)
        if 'FINISHED' not in result:
            raise RuntimeError('Blender 无法打开已保存场景')
        scene = bpy.context.scene
        scene.frame_set(scene.frame_start)
        bpy.context.view_layer.update()
        actual = scene_facts(scene)
        report['actual'] = actual
        if manifest.get('character_policy') == 'library_beta':
            request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
            execution = robot_execution_facts(folder.name, request)
            report['robot_execution'] = execution
            for field in ('character_policy', 'character_execution', 'motion_execution'):
                if manifest.get(field) != execution.get(field):
                    errors.append({'code': 'robot_execution_mismatch', 'field': field})
        for warning in appearance_warnings(actual):
            if warning not in warnings:
                warnings.append(warning)
        if not actual.get('illuminated', True):
            errors.append({'code': 'unlit_scene', 'render_engine': actual.get('render_engine'),
                           'render_enabled_light_count': actual.get('render_enabled_light_count'),
                           'world_present': actual.get('world_present'),
                           'message': 'EEVEE/Cycles 场景没有任何可渲染灯光、非黑 World 或自发光，'
                                      '成片会全黑；请打灯（例如 print(bpy.wm_agent_lighting())，'
                                      '或用 bpy.data.lights 新建 AREA/SUN 并设置 scene.world）后重新提交。'})
        for field in ('scene_name', 'frame_start', 'frame_end', 'frame_step', 'frame_count',
                      'fps_nominal', 'resolution', 'resolution_x', 'resolution_y',
                      'resolution_percentage', 'render_engine', 'camera_markers'):
            if manifest.get(field) != actual[field]:
                errors.append({'code': 'manifest_mismatch', 'field': field,
                               'expected': manifest.get(field), 'actual': actual[field]})
        # Added diagnostic facts are optional in previously committed scenes.
        for field in ('actor_geometry', 'actor_geometry_scope', 'light_count', 'render_enabled_light_count'):
            if field in manifest and manifest[field] != actual[field]:
                errors.append({'code': 'manifest_mismatch', 'field': field,
                               'expected': manifest[field], 'actual': actual[field]})
        for field in ('fps_base', 'fps', 'duration_seconds'):
            claimed = manifest.get(field)
            if (isinstance(claimed, bool) or not isinstance(claimed, (float, int))
                    or not math.isfinite(claimed)
                    or not math.isclose(claimed, actual[field], rel_tol=1e-7, abs_tol=1e-7)):
                errors.append({'code': 'manifest_mismatch', 'field': field,
                               'expected': claimed, 'actual': actual[field]})
        expected_camera, actual_camera = manifest.get('camera'), actual['camera']
        if bool(expected_camera) != bool(actual_camera):
            errors.append({'code': 'camera_mismatch'})
        elif actual_camera:
            for field in ('name', 'type', 'projection'):
                if actual_camera.get(field) != expected_camera.get(field):
                    errors.append({'code': 'camera_mismatch', 'field': field})
            for field in ('matrix_world', 'lens', 'ortho_scale', 'clip_start', 'clip_end'):
                left, right = expected_camera.get(field), actual_camera.get(field)
                if isinstance(right, list):
                    good = isinstance(left, list) and len(left) == len(right) and all(
                        isinstance(a, (float, int)) and not isinstance(a, bool) and math.isfinite(a)
                        and abs(a - b) <= 1e-5 for a, b in zip(left, right))
                else:
                    good = isinstance(left, (float, int)) and not isinstance(left, bool) and math.isfinite(left) and abs(left - right) <= 1e-5
                if not good:
                    errors.append({'code': 'camera_mismatch', 'field': field})
        if actual['frame_step'] != 1:
            warnings.append('保存的 frame_step 不为1；视频按完整时间线逐帧导出，不跳帧')
        resolution_errors, resolution_warnings = resolution_messages(actual['resolution'])
        errors.extend(resolution_errors)
        warnings.extend(resolution_warnings)
        dependencies = resource_facts()
        report['resources'] = dependencies
        for resource in dependencies:
            if resource['missing']:
                errors.append({'code': 'missing_resource', **resource})
        animation_ids = _animation_ids(bpy)
        scene_objects = list(scene.objects)
        samples = {sample['frame']: sample for sample in manifest.get('evaluation_samples', [])}
        if not samples:
            errors.append({'code': 'missing_evaluation_samples', 'message': '请重新提交真实场景以记录求值样本'})
        for frame in range(scene.frame_start, scene.frame_end + 1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            graph = bpy.context.evaluated_depsgraph_get()
            graph.update()
            camera = _camera_for_frame(scene, frame)
            if camera is None or camera.type != 'CAMERA' or camera.name not in scene.objects:
                errors.append({'code': 'missing_camera', 'frame': frame})
                break
            drivers = _invalid_drivers(animation_ids)
            if drivers:
                errors.append({'code': 'invalid_drivers', 'frame': frame, 'drivers': drivers,
                               'message': '临时 Python 驱动不会自动保存；请烘焙或改为可重载表达式'})
                break
            for obj in scene_objects:
                evaluated = obj.evaluated_get(graph)
                if not _finite_matrix(evaluated.matrix_world):
                    errors.append({'code': 'nonfinite_transform', 'frame': frame, 'object': obj.name})
                    break
                if evaluated.type in ('MESH', 'CURVE', 'FONT', 'SURFACE', 'META', 'VOLUME'):
                    if not all(math.isfinite(float(v)) for point in evaluated.bound_box for v in point):
                        errors.append({'code': 'nonfinite_geometry', 'frame': frame, 'object': obj.name})
                        break
                if evaluated.type == 'ARMATURE':
                    invalid_bones = [bone.name for bone in evaluated.pose.bones if not _finite_matrix(bone.matrix)]
                    if invalid_bones:
                        errors.append({'code': 'nonfinite_pose', 'frame': frame,
                                       'object': obj.name, 'bones': invalid_bones})
                        break
            if frame in samples:
                differences = compare_evaluation_sample(samples[frame], evaluation_sample(scene, frame))
                if differences:
                    errors.append({'code': 'evaluation_changed_after_reload', 'frame': frame,
                                   'differences': differences,
                                   'message': '重载后实际姿态/蒙皮不同；请将临时回调或模拟烘焙为可保存的动画'})
            report['tested_frames'] += 1
            if errors:
                break
        if any(key in manifest for key in ('camera_visibility_version','camera_visibility_requirements','camera_visibility_status')):
            from agent_camera_quality import camera_requirements, check_camera_visibility
            try:
                if type(manifest.get('camera_visibility_version')) is not int or manifest.get('camera_visibility_version') != 1:
                    raise ValueError('Unknown camera visibility version')
                registered = camera_requirements(folder.name)
                expected = manifest.get('camera_visibility_requirements')
                if expected is None or registered['status'] == 'missing':
                    errors.append({'code': 'camera_visibility', 'reason': 'camera_requirements_missing'})
                elif expected != registered['requirements']:
                    errors.append({'code': 'camera_visibility', 'reason': 'requirements_changed_after_reload'})
                else:
                    visibility = check_camera_visibility(folder.name)
                    report['camera_visibility'] = visibility
                    if not visibility['passed']:
                        errors.append({'code': 'camera_visibility', 'reason': visibility['status'],
                                       'failure_count': visibility['failure_count'],
                                       'failures': visibility['failures']})
            except Exception as error:
                errors.append({'code': 'camera_visibility', 'reason': 'contract_or_check_error',
                               'type': type(error).__name__, 'message': str(error)})
        from agent_motion_quality import registered_contact_report, registered_ground_report
        contact_result = registered_contact_report(folder.name)
        report['registered_contacts'] = contact_result
        if not contact_result['passed']:
            errors.append({'code': 'contacts', 'reason': contact_result['status'],
                           'failure_count': contact_result['failure_count'],
                           'failures': contact_result['failures']})
        ground_result = registered_ground_report(folder.name)
        report['registered_ground'] = ground_result
        if not ground_result['passed']:
            errors.append({'code': 'ground_motion', 'reason': ground_result['status'],
                           'failures': ground_result['failures']})
        report['passed'] = not errors
    except Exception as error:
        errors.append({'code': 'probe_error', 'type': type(error).__name__, 'message': str(error)})
    report['completed_at'] = time.time()
    return report


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args(argv)
    report = probe_scene(args.manifest)
    atomic_json(args.report, report)
    print('AGENT_SCENE_PROBE ' + json.dumps({'passed': report['passed'],
                                           'tested_frames': report['tested_frames'],
                                           'errors': report['errors']}, ensure_ascii=False), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
