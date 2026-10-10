# -*- coding: utf-8 -*-
"""
scene.json 编排引擎 —— 把声明式分镜转换成 Blender bpy 代码

这是整个白膜预演项目的核心。它把「谁在何时做什么动作 + 相机怎么走」
的结构化描述，转换成 Blender 能执行的完整场景。

架构：
  scene.json（声明式分镜）
      ↓ parse() 解析 + 校验
  编排指令（角色/动作/摆位/相机/渲染）
      ↓ generate_bpy() 生成 Python 代码
  bpy 代码字符串 → execute_code → Blender 执行 → 渲染出片

用法：
  engine = SceneEngine()
  bpy_code = engine.generate_bpy(scene_dict)
  # 然后通过 socket execute_code 发给 Blender

scene.json 结构（与架构文档一致）：
  {
    "meta": {"name", "fps", "resolution", "duration"},
    "characters": [{"id", "asset", "position", "rotation"}],
    "tracks": [{"char", "clips": [{"action", "start", "end"}]}],
    "camera": {"clips": [{"type", "target", "start", "end", ...}]}
  }
"""

import json
import os
import posixpath as _pp  # 强制 Linux 风格路径（Blender 在 Linux 服务器上）

# 动作名 → 文件名映射（Mixamo 动作库，去掉 .fbx 后缀即动作名）
# 关键：动作文件名（去空格/下划线）就是 Action 在 Blender 里的引用名
ACTIONS = {
    # 攻击
    "punch": "Elbow_Punch",
    "elbow_punch": "Elbow_Punch",
    "combo_punch": "Combo_Punch",
    "cross_punch": "Cross_Punch",
    "kick": "Flying_Kick",
    "flip_kick": "Flip_Kick",
    "drop_kick": "Drop_Kick",
    # 被击/反应
    "get_hit": "Getting_Hit",
    "hit_reaction": "Hit_Reaction",
    "get_hit_backwards": "Getting_Hit_Backwards",
    "head_hit": "Head_Hit",
    "fall": "Falling",
    "fall_down": "Falling_Down",
    "fall_over": "Fall_Over",
    # 防御
    "block": "Block",
    "blocking": "Blocking",
    "dodge": "Blocking_Attack",
    "inward_block": "Inward_Block",
    "outward_block": "Outward_Block",
    # 位移
    "walk": "Crouch_Walk",
    "run": "Fast_Run",
    "idle": "Bouncing_Fight_Idle",
    "fight_idle": "Bouncing_Fight_Idle",
    # 待机
    "stand_idle": "Box_Idle",
}

# 动作帧率/时长（帧数，Mixamo 默认 24fps）
# 从之前实测：Elbow_Punch=53, Getting_Hit=139。这里放已知的，未知的运行时查


class SceneEngine:
    """scene.json → bpy 代码 的转换引擎"""

    def __init__(self, anim_dir="/mnt/animations", char_fbx="Reloading.fbx",
                 out_dir="/root/render_out"):
        self.anim_dir = anim_dir
        self.char_fbx = char_fbx
        self.out_dir = out_dir

    def _resolve_action(self, action_name):
        """把动作语义名解析成 Mixamo 文件名"""
        if action_name in ACTIONS:
            return ACTIONS[action_name]
        # 直接当文件名用（用户可能直接给 Mixamo 名）
        return action_name

    def _resolve_action_fbx(self, action_name):
        """解析成完整 FBX 路径，找不到返回 None"""
        fname = self._resolve_action(action_name)
        path = _pp.join(self.anim_dir, fname + ".fbx")
        return path if os.path.exists(path) else None

    def parse(self, scene_dict):
        """校验并规范化 scene.json"""
        if "meta" not in scene_dict:
            raise ValueError("scene.json 缺少 meta")
        meta = scene_dict["meta"]
        meta.setdefault("fps", 24)
        meta.setdefault("resolution", [864, 480])
        meta.setdefault("duration", 3)
        scene_dict.setdefault("characters", [])
        scene_dict.setdefault("tracks", [])
        scene_dict.setdefault("camera", {"clips": []})
        return scene_dict

    def generate_bpy(self, scene_dict):
        """生成完整的 bpy 代码字符串"""
        scene = self.parse(scene_dict)
        meta = scene["meta"]
        fps = meta["fps"]
        res_x, res_y = meta["resolution"]
        duration = meta["duration"]
        total_frames = int(duration * fps)
        characters = scene["characters"]
        tracks = scene["tracks"]
        camera = scene["camera"]

        # 收集所有用到的动作，提前验证
        used_actions = set()
        for t in tracks:
            for clip in t.get("clips", []):
                used_actions.add(clip["action"])

        # 生成代码片段（用列表拼接）
        lines = []
        A = lines.append

        A("import bpy")
        A("import math")
        A("import os")
        A("from mathutils import Vector")
        A("")
        A("scene = bpy.context.scene")
        A("")
        A("# ===== 清空场景 =====")
        A("bpy.ops.object.select_all(action='SELECT')")
        A("bpy.ops.object.delete(use_global=False)")
        A("for a in list(bpy.data.actions): bpy.data.actions.remove(a)")
        A("for m in list(bpy.data.meshes):\n    if m.users == 0: bpy.data.meshes.remove(m)")
        A("for m in list(bpy.data.materials):\n    if m.users == 0: bpy.data.materials.remove(m)")
        A("for arm in list(bpy.data.armatures): bpy.data.armatures.remove(arm)")
        A("")
        A("# ===== 白膜材质 =====")
        A("white_mat = bpy.data.materials.new('WhiteBlockout')")
        A("white_mat.use_nodes = True")
        A("bsdf = white_mat.node_tree.nodes.get('Principled BSDF')")
        A("bsdf.inputs['Base Color'].default_value = (0.72, 0.72, 0.74, 1.0)")
        A("bsdf.inputs['Roughness'].default_value = 0.85")
        A("")
        A("# ===== 导入角色 =====")
        A("def import_mannequin(prefix):")
        A("    bpy.ops.import_scene.fbx(filepath='%s')" % _pp.join(self.anim_dir, self.char_fbx))
        A("    arm = [o for o in bpy.data.objects if o.type=='ARMATURE'][-1]")
        A("    arm.name = prefix + '_Armature'")
        A("    arm.data.name = prefix + '_ArmatureData'")
        A("    meshes = [o for o in bpy.data.objects if o.type=='MESH' and not o.name.startswith(('A_','B_','C_','D_'))]")
        A("    for o in meshes:")
        A("        o.name = prefix + '_' + o.name")
        A("        o.data.materials.clear()")
        A("        o.data.materials.append(white_mat)")
        A("    for o in list(bpy.data.objects):")
        A("        if o.type == 'CAMERA': bpy.data.objects.remove(o)")
        A("    return arm")
        A("")
        A("# ===== 加载动作到角色 =====")
        A("def load_action(fbx_path, target_arm):")
        A("    before = set(bpy.data.actions)")
        A("    bpy.ops.import_scene.fbx(filepath=fbx_path)")
        A("    for o in list(bpy.data.objects):")
        A("        if o.type == 'ARMATURE' and not o.name.endswith(('_Armature',)):")
        A("            if o.name not in [a.name for a in armatures]:")
        A("                bpy.data.objects.remove(o)")
        A("    new_actions = [a for a in bpy.data.actions if a not in before and 'CameraAction' not in a.name]")
        A("    act = new_actions[0]")
        A("    if target_arm.animation_data is None: target_arm.animation_data_create()")
        A("    target_arm.animation_data.action = act")
        A("    return act")
        A("")
        A("armatures = []")
        A("char_arms = {}")
        A("")

        # 生成每个角色
        for i, ch in enumerate(characters):
            cid = ch["id"]
            A("arm = import_mannequin('%s')" % cid)
            A("arm.location = (%s, %s, %s)" % tuple(ch.get("position", [0, 0, 0])))
            rot = ch.get("rotation", [0, 0, 0])
            A("arm.rotation_euler = (math.radians(%s), math.radians(%s), math.radians(%s))" % tuple(rot))
            A("if arm.animation_data: arm.animation_data.action = None")
            A("armatures.append(arm)")
            A("char_arms['%s'] = arm" % cid)
            A("")

        # 加载动作（按 track 分配）
        A("# ===== 挂载动作 =====")
        action_map = {}  # char_id -> 动作语义名（多 clip 时取第一个，简化）
        for t in tracks:
            cid = t["char"]
            clips = t.get("clips", [])
            if clips:
                # 简化：每个角色先只支持一个主动作（多 clip 时间轴编排后续做）
                action_name = clips[0]["action"]
                fname = self._resolve_action(action_name)
                A("# 角色 %s 的动作: %s" % (cid, action_name))
                A("act = load_action('%s', char_arms['%s'])" % (_pp.join(self.anim_dir, fname + ".fbx"), cid))
                action_map[cid] = action_name
        A("")

        # ===== 相机 =====
        A("# ===== 相机 =====")
        cam_clips = camera.get("clips", [])
        cam_pos = [0, -5.5, 1.6]
        cam_target = [0, 0, 0.95]
        cam_lens = 35
        if cam_clips:
            # 取第一个 clip 的位置参数（简化）
            first = cam_clips[0]
            if first.get("type") == "tracking" and first.get("target"):
                # 跟随目标角色
                tid = first["target"]
                A("cam_target_char = char_arms['%s']" % tid)
                cam_target = None  # 用角色位置
            cam_lens = first.get("lens", cam_lens)

        A("bpy.ops.object.camera_add(location=(%s, %s, %s))" % tuple(cam_pos))
        A("cam = bpy.context.object")
        A("cam.rotation_mode = 'XYZ'")
        A("cam.rotation_euler = (math.radians(90), 0, 0)")
        A("cam.data.lens = %s" % cam_lens)
        A("bpy.ops.object.empty_add(location=(%s, %s, %s))" % tuple(cam_target if cam_target else [0,0,0.95]))
        A("target = bpy.context.object")
        A("target.name = 'AimTarget'")
        A("tc = cam.constraints.new(type='TRACK_TO')")
        A("tc.target = target")
        A("tc.track_axis = 'TRACK_NEGATIVE_Z'")
        A("tc.up_axis = 'UP_Y'")
        A("scene.camera = cam")
        A("")

        # ===== 灯光 + 地面 =====
        A("# ===== 灯光 + 地面 =====")
        A("bpy.ops.object.light_add(type='SUN', location=(2, -3, 5))")
        A("sun = bpy.context.object")
        A("sun.data.energy = 3")
        A("sun.rotation_euler = (math.radians(45), 0, math.radians(15))")
        A("bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))")
        A("ground = bpy.context.object")
        A("ground.name = 'Ground'")
        A("gmat = bpy.data.materials.new('GroundMat')")
        A("gmat.use_nodes = True")
        A("gb = gmat.node_tree.nodes.get('Principled BSDF')")
        A("gb.inputs['Base Color'].default_value = (0.28, 0.28, 0.30, 1.0)")
        A("gb.inputs['Roughness'].default_value = 1.0")
        A("ground.data.materials.append(gmat)")
        A("")

        # ===== 渲染设置 =====
        A("# ===== 渲染设置 =====")
        A("scene.render.engine = 'BLENDER_WORKBENCH'")
        A("scene.render.resolution_x = %d" % res_x)
        A("scene.render.resolution_y = %d" % res_y)
        A("scene.render.fps = %d" % fps)
        A("scene.frame_start = 1")
        A("scene.frame_end = %d" % total_frames)
        A("scene.render.image_settings.file_format = 'PNG'")
        A("os.makedirs('%s', exist_ok=True)" % self.out_dir)
        A("scene.render.filepath = '%s/out_'" % self.out_dir)
        A("scene.frame_set(1)")
        A("")
        A("print('SCENE_ENGINE_DONE')")

        return "\n".join(lines)

    def render_bpy(self):
        """渲染指令（单独一段，便于分阶段执行）"""
        return "import bpy\nscene = bpy.context.scene\nscene.frame_set(1)\nbpy.ops.render.render(animation=True)\nprint('RENDER_DONE')"


# ===== 独立运行：读取 scene.json 并打印生成的 bpy 代码 =====
if __name__ == "__main__":
    import sys
    json_path = sys.argv[1] if len(sys.argv) > 1 else "scene.json"
    with open(json_path, "r", encoding="utf-8") as f:
        scene_dict = json.load(f)
    engine = SceneEngine()
    code = engine.generate_bpy(scene_dict)
    print(code)
