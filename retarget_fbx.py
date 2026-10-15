"""把非 mixamorig 骨架的 FBX 重定向成 mixamorig 命名 FBX。

用法:
    blender --background --python retarget_fbx.py -- src.fbx movella|rokoko out.fbx

复用 duel_motion_sources.py 中 _load_glb 的世界空间旋转映射数学。
"""
import bpy, sys
from mathutils import Matrix, Quaternion, Vector

PREFIX = 'mixamorig:'
TEMPLATE = '/root/rivermind-data/bamo/mixamo_fbx/combat_melee/Mma Idle.fbx'
TARGET_FPS = 30

# 源骨骼 -> mixamorig 目标骨骼
MOV_MAP = {
    'Hips': 'mixamorig:Hips', 'Spine': 'mixamorig:Spine',
    'Spine1': 'mixamorig:Spine1', 'Spine2': 'mixamorig:Spine2',
    'Spine3': 'mixamorig:Neck', 'Neck': 'mixamorig:Head',
    'LeftShoulder': 'mixamorig:LeftShoulder', 'LeftArm': 'mixamorig:LeftArm',
    'LeftForeArm': 'mixamorig:LeftForeArm', 'LeftHand': 'mixamorig:LeftHand',
    'LeftUpLeg': 'mixamorig:LeftUpLeg', 'LeftLeg': 'mixamorig:LeftLeg',
    'LeftFoot': 'mixamorig:LeftFoot', 'LeftToeBase': 'mixamorig:LeftToeBase',
    'RightShoulder': 'mixamorig:RightShoulder', 'RightArm': 'mixamorig:RightArm',
    'RightForeArm': 'mixamorig:RightForeArm', 'RightHand': 'mixamorig:RightHand',
    'RightUpLeg': 'mixamorig:RightUpLeg', 'RightLeg': 'mixamorig:RightLeg',
    'RightFoot': 'mixamorig:RightFoot', 'RightToeBase': 'mixamorig:RightToeBase',
}

ROK_MAP = {
    'Hips': 'mixamorig:Hips',
    'Spine1': 'mixamorig:Spine', 'Spine2': 'mixamorig:Spine1',
    'Spine3': 'mixamorig:Spine2', 'Spine4': 'mixamorig:Neck',
    'Neck': 'mixamorig:Head',
    'LeftShoulder': 'mixamorig:LeftShoulder', 'LeftArm': 'mixamorig:LeftArm',
    'LeftForeArm': 'mixamorig:LeftForeArm', 'LeftHand': 'mixamorig:LeftHand',
    'LeftThigh': 'mixamorig:LeftUpLeg', 'LeftShin': 'mixamorig:LeftLeg',
    'LeftFoot': 'mixamorig:LeftFoot', 'LeftToe': 'mixamorig:LeftToeBase',
    'RightShoulder': 'mixamorig:RightShoulder', 'RightArm': 'mixamorig:RightArm',
    'RightForeArm': 'mixamorig:RightForeArm', 'RightHand': 'mixamorig:RightHand',
    'RightThigh': 'mixamorig:RightUpLeg', 'RightShin': 'mixamorig:RightLeg',
    'RightFoot': 'mixamorig:RightFoot', 'RightToe': 'mixamorig:RightToeBase',
}


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    for ar in list(bpy.data.armatures):
        bpy.data.armatures.remove(ar)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)


def retarget(src_fbx, src_map, out_fbx):
    clear()
    bpy.context.scene.render.fps = TARGET_FPS

    # 导入 mixamorig 模板
    bpy.ops.import_scene.fbx(filepath=TEMPLATE)
    target = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    for o in [o for o in bpy.data.objects if o.type == 'MESH']:
        bpy.data.objects.remove(o, do_unlink=True)

    # 导入源 FBX
    bpy.ops.import_scene.fbx(filepath=src_fbx)
    source = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and o != target)
    for o in [o for o in bpy.data.objects if o.type == 'MESH']:
        bpy.data.objects.remove(o, do_unlink=True)

    src_fps = bpy.context.scene.render.fps  # FBX 导入后 scene fps = 源 fps
    inv_map = {v: k for k, v in src_map.items()}  # mixamorig -> 源

    target_rot = target.matrix_world.to_quaternion()
    target_inverse = target.matrix_world.to_3x3().inverted()
    rest_world = {b.name: (target.matrix_world @ b.matrix_local).to_quaternion()
                  for b in target.data.bones}
    source_rest = {b.name: (source.matrix_world @ b.matrix_local).to_quaternion()
                   for b in source.data.bones}
    target_hip = target.matrix_world @ target.data.bones[PREFIX + 'Hips'].head_local
    source_hip = source.matrix_world @ source.data.bones['Hips'].head_local
    scale = target_hip.z / source_hip.z

    action = source.animation_data.action
    start, end = int(action.frame_range[0]), int(action.frame_range[1])
    step = src_fps / TARGET_FPS

    # 清除 target 默认动作
    target.animation_data_create()
    if target.animation_data.action:
        target.animation_data.action = None

    scene = bpy.context.scene
    origin = None
    out_frame = 1
    src_frame = float(start)
    while src_frame <= end:
        scene.frame_set(int(round(src_frame)))
        src_hip_pos = source.matrix_world @ source.pose.bones['Hips'].head
        if origin is None:
            origin = src_hip_pos.copy()
        world_delta = (src_hip_pos - origin) * scale

        desired = {}
        for bone in target.data.bones:
            src_name = inv_map.get(bone.name)
            if src_name and src_name in source.pose.bones:
                rotation = (source.matrix_world @ source.pose.bones[src_name].matrix).to_quaternion()
                world = rotation @ source_rest[src_name].inverted() @ rest_world[bone.name]
                desired[bone.name] = (target_rot.inverted() @ world).normalized()
            else:
                if bone.parent:
                    desired[bone.name] = (desired[bone.parent.name]
                                          @ bone.parent.matrix_local.to_quaternion().inverted()
                                          @ bone.matrix_local.to_quaternion()).normalized()
                else:
                    desired[bone.name] = bone.matrix_local.to_quaternion()

        for bone in target.data.bones:
            rest = bone.matrix_local.to_quaternion()
            parent = bone.parent
            inherited = (desired[parent.name] @ parent.matrix_local.to_quaternion().inverted() @ rest) if parent else rest
            local_rot = (inherited.inverted() @ desired[bone.name]).normalized()
            pb = target.pose.bones[bone.name]
            pb.rotation_quaternion = local_rot
            pb.keyframe_insert(data_path='rotation_quaternion', frame=out_frame)
            if bone.name == PREFIX + 'Hips':
                loc = rest.inverted() @ (target_inverse @ world_delta)
                pb.location = loc
                pb.keyframe_insert(data_path='location', frame=out_frame)

        out_frame += 1
        src_frame += step

    bpy.context.scene.render.fps = TARGET_FPS
    bpy.context.scene.frame_end = out_frame - 1
    bpy.ops.export_scene.fbx(filepath=out_fbx, use_selection=False)
    print(f'RETARGET_OK src={src_fbx} out={out_fbx} frames={out_frame-1} src_fps={src_fps} scale={scale:.3f}')


if __name__ == '__main__':
    args = [a for a in sys.argv if a != '--']
    src_fbx, map_name, out_fbx = args[-3], args[-2], args[-1]
    m = MOV_MAP if map_name == 'movella' else ROK_MAP
    retarget(src_fbx, m, out_fbx)
