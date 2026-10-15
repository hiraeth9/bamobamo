"""Headless-only asset audit and rebuild of the original, unanimated Beta cache.

Run: ./blender -b --python scripts/scan_robot_assets.py
The script resets its own process scene; never run through the shared GUI MCP.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

import bpy

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
from agent_character import binding_facts, compatible_rig, file_sha256, matrix_values, rig_facts
from duel_plan import atomic_json


def fbx_metadata(path):
    from io_scene_fbx import parse_fbx
    from io_scene_fbx.fbx_utils import FBX_FRAMERATES
    root, version = parse_fbx.parse(str(path))
    settings = next(elem for elem in root.elems if elem.id == b'GlobalSettings')
    properties = next(elem for elem in settings.elems if elem.id == b'Properties70')
    data = {}
    for elem in properties.elems:
        if elem.id == b'P' and len(elem.props) >= 5:
            data[elem.props[0].decode()] = elem.props[4]
    time_mode = data.get('TimeMode')
    fps = {enum: rate for rate, enum in FBX_FRAMERATES[1:]}.get(time_mode, data.get('CustomFrameRate', 25))
    if fps <= 0:
        fps = 25
    return {'fbx_version': version, 'time_mode': time_mode, 'custom_frame_rate': data.get('CustomFrameRate'),
            'source_fps': fps, 'source_unit_scale_factor': data.get('UnitScaleFactor'),
            'source_original_unit_scale_factor': data.get('OriginalUnitScaleFactor'),
            'source_axes': {key: data.get(key) for key in
                            ('UpAxis', 'UpAxisSign', 'FrontAxis', 'FrontAxisSign', 'CoordAxis', 'CoordAxisSign')}}


def action_facts(armature):
    action = armature.animation_data.action if armature.animation_data else None
    if action is None:
        return []
    return [{'name': action.name, 'frame_range': list(action.frame_range),
             'slots': [{'identifier': slot.identifier, 'target_id_type': slot.target_id_type}
                       for slot in action.slots],
             'active_slot': armature.animation_data.action_slot.identifier
                            if armature.animation_data.action_slot else None}]


def import_facts(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with contextlib.redirect_stdout(io.StringIO()):
        bpy.ops.import_scene.fbx(filepath=str(path))
    rigs = [obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE']
    if len(rigs) != 1:
        raise ValueError(f'Expected one armature, got {len(rigs)}')
    armature = rigs[0]
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    return {'asset': str(path.relative_to(PROJECT)), 'path_sha256': file_sha256(path), 'status': 'scanned',
            'skeleton': rig_facts(armature),
            'object_transform': {'scale': list(armature.scale), 'matrix_basis': matrix_values(armature.matrix_basis),
                                 'matrix_world': matrix_values(armature.matrix_world)},
            'binding': binding_facts(meshes, armature), 'actions': action_facts(armature),
            **fbx_metadata(path),
            'imported_fps': bpy.context.scene.render.fps / bpy.context.scene.render.fps_base}


def build_cache(canonical):
    path = PROJECT / canonical['asset']
    import_facts(path)
    armature = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
    idle_start = armature.animation_data.action.frame_range[0]
    bpy.context.scene.frame_set(int(idle_start))
    bpy.context.view_layer.update()
    default_pose = {bone.name: {'location': list(bone.location),
                              'rotation_quaternion': list(bone.rotation_quaternion),
                              'scale': list(bone.scale)} for bone in armature.pose.bones}
    for obj in bpy.context.scene.objects:
        obj.animation_data_clear()
        if obj.type == 'ARMATURE':
            for bone in obj.pose.bones:
                bone.rotation_mode = 'QUATERNION'
                bone.location = default_pose[bone.name]['location']
                bone.rotation_quaternion = default_pose[bone.name]['rotation_quaternion']
                bone.scale = default_pose[bone.name]['scale']
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    bpy.context.scene.render.fps, bpy.context.scene.render.fps_base = 30, 1
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    armature = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    vertices = [mesh.matrix_world @ vertex.co for mesh in meshes for vertex in mesh.data.vertices]
    bounds = {'min': [min(v[i] for v in vertices) for i in range(3)],
              'max': [max(v[i] for v in vertices) for i in range(3)]}
    bpy.ops.file.pack_all()
    cache = PROJECT / 'assets/characters/beta/character.blend'
    cache.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(cache), check_existing=False)
    return {'cache_asset': str(cache.relative_to(PROJECT)), 'cache_sha256': file_sha256(cache),
            'rest_world_bounds_m': bounds, 'height_m': bounds['max'][2]-bounds['min'][2],
            'cache_binding_signature': binding_facts(meshes, armature)['signature'],
            'default_pose_source': canonical['asset'], 'default_pose_source_frame': idle_start,
            'default_pose': default_pose}


def main():
    if not bpy.app.background:
        raise RuntimeError('扫描只允许隔离 headless Blender，不能重置GUI当前任务')
    files = sorted((PROJECT / 'mixamo_fbx').rglob('*.fbx'))
    canonical_path = PROJECT / 'mixamo_fbx/basic/Idle.fbx'
    canonical = import_facts(canonical_path)
    assets = []
    cache_only = '--cache-only' in sys.argv
    if cache_only:
        previous = json.loads((PROJECT / 'reports/robot_asset_scan_20261014.json').read_text())
        assets = previous['assets']
        if any(not (PROJECT / item['asset']).is_file() or
               file_sha256(PROJECT / item['asset']) != item.get('path_sha256') for item in assets):
            raise ValueError('Asset changed since scan; full scan required')
        files = []
    for index, path in enumerate(files):
        try:
            item = canonical if path == canonical_path else import_facts(path)
            skeleton_ok = compatible_rig(item['skeleton'], canonical['skeleton'])
            transform_ok = max(abs(a-b) for row, reference in zip(item['object_transform']['matrix_basis'],
                               canonical['object_transform']['matrix_basis']) for a,b in zip(row,reference)) <= 1e-5
            binding_same = item['binding']['signature'] == canonical['binding']['signature']
            item['compatibility'] = {'skeleton': skeleton_ok, 'object_transform': transform_ok,
                                     'binding': binding_same,
                                     'direct_action': skeleton_ok and transform_ok and bool(item['actions'])}
        except Exception as exc:
            item = {'asset': str(path.relative_to(PROJECT)), 'status': 'failed',
                    'error': f'{type(exc).__name__}: {exc}'}
        assets.append(item)
        print(f'ROBOT_SCAN {index+1}/{len(files)} {item["asset"]} {item["status"]}', flush=True)
    audit_only = '--audit-only' in sys.argv
    cache = None if audit_only else build_cache(canonical)
    report = {'version': 1, 'date': '2026-10-14', 'blender_version': bpy.app.version_string,
              'canonical_asset': canonical['asset'], 'asset_count': len(assets),
              'scanned_count': sum(item['status'] == 'scanned' for item in assets),
              'direct_action_count': sum(item.get('compatibility', {}).get('direct_action', False) for item in assets),
              'scope': 'Measured FBX skeleton/rest/units/undeformed skin/binding/source FPS only. '
                       'No semantic, contact or naturalness certification.', 'assets': assets}
    report_path = PROJECT / 'reports/robot_asset_scan_20261014.json'
    atomic_json(report_path, report)
    if audit_only:
        if report['scanned_count'] != len(assets):
            raise RuntimeError('Some original FBX assets failed scanning; inspect report')
        print('ROBOT_AUDIT_COMPLETE', json.dumps({key: report[key] for key in
              ('asset_count', 'scanned_count', 'direct_action_count')}, ensure_ascii=False), flush=True)
        return
    # Mma Idle is accepted for recipe adoption only when actual original skin
    # and rest rig match; a new binding hash is never accepted by filename.
    accepted = [canonical['binding']['signature']]
    mma = next((item for item in assets if item['asset'] == 'mixamo_fbx/combat_melee/Mma Idle.fbx'), None)
    if mma and mma.get('compatibility', {}).get('skeleton') and mma['binding']['binding_ok']:
        accepted.append(mma['binding']['signature'])
    character = {'character_id': 'beta', 'version': 1, 'source_asset': canonical['asset'],
                 'source_sha256': canonical['path_sha256'], 'skeleton': canonical['skeleton'],
                 'object_transform': canonical['object_transform'], 'binding': canonical['binding'],
                 'accepted_binding_signatures': sorted(set(accepted)),
                 'world_unit': 'metre', 'rest_bone_unit': 'centimetre',
                 'up_axis': '+Z', 'forward_axis': '-Y', 'validation_state': 'asset_scanned',
                 'license': {'source': 'existing local Mixamo Beta FBX',
                             'identifier': 'Mixamo terms; not CC0 or open source',
                             'redistribution_review_required': True},
                 'report': str(report_path.relative_to(PROJECT)), **cache}
    atomic_json(PROJECT / 'knowledge/characters/catalog.json', {'version': 1, 'characters': {'beta': character}})
    if report['scanned_count'] != len(assets):
        raise RuntimeError('Some original FBX assets failed scanning; inspect report')
    print('ROBOT_SCAN_COMPLETE', json.dumps({key: report[key] for key in
          ('asset_count', 'scanned_count', 'direct_action_count')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
