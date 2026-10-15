"""Full-body motion loading and world/rest-space retargeting to Mixamo Beta.

Root translation is separate from residual pelvis motion. Named clips from one
GLB are loaded once; Action Slots and muted NLA tracks are explicit.
"""
import contextlib
import io
import math
import bpy
from mathutils import Matrix, Quaternion, Vector
from duel_plan import PROJECT

PREFIX = 'mixamorig:'


def bone_mapping():
    mapping = {'Hips': 'pelvis', 'Spine': 'spine_01', 'Spine1': 'spine_02',
               'Spine2': 'spine_03', 'Neck': 'neck_01', 'Head': 'Head'}
    for side, suffix in (('Left', 'l'), ('Right', 'r')):
        for target, source in {'Shoulder': 'clavicle', 'Arm': 'upperarm',
                               'ForeArm': 'lowerarm', 'Hand': 'hand',
                               'UpLeg': 'thigh', 'Leg': 'calf', 'Foot': 'foot',
                               'ToeBase': 'ball', 'Toe_End': 'ball_leaf'}.items():
            mapping[side + target] = source + '_' + suffix
        for target, source in (('Thumb', 'thumb'), ('Index', 'index'), ('Middle', 'middle'), ('Ring', 'ring'), ('Pinky', 'pinky')):
            for i in range(1, 5):
                mapping[f'{side}Hand{target}{i}'] = f'{source}_{i:02d}' + ('_leaf' if i == 4 else '') + '_' + suffix
    return {PREFIX + k: v for k, v in mapping.items()}


class MotionSources:
    def __init__(self, definitions, needs_fall=False):
        self.definitions = definitions
        self.poses, self.ends, self.root_motion, self.analysis = {}, {}, {}, {}
        self.template, self.meshes = None, []
        self._load_fbx('guard', definitions['guard'])
        grouped = {}
        for name, definition in definitions.items():
            if name == 'guard' or not definition.get('asset'):
                continue
            if definition.get('optional_fall') and not needs_fall:
                continue
            if definition.get('clip'):
                grouped.setdefault(definition['asset'], []).append((name, definition))
            else:
                self._load_fbx(name, definition)
        for asset, clips in grouped.items():
            self._load_glb(asset, clips)
        self.bone_order = [b.name for b in self.template.data.bones]
        self.rest_rotations = {b.name:b.matrix_local.to_quaternion() for b in self.template.data.bones}
        self.parents = {b.name:b.parent.name if b.parent else None for b in self.template.data.bones}
        self.template.animation_data_clear()

    def _load_fbx(self, name, definition):
        before = set(bpy.data.objects)
        with contextlib.redirect_stdout(io.StringIO()):
            bpy.ops.import_scene.fbx(filepath=str(PROJECT / definition['asset']))
        added = set(bpy.data.objects) - before
        arm = next(o for o in added if o.type == 'ARMATURE')
        action = arm.animation_data.action
        start, end = (int(v) for v in action.frame_range)
        poses, roots = [], []
        hip = arm.pose.bones[PREFIX + 'Hips']
        origin = None
        for frame in range(start, end + 1):
            bpy.context.scene.frame_set(frame)
            pose = {b.name: (b.location.copy(), b.rotation_quaternion.copy(), b.scale.copy()) for b in arm.pose.bones}
            world = arm.matrix_world @ hip.head
            if origin is None:
                origin = world.copy()
            root = Vector((world.x - origin.x, world.y - origin.y, 0)) if definition.get('extract_root') else Vector()
            if definition.get('extract_root'):
                pose[hip.name] = (pose[hip.name][0] - arm.matrix_world.to_3x3().inverted() @ root, pose[hip.name][1], pose[hip.name][2])
            poses.append(pose)
            roots.append(root)
        self.poses[name], self.ends[name], self.root_motion[name] = poses, len(poses), roots
        if name == 'guard':
            self.template = arm
            self.meshes = [o for o in added if o.type == 'MESH']
        else:
            for obj in added:
                bpy.data.objects.remove(obj, do_unlink=True)

    def _load_glb(self, asset, clips):
        scene = bpy.context.scene
        scene.render.fps = 30
        before = set(bpy.data.objects)
        before_actions = set(bpy.data.actions)
        with contextlib.redirect_stdout(io.StringIO()):
            bpy.ops.import_scene.gltf(filepath=str(PROJECT / asset))
        added = set(bpy.data.objects) - before
        source = next(o for o in added if o.type == 'ARMATURE')
        for track in source.animation_data.nla_tracks:
            track.mute = True
        actions = {a.name: a for a in set(bpy.data.actions) - before_actions}
        mapping = bone_mapping()
        # Beta has an extra HeadTop_End; the source has an extra root bone.
        target = self.template
        target_rot = target.matrix_world.to_quaternion()
        target_inverse = target.matrix_world.to_3x3().inverted()
        rest_world = {b.name: (target.matrix_world @ b.matrix_local).to_quaternion() for b in target.data.bones}
        source_rest = {b.name: (source.matrix_world @ b.matrix_local).to_quaternion() for b in source.data.bones}
        target_hip = target.matrix_world @ target.data.bones[PREFIX + 'Hips'].head_local
        source_hip = source.matrix_world @ source.data.bones['pelvis'].head_local
        scale = target_hip.z / source_hip.z
        for name, definition in clips:
            clip = definition['clip']
            if clip not in actions:
                raise RuntimeError(f'GLB 缺少已登记动作 {clip}')
            action = actions[clip]
            source.animation_data.action = action
            source.animation_data.action_slot = action.slots[0]
            start, end = (round(v) for v in action.frame_range)
            poses, roots, metrics = [], [], []
            for frame in range(start, end + 1):
                scene.frame_set(frame)
                world_root = source.matrix_world @ source.pose.bones['root'].head
                root = world_root * scale
                desired = {}
                pose = {}
                for bone in target.data.bones:
                    source_name = mapping.get(bone.name)
                    if source_name:
                        rotation = (source.matrix_world @ source.pose.bones[source_name].matrix).to_quaternion()
                        world = rotation @ source_rest[source_name].inverted() @ rest_world[bone.name]
                        desired[bone.name] = target_rot.inverted() @ world
                    else:
                        desired[bone.name] = (desired[bone.parent.name] @ bone.parent.matrix_local.to_quaternion().inverted() @ bone.matrix_local.to_quaternion()
                                              if bone.parent else bone.matrix_local.to_quaternion())
                    rest = bone.matrix_local.to_quaternion()
                    parent = bone.parent
                    inherited = (desired[parent.name] @ parent.matrix_local.to_quaternion().inverted() @ rest) if parent else rest
                    local_rot = inherited.inverted() @ desired[bone.name]
                    location = Vector()
                    if bone.name == PREFIX + 'Hips':
                        current = source.matrix_world @ source.pose.bones['pelvis'].head
                        world_delta = (current - source_hip - world_root) * scale
                        # Root bone local location is expressed in its own rest axes.
                        location = rest.inverted() @ (target_inverse @ world_delta)
                    pose[bone.name] = (location, local_rot.normalized(), Vector((1, 1, 1)))
                poses.append(pose)
                roots.append(root)
                side = definition.get('side', 'left')
                suffix = 'l' if side == 'left' else 'r'
                def point(n):
                    return source.matrix_world @ source.pose.bones[n].head
                shoulder, elbow, wrist = [point(n + '_' + suffix) for n in ('upperarm', 'lowerarm', 'hand')]
                metrics.append({'frame': len(poses), 'elbow_deg': math.degrees((shoulder-elbow).angle(wrist-elbow)),
                                'forward_extension_m': -(wrist - point('pelvis')).y})
            self.poses[name], self.ends[name], self.root_motion[name] = poses, len(poses), roots
            self.analysis[name] = {'clip': clip, 'retarget_scale': scale, 'metrics': metrics,
                                   'mapped_bones': len(mapping.keys() & set(target.data.bones.keys()))}
        for obj in added:
            bpy.data.objects.remove(obj, do_unlink=True)

    def sample(self, name, frame, mirror=False):
        poses = self.poses[name]
        f = min(len(poses) - 1, max(0, frame - 1))
        first, fraction = int(f), f - int(f)
        a, b = poses[first], poses[min(first + 1, len(poses) - 1)]
        result = {key: (a[key][0].lerp(b[key][0], fraction), a[key][1].slerp(b[key][1], fraction), a[key][2].lerp(b[key][2], fraction)) for key in a}
        if mirror:
            reflection = Matrix.Diagonal((-1.0, 1.0, 1.0))
            # Reflect deformation in armature space, then recover local rotations
            # using each destination's rest basis. Left/right bone axes differ.
            global_rotations = {}
            for key in self.bone_order:
                parent = self.parents[key]
                rest = self.rest_rotations[key]
                inherited = global_rotations[parent] @ self.rest_rotations[parent].inverted() @ rest if parent else rest
                global_rotations[key] = inherited @ result[key][1]
            mirrored_global = {}
            mirrored = {}
            for key in self.bone_order:
                opposite = key.replace('Left','#').replace('Right','Left').replace('#','Right')
                source_rest = self.rest_rotations[opposite]
                rest = self.rest_rotations[key]
                deformation = (global_rotations[opposite] @ source_rest.inverted()).to_matrix()
                mirrored_global[key] = (reflection @ deformation @ reflection).to_quaternion() @ rest
                parent = self.parents[key]
                inherited = mirrored_global[parent] @ self.rest_rotations[parent].inverted() @ rest if parent else rest
                loc, _, scale = result[opposite]
                mirrored[key] = (rest.inverted() @ (reflection @ (source_rest @ loc)),
                                 inherited.inverted() @ mirrored_global[key], scale.copy())
            result = mirrored
        return result

    def root_at(self, name, frame):
        samples = self.root_motion[name]
        f = min(len(samples) - 1, max(0, frame - 1))
        first = int(f)
        return samples[first].lerp(samples[min(first + 1, len(samples) - 1)], f - first)
