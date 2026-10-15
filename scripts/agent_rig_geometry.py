"""Optional rest-space rigid proxy skin; no action/keyframe/rig edits.

The mesh vertices are in armature object space at REST, never current pose
world space.  Each segment has a single exact-name vertex group, weight 1.
Its mesh follows the armature OBJECT once, not an animated BONE as well.
"""
import math


def attach_rest_proxy(armature, bone_names=None, radii=None, material=None,
                      name=None, collection=None, sides=12):
    import bpy
    from mathutils import Matrix, Vector
    if armature.type != 'ARMATURE':
        raise ValueError('armature must be an ARMATURE object')
    names = list(bone_names) if bone_names is not None else [
        bone.name for bone in armature.data.bones if bone.use_deform and bone.length > 1e-6]
    radii = radii or {}
    points = [point for bone in armature.data.bones for point in (bone.head_local, bone.tail_local)]
    extent = max((max(p[i] for p in points) - min(p[i] for p in points)) for i in range(3)) if points else 1.
    vertices, faces, groups = [], [], {}
    for bone_name in names:
        bone = armature.data.bones[bone_name]
        if not bone.use_deform or bone.length <= 1e-6:
            raise ValueError('Choose a nonzero deform bone: ' + bone_name)
        radius = float(radii.get(bone_name, max(extent * .014, min(bone.length * .12, extent * .065))))
        if not math.isfinite(radius) or radius <= 0:
            raise ValueError('radius must be finite and positive: ' + bone_name)
        start = len(vertices)
        # Bone-local Y runs from its rest head (0) to rest tail (length).
        for y in (0., bone.length):
            for index in range(sides):
                angle = 2 * math.pi * index / sides
                local = Vector((radius * math.cos(angle), y, radius * math.sin(angle)))
                vertices.append(tuple(bone.matrix_local @ local))
        for index in range(sides):
            after = (index + 1) % sides
            faces.append((start + index, start + after, start + sides + after, start + sides + index))
        faces.append(tuple(start + index for index in reversed(range(sides))))
        faces.append(tuple(start + sides + index for index in range(sides)))
        groups[bone_name] = list(range(start, len(vertices)))
    data = bpy.data.meshes.new((name or armature.name + '_RestProxy') + '_Mesh')
    data.from_pydata(vertices, [], faces)
    data.update()
    mesh = bpy.data.objects.new(name or armature.name + '_RestProxy', data)
    (collection or bpy.context.scene.collection).objects.link(mesh)
    # Do not set world transforms before bone parenting or reuse a posed matrix.
    mesh.parent = armature
    mesh.parent_type = 'OBJECT'
    mesh.matrix_parent_inverse = Matrix.Identity(4)
    mesh.matrix_basis = Matrix.Identity(4)
    for bone_name, indices in groups.items():
        mesh.vertex_groups.new(name=bone_name).add(indices, 1., 'REPLACE')
    modifier = mesh.modifiers.new('RestSpaceSkin', 'ARMATURE')
    modifier.object = armature
    modifier.use_vertex_groups = True
    modifier.use_bone_envelopes = False
    modifier.use_deform_preserve_volume = False
    if material is not None:
        mesh.data.materials.append(material)
    for polygon in mesh.data.polygons:
        polygon.use_smooth = True
    mesh['proxy_scope'] = 'rigid per-bone rest-space geometry; not smooth anatomical skin or physical QA'
    return mesh


def _curves(action):
    if action is None:
        return []
    if hasattr(action, 'fcurves'):
        return list(action.fcurves)
    return [curve for layer in action.layers for strip in layer.strips
            for bag in getattr(strip, 'channelbags', []) for curve in bag.fcurves]


def animation_snapshot(armatures):
    return {arm.name: {'action': arm.animation_data.action.name if arm.animation_data and arm.animation_data.action else None,
                      'curves': [(curve.data_path, curve.array_index,
                                  [(tuple(point.co), tuple(point.handle_left), tuple(point.handle_right), point.interpolation)
                                   for point in curve.keyframe_points])
                                 for curve in _curves(arm.animation_data.action if arm.animation_data else None)],
                      'rest': {bone.name: [tuple(row) for row in bone.matrix_local] for bone in arm.data.bones}}
            for arm in armatures}


def main():
    import argparse
    import hashlib
    import json
    from pathlib import Path
    import sys
    import bpy
    import numpy as np
    from mathutils import Vector
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    source = Path(args.source).resolve()
    folder = Path(args.output).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    arms = [obj for obj in scene.objects if obj.type == 'ARMATURE']
    before = animation_snapshot(arms)
    material = bpy.data.materials.new('ReferenceProxyClay')
    material.use_nodes = True
    material.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (.8, .8, .8, 1.)
    material.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value = .7
    proxies = []
    for arm in arms:
        names = [bone.name for bone in arm.data.bones if bone.use_deform and bone.length > 1e-6 and bone.name.lower() != 'root']
        radii = {bone.name: (.14 if 'head' in bone.name.lower() else .16 if 'spine' in bone.name.lower() else .055)
                 for bone in arm.data.bones}
        proxies.append((arm, attach_rest_proxy(arm, names, radii, material)))
    reference = []
    for arm, mesh in proxies:
        by_bone = {}
        coords = np.array([tuple(vertex.co) + (1.,) for vertex in mesh.data.vertices], dtype=np.float64)
        for group in mesh.vertex_groups:
            indices = [vertex.index for vertex in mesh.data.vertices if any(entry.group == group.index for entry in vertex.groups)]
            by_bone[group.name] = np.array(indices)
        inverse = {name: np.array(arm.data.bones[name].matrix_local.inverted(), dtype=np.float64) for name in by_bone}
        reference.append((arm, mesh, coords, by_bone, inverse))
    maximum = 0.
    frames = list(range(scene.frame_start, scene.frame_end + 1))
    for frame in frames:
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        for arm, mesh, coords, groups, inverse in reference:
            evaluated = mesh.evaluated_get(graph)
            actual_local = np.empty(len(coords) * 3, dtype=np.float32)
            evaluated.data.vertices.foreach_get('co', actual_local)
            actual_h = np.column_stack((actual_local.reshape((-1, 3)), np.ones(len(coords))))
            actual_world = (np.array(evaluated.matrix_world) @ actual_h.T).T[:, :3]
            expected = np.zeros_like(actual_world)
            arm_world = np.array(arm.matrix_world, dtype=np.float64)
            for name, indices in groups.items():
                deform = arm_world @ np.array(arm.pose.bones[name].matrix, dtype=np.float64) @ inverse[name]
                expected[indices] = (deform @ coords[indices].T).T[:, :3]
            maximum = max(maximum, float(np.linalg.norm(actual_world - expected, axis=1).max()))
    assert maximum < 1e-5, maximum
    assert animation_snapshot(arms) == before, 'Original FK keys or rest matrices changed'
    scene.frame_set(scene.frame_start)
    bpy.context.view_layer.update()
    # Presentation lives only in this isolated fixture, never the source job.
    world = bpy.data.worlds.new('ReferenceIllumination')
    world.use_nodes = True
    world.node_tree.nodes['Background'].inputs[0].default_value = (.16, .16, .16, 1.)
    world.node_tree.nodes['Background'].inputs[1].default_value = .6
    scene.world = world
    for name, position, energy in [('Key', (3, -4, 6), 1000), ('Fill', (-3, -2, 4), 600)]:
        light = bpy.data.lights.new(name, 'AREA')
        light.energy, light.shape, light.size = energy, 'DISK', 5
        obj = bpy.data.objects.new(name, light)
        scene.collection.objects.link(obj)
        obj.location = position
        obj.rotation_euler = (Vector((0, 0, 1)) - obj.location).to_track_quat('-Z', 'Y').to_euler()
    camera = bpy.data.objects.new('ReferenceCamera', bpy.data.cameras.new('ReferenceCamera'))
    scene.collection.objects.link(camera)
    camera.location = (4, -7, 4)
    camera.rotation_euler = (Vector((0, 0, .9)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type, camera.data.ortho_scale = 'ORTHO', 5.2
    scene.camera = camera
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 512, 288, 100
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / 'reference.blend'), copy=True, check_existing=False)
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = str(folder / 'reference_frame.png')
    bpy.ops.render.render(write_still=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    report = {'passed': True, 'source': str(source), 'source_sha256': original_hash,
              'source_file_unchanged': True, 'actor_count': len(arms), 'tested_frames': len(frames),
              'maximum_actual_vertex_vs_pose_rest_mapping_error_m': maximum,
              'fk_keys_and_rest_matrices_unchanged': True,
              'proxies': [{'rig': arm.name, 'mesh': mesh.name, 'vertex_count': len(mesh.data.vertices),
                           'bone_count': len(mesh.vertex_groups)} for arm, mesh in proxies],
              'scope': 'rest-space binding only; rigid segmented proxy, no anatomical-skin or action-naturalness certification',
              'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (folder / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('REST_PROXY_VALIDATED ' + json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
