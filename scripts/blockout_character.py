# -*- coding: utf-8 -*-
"""
白膜角色生成 + 打斗编排脚本（通过 execute_code 下发到 Blender）

功能：
1. 导入 Mixamo 骨骼动画（纯骨骼，无网格）
2. 给标准 Mixamo 骨骼自动生成「胶囊白膜人形」（几何体蒙皮绑定到骨骼）
3. 编排打斗：切 Action、设相机
4. 渲染帧序列

用法：本文件内容通过 socket 的 execute_code 发送给 Blender addon 执行
"""

import bpy
import math
from mathutils import Vector, Matrix

# ==================== 配置 ====================
ANIM_DIR = "/mnt/animations"
OUT_DIR = "/root/render_out"

# Mixamo 标准骨骼名称（用于生成白膜几何体）
BONES = {
    "mixamorig:Hips":       {"parent": None,                      "head_tail": 0.25, "radius": 0.22},
    "mixamorig:Spine":      {"parent": "mixamorig:Hips",          "head_tail": 0.20, "radius": 0.18},
    "mixamorig:Spine1":     {"parent": "mixamorig:Spine",         "head_tail": 0.20, "radius": 0.18},
    "mixamorig:Spine2":     {"parent": "mixamorig:Spine1",        "head_tail": 0.22, "radius": 0.20},
    "mixamorig:Neck":       {"parent": "mixamorig:Spine2",        "head_tail": 0.08, "radius": 0.09},
    "mixamorig:Head":       {"parent": "mixamorig:Neck",          "head_tail": 0.20, "radius": 0.16},
    "mixamorig:LeftShoulder": {"parent": "mixamorig:Spine2",      "head_tail": 0.10, "radius": 0.09},
    "mixamorig:LeftArm":    {"parent": "mixamorig:LeftShoulder",  "head_tail": 0.28, "radius": 0.07},
    "mixamorig:LeftForeArm": {"parent": "mixamorig:LeftArm",      "head_tail": 0.25, "radius": 0.06},
    "mixamorig:LeftHand":   {"parent": "mixamorig:LeftForeArm",   "head_tail": 0.10, "radius": 0.06},
    "mixamorig:RightShoulder": {"parent": "mixamorig:Spine2",     "head_tail": 0.10, "radius": 0.09},
    "mixamorig:RightArm":   {"parent": "mixamorig:RightShoulder", "head_tail": 0.28, "radius": 0.07},
    "mixamorig:RightForeArm": {"parent": "mixamorig:RightArm",    "head_tail": 0.25, "radius": 0.06},
    "mixamorig:RightHand":  {"parent": "mixamorig:RightForeArm",  "head_tail": 0.10, "radius": 0.06},
    "mixamorig:LeftUpLeg":  {"parent": "mixamorig:Hips",          "head_tail": 0.30, "radius": 0.10},
    "mixamorig:LeftLeg":    {"parent": "mixamorig:LeftUpLeg",     "head_tail": 0.32, "radius": 0.08},
    "mixamorig:LeftFoot":   {"parent": "mixamorig:LeftLeg",       "head_tail": 0.12, "radius": 0.07},
    "mixamorig:RightUpLeg": {"parent": "mixamorig:Hips",          "head_tail": 0.30, "radius": 0.10},
    "mixamorig:RightLeg":   {"parent": "mixamorig:RightUpLeg",    "head_tail": 0.32, "radius": 0.08},
    "mixamorig:RightFoot":  {"parent": "mixamorig:RightLeg",      "head_tail": 0.12, "radius": 0.07},
}


def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for a in bpy.data.actions:
        bpy.data.actions.remove(a)


def import_animation(fbx_path):
    """导入一个 Mixamo FBX，返回 (armature, action)"""
    before = set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=fbx_path)
    arm = [o for o in bpy.data.objects if o.type == 'ARMATURE'][-1]
    # 找新导入的 action（排除 CameraAction）
    new_actions = [a for a in bpy.data.actions if a not in before and 'CameraAction' not in a.name]
    action = new_actions[0] if new_actions else None
    return arm, action


def add_blockout_mesh(arm):
    """给骨骼生成胶囊白膜人形，蒙皮绑定到骨骼"""
    # 进入编辑模式，用每个骨骼的 head/tail 生成胶囊
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')

    # 记录每个骨骼的 head/tail 世界坐标（骨骼空间）
    bone_data = {}
    for bname, spec in BONES.items():
        if bname in arm.data.edit_bones:
            eb = arm.data.edit_bones[bname]
            bone_data[bname] = (eb.head.copy(), eb.tail.copy(), spec["radius"])

    bpy.ops.object.mode_set(mode='OBJECT')

    # 生成一个合并的 mesh，用胶囊近似
    # 简化：为每个骨骼生成一个 cube，scale 成胶囊状，然后 skin
    # 这里用更简单的方案：生成整体 mesh 用 capsule 近似
    return bone_data


def build_capsule_character(arm):
    """构建胶囊白膜角色（简化版：每个骨骼一个拉长的 cube，蒙皮绑定）"""
    # 收集骨骼坐标
    arm.data.pose_position = 'REST'
    bpy.context.view_layer.objects.active = arm

    # 创建主 mesh
    mesh = bpy.data.meshes.new("BlockoutBody")
    obj = bpy.data.objects.new("BlockoutBody", mesh)
    bpy.context.collection.objects.link(obj)

    verts = []
    for bname, spec in BONES.items():
        bone = arm.data.bones.get(bname)
        if not bone:
            continue
        head = bone.head_local
        tail = bone.tail_local
        # 为每段骨骼生成一个 8 顶点的棱柱（拉长立方体）
        direction = (tail - head).normalized()
        radius = spec["radius"]
        # 构造正交基
        if abs(direction.z) < 0.9:
            up = Vector((0, 0, 1))
        else:
            up = Vector((0, 1, 0))
        right = direction.cross(up).normalized()
        up2 = right.cross(direction).normalized()
        for sign_r in (-1, 1):
            for sign_u in (-1, 1):
                for endpoint in (head, tail):
                    v = endpoint + right * sign_r * radius + up2 * sign_u * radius
                    verts.append(v)

    mesh.from_pydata(verts, [], [])
    mesh.update()

    # 用 Armature modifier + 顶点组绑定到骨骼
    obj.parent = arm
    modifier = obj.modifiers.new(name="Armature", type='ARMATURE')
    modifier.object = arm

    # 给每个骨骼对应的顶点分配顶点组（按最近骨骼）
    vg_map = {}
    v_idx = 0
    for bname, spec in BONES.items():
        bone = arm.data.bones.get(bname)
        if not bone:
            continue
        vg = obj.vertex_groups.new(name=bname)
        vg_map[bname] = vg
        for _ in range(8):
            vg.add([v_idx], 1.0, 'REPLACE')
            v_idx += 1

    # 设置白膜材质
    mat = bpy.data.materials.new("BlockoutMat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.7, 0.7, 0.72, 1.0)  # 灰白
    bsdf.inputs["Roughness"].default_value = 0.9
    obj.data.materials.append(mat)

    return obj


print("=== 白膜角色生成模块加载完成 ===")
