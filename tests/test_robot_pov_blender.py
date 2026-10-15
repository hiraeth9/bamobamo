"""Original two-skin Beta full -> attacker POV -> full, without split meshes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))


def main():
    import bpy
    import numpy as np
    from mathutils import Vector
    import duel_plan
    from agent_character import actor_ensure, resolve_actor, binding_facts, scene_actor_facts
    from agent_motion_timeline import register_event, motion_commit
    from agent_motion_cameras import camera_pov, camera_full

    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default=str(PROJECT / 'reports/robot_pov_validation_20261014.json'))
    parser.add_argument('--reuse-fixture', help='Load this test\'s own saved, original-skin guard/punch fixture; rerun every camera check')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    output = Path(args.report).resolve()
    artifacts = output.with_suffix('')
    artifacts.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix='bamo_pov_test_')
    duel_plan.JOBS = Path(temporary.name)
    job = 'robot_pov_proof'
    folder = duel_plan.JOBS / job
    folder.mkdir()
    (folder / 'request.json').write_text(json.dumps({'prompt': 'B出拳时切B第一视角看A，随后回全景'}), encoding='utf-8')
    if args.reuse_fixture:
        bpy.ops.wm.open_mainfile(filepath=str(Path(args.reuse_fixture).resolve()), use_scripts=False)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 30
    scene.render.fps = 30
    scene.render.fps_base = 1
    if not args.reuse_fixture:
        actor_ensure(job, 'A', position=(0, -.7, 0), yaw=3.141592653589793, color=(.8, .11, .07, 1))
        actor_ensure(job, 'B', position=(0, .7, 0), yaw=0, color=(.08, .27, .9, 1))
        for actor in ('A', 'B'):
            register_event(job, actor, 'guard', {'source': 'library', 'motion_id': 'mixamo.combat_melee.mma_idle',
                                               'start_frame': 1, 'end_frame': 30, 'root_strategy': 'hold'})
        register_event(job, 'B', 'cross', {'source': 'library', 'motion_id': 'quaternius.punch_cross',
                                         'start_frame': 9, 'end_frame': 21, 'blend_in_frames': 2,
                                         'source_window': [0., 1.], 'root_strategy': 'hold', 'priority': 1})
        for actor in ('A', 'B'):
            motion_commit(job, actor, frame_start=1, frame_end=30)
    a, b = resolve_actor(job, 'A'), resolve_actor(job, 'B')
    if args.reuse_fixture:
        full = bpy.data.objects['Full']
    else:
        full_data = bpy.data.cameras.new('Full')
        full = bpy.data.objects.new('Full', full_data)
        scene.collection.objects.link(full)
    full.location = (3.8, -4.5, 2.7)
    full.rotation_euler = (Vector((0, 0, .9)) - full.location).to_track_quat('-Z', 'Y').to_euler()
    full.data.type = 'ORTHO'
    full.data.ortho_scale = 4.1
    scene.camera = full
    full_name = full.name
    old_meshes = set(obj.name for obj in scene.objects if obj.type == 'MESH')
    original_data = {actor: binding_facts(info['meshes'], info['armature'])['signature'] for actor, info in (('A', a), ('B', b))}

    def viewport_vertices(actors):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        snapshots = {}
        for actor in actors:
            for obj in actor['meshes']:
                evaluated = obj.evaluated_get(depsgraph)
                mesh = evaluated.to_mesh()
                try:
                    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
                    mesh.vertices.foreach_get('co', coords)
                    snapshots[obj.name] = coords
                finally:
                    evaluated.to_mesh_clear()
        return snapshots

    scene.frame_set(15)
    before = viewport_vertices((a, b))
    info = camera_pov(job, 'B', 'A', 10, 21, fov=75, return_camera=full.name, shot_id='B_counter')
    second = camera_pov(job, 'B', 'A', 10, 21, fov=75, return_camera=full.name, shot_id='B_counter')
    after = viewport_vertices((a, b))
    viewport_error = max(float(np.max(np.abs(before[name] - after[name]))) for name in before)
    all_skin_kept = (old_meshes == set(obj.name for obj in scene.objects if obj.type == 'MESH') and
                     all(binding_facts(actor['meshes'], actor['armature'])['signature'] == original_data[key]
                         for key, actor in (('A', a), ('B', b))))
    markers = []
    states = []
    camera = bpy.data.objects[info['camera']]
    for frame, expected in ((1, full_name), (15, camera.name), (25, full_name)):
        scene.frame_set(frame)
        markers.append({'frame': frame, 'camera': scene.camera.name, 'expected': expected,
                        'passed': scene.camera.name == expected})
        states.append({'frame': frame, 'masks': [bool(obj.modifiers[record['modifier']].show_render)
                                               for record in info['render_masks'] for obj in (bpy.data.objects[record['mesh']],)]})
    scene.frame_set(15)
    # Evaluate the same original meshes with equivalent modifier settings to
    # measure the removal, then restore viewport-off before all saved output.
    counts = []
    for obj in b['meshes']:
        original_count = len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices)
        modifiers = [m for m in obj.modifiers if m.name.startswith('WM_POV_RenderOnly_')]
        for modifier in modifiers:
            modifier.show_viewport = True
        bpy.context.view_layer.update()
        masked_count = len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices)
        for modifier in modifiers:
            modifier.show_viewport = False
        bpy.context.view_layer.update()
        counts.append({'mesh': obj.name, 'full_vertices': original_count, 'masked_vertices': masked_count,
                       'passed': 0 < masked_count < original_count})
    scene.frame_set(15)
    pose_before_reload = viewport_vertices((a, b))
    camera_transform = [list(row) for row in camera.matrix_world]
    saved = artifacts / 'two_beta_pov.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved), use_scripts=False)
    a, b = resolve_actor(job, 'A'), resolve_actor(job, 'B')
    scene = bpy.context.scene
    scene.frame_set(15)
    reloaded = viewport_vertices((a, b))
    reload_error = max(float(np.max(np.abs(pose_before_reload[name] - reloaded[name]))) for name in pose_before_reload)
    camera = bpy.data.objects[info['camera']]
    camera_reload_error = max(abs(x-y) for row, expected in zip(camera.matrix_world, camera_transform) for x,y in zip(row,expected))
    identity = scene_actor_facts(job)
    masks_viewport_off = all(not bpy.data.objects[record['mesh']].modifiers[record['modifier']].show_viewport for record in info['render_masks'])
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x, scene.render.resolution_y = 640, 360
    scene.render.resolution_percentage = 100
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    renders = []
    for frame in (1, 15, 25):
        scene.frame_set(frame)
        path = artifacts / ('frame_' + str(frame) + '.png')
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        renders.append(str(path))
    def scene_digest():
        records = {'scene': {'camera': scene.camera.name if scene.camera else None,
                              'frame': scene.frame_current, 'properties': {key: str(scene[key]) for key in scene.keys()}},
                   'objects': [], 'markers': [(m.name, m.frame, m.camera.name if m.camera else None)
                                             for m in scene.timeline_markers], 'actions': []}
        for obj in sorted(bpy.data.objects, key=lambda x: x.name):
            records['objects'].append({'name': obj.name, 'type': obj.type,
                                       'matrix': [list(row) for row in obj.matrix_world],
                                       'mask': [(m.name, m.vertex_group, m.show_viewport, m.show_render)
                                                for m in obj.modifiers if m.type == 'MASK']})
        for action in sorted(bpy.data.actions, key=lambda x: x.name):
            curves = []
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        curves.extend((curve.data_path, curve.array_index,
                                       [(list(point.co), point.interpolation) for point in curve.keyframe_points])
                                      for curve in bag.fcurves)
            records['actions'].append((action.name, curves))
        return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()

    rejected_overlaps = []
    for owner, target, begin, finish, sid in (('A', 'B', 15, 23, 'A_overlap'),
                                              ('A', 'B', 21, 23, 'A_endpoint_overlap'),
                                              ('B', 'A', 20, 24, 'B_other_overlap')):
        unchanged = scene_digest()
        message = ''
        try:
            camera_pov(job, owner, target, begin, finish, return_camera=full_name, shot_id=sid)
        except ValueError as exc:
            message = str(exc)
        rejected_overlaps.append({'shot_id': sid, 'message': message,
                                  'state_digest_unchanged': unchanged == scene_digest(),
                                  'passed': 'overlaps' in message and unchanged == scene_digest()})
    # This unrelated display channel must survive removal/recompilation of
    # only the helper's mask.show_render curves.
    for mesh in b['meshes']:
        mesh.hide_render = False
        mesh.keyframe_insert('hide_render', frame=1)
        mesh.keyframe_insert('hide_render', frame=30)
    def display_curves():
        result = []
        for mesh in b['meshes']:
            for layer in mesh.animation_data.action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for curve in bag.fcurves:
                            if curve.data_path == 'hide_render':
                                result.append((mesh.name, curve.array_index, [list(key.co) for key in curve.keyframe_points]))
        return result
    display_before = display_curves()
    updated = camera_pov(job, 'B', 'A', 23, 27, fov=75, shot_id='B_counter')
    stable_update_checks = []
    for frame, expected, masked in ((15, full_name, False), (24, updated['camera'], True), (29, full_name, False)):
        scene.frame_set(frame)
        masks_state = [bpy.data.objects[record['mesh']].modifiers[record['modifier']].show_render
                       for record in updated['render_masks']]
        stable_update_checks.append({'frame': frame, 'camera': scene.camera.name,
                                     'masks': masks_state,
                                     'passed': scene.camera.name == expected and all(bool(value) == masked for value in masks_state)})
    active_keys = []
    for layer in bpy.data.objects[updated['camera']].animation_data.action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                active_keys.extend(point.co.x for curve in bag.fcurves for point in curve.keyframe_points)
    stable_update = {'passed': all(x['passed'] for x in stable_update_checks)
                               and info['camera'] == updated['camera']
                               and display_before == display_curves()
                               and min(active_keys) == 23 and max(active_keys) == 27,
                     'checks': stable_update_checks, 'same_camera_object': info['camera'] == updated['camera'],
                     'unrelated_display_curve_preserved': display_before == display_curves(),
                     'active_camera_keyframe_range': [min(active_keys), max(active_keys)]}
    updated_scene = artifacts / 'stable_shot_updated.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(updated_scene))
    bpy.ops.wm.open_mainfile(filepath=str(updated_scene), use_scripts=False)
    scene = bpy.context.scene
    update_reload = []
    for frame, expected, masked in ((15, full_name, False), (24, updated['camera'], True), (29, full_name, False)):
        scene.frame_set(frame)
        mask_values = [bpy.data.objects[record['mesh']].modifiers[record['modifier']].show_render
                       for record in updated['render_masks']]
        update_reload.append({'frame': frame, 'passed': scene.camera.name == expected and all(bool(value) == masked for value in mask_values)})
    stable_update['saved_scene'] = str(updated_scene)
    stable_update['reload_checks'] = update_reload
    stable_update['passed'] = stable_update['passed'] and all(check['passed'] for check in update_reload)
    # Reuse the tested fitter for world +60/+20 and verify actual deformed
    # skins, including managed Full markers after a previously added POV.
    a, b = resolve_actor(job, 'A'), resolve_actor(job, 'B')
    bpy.ops.mesh.primitive_cube_add(size=.4, location=(1.2, -.5, .2))
    focus = bpy.context.object
    focus.name = 'FocusBox'
    skin_before_full = {key: binding_facts(actor['meshes'], actor['armature'])['signature'] for key, actor in (('A', a), ('B', b))}
    full_result = camera_full(job, {'reference': 'world', 'azimuth_deg': 60, 'elevation_deg': 20,
                                   'projection': 'orthographic', 'fov_deg': 50, 'occlusion_policy': 'strict'},
                              focus_objects=[focus.name])
    fitted = bpy.data.objects[full_result['camera']]
    offset = fitted.matrix_world.translation - Vector(full_result['facts']['applied']['look_at'])
    azimuth = float(np.degrees(np.arctan2(offset.x, -offset.y)))
    elevation = float(np.degrees(np.arctan2(offset.z, offset.xy.length)))
    min_margin = 1.
    for frame in range(1, 31):
        scene.frame_set(frame)
        deps = bpy.context.evaluated_depsgraph_get()
        projection = np.asarray(fitted.calc_matrix_camera(deps, x=scene.render.resolution_x, y=scene.render.resolution_y)
                                @ fitted.matrix_world.inverted(), dtype=float)
        for obj in [*a['meshes'], *b['meshes'], focus]:
            evaluated = obj.evaluated_get(deps)
            coords = np.empty(len(evaluated.data.vertices) * 3, dtype=np.float32)
            evaluated.data.vertices.foreach_get('co', coords)
            matrix = np.asarray(evaluated.matrix_world, dtype=float)
            world = coords.reshape((-1, 3)) @ matrix[:3, :3].T + matrix[:3, 3]
            projected = np.c_[world, np.ones(len(world))] @ projection.T
            uv = projected[:, :2] / projected[:, 3:4] * .5 + .5
            min_margin = min(min_margin, float(np.minimum(uv, 1 - uv).min()))
    full_marker_checks = []
    for frame, expected in ((15, fitted.name), (24, updated['camera']), (29, fitted.name)):
        scene.frame_set(frame)
        full_marker_checks.append({'frame': frame, 'camera': scene.camera.name, 'passed': scene.camera.name == expected})
    full_check = {'passed': abs(azimuth - 60) < .001 and abs(elevation - 20) < .001 and min_margin > .05
                            and all(x['passed'] for x in full_marker_checks)
                            and all(binding_facts(actor['meshes'], actor['armature'])['signature'] == skin_before_full[key]
                                    for key, actor in (('A', a), ('B', b))),
                  'actual_world_azimuth_deg': azimuth, 'actual_elevation_deg': elevation,
                  'minimum_all_skin_and_focus_vertex_margin_all_30_frames': min_margin,
                  'managed_marker_checks': full_marker_checks, 'camera_info': full_result}
    full_scene = artifacts / 'full_60_20_and_pov.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(full_scene))
    full_check['scene'] = str(full_scene)
    passed = (all_skin_kept and viewport_error == 0 and reload_error == 0 and camera_reload_error < 1e-6
              and masks_viewport_off and not identity['errors'] and all(c['passed'] for c in counts)
              and all(x['passed'] for x in markers) and info['camera'] == second['camera']
              and not any(states[0]['masks']) and all(states[1]['masks']) and not any(states[2]['masks'])
              and all(x['passed'] for x in rejected_overlaps) and stable_update['passed'] and full_check['passed'])
    result = {'passed': passed, 'camera_info': info, 'source_actor_count': 2, 'original_mesh_count': len(old_meshes),
              'original_skin_identity_preserved': all_skin_kept, 'viewport_vertex_error': viewport_error,
              'render_mask_vertex_counts': counts, 'marker_checks': markers, 'render_mask_states': states,
              'mask_viewport_disabled': masks_viewport_off, 'skin_reload_error': reload_error,
              'camera_reload_error': camera_reload_error, 'scene': str(saved), 'renders': renders,
              'overlap_rejections': rejected_overlaps, 'stable_shot_id_update': stable_update,
              'fixed_full_camera_60_20': full_check,
              'scope': 'Original Beta identity, render-only head masking, bone-based POV/cuts and persistence. Pixel semantic visibility requires independent review.'}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('ROBOT_POV ' + json.dumps({'passed': passed, 'renders': renders, 'counts': counts}), flush=True)
    temporary.cleanup()
    if not passed:
        raise RuntimeError('Robot POV regression failed')


if __name__ == '__main__':
    main()
