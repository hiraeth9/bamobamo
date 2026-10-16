"""
白膜（Clay/Greybox）参考视频渲染脚本 — Mixamo 单文件版
用法：
  blender -b --python render_white_model.py -- \
      --fbx /root/rivermind-data/bamo/mixamo_fbx/basic/Idle.fbx \
      --output /root/rivermind-data/bamo/output/idle.mp4

说明：
  - Mixamo 动画 FBX 自带角色（Beta）+ 动画，单文件导入即可。
  - 白膜 = 统一浅灰 Principled BSDF + 三点光 + EEVEE。
  - 相机绕角色 360° 环绕，渲染帧范围自动跟随动画长度。
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from animation_sequence import sequence_actions

argv = sys.argv
if "--" in argv:
    argv = argv[argv.index("--") + 1:]
else:
    argv = []

p = argparse.ArgumentParser()
p.add_argument("--fbx", required=True, help="动作 FBX，多个用逗号分隔（如 a.fbx,b.fbx）")
p.add_argument("--output", required=True)
p.add_argument("--resolution", default="1280x720")
p.add_argument("--fps", type=int, default=30)
p.add_argument("--frames", type=int, default=0, help="0=跟随动画长度")
p.add_argument("--engine", default="BLENDER_EEVEE", choices=["BLENDER_EEVEE", "CYCLES"])
p.add_argument("--pitch_deg", type=float, default=0.0, help="俯仰角(度)：正=仰视，负=俯视")
p.add_argument("--distance_ratio", type=float, default=2.2, help="环绕半径=身高*ratio")
p.add_argument("--orbit_seconds", type=float, default=None, help="环绕360°时长(秒)，None=跟随动画(至少2秒)")
p.add_argument("--fov", type=float, default=50.0, help="相机视角(度)")
p.add_argument("--blend_frames", type=int, default=12, help="多动作之间的过渡帧数(默认12=0.4秒)")
p.add_argument("--camera_policy", choices=['fixed', 'orbit'], default='orbit')
p.add_argument('--scene-plan')
p.add_argument('--save-scene')
p.add_argument('--camera-plan', help='Saved single plan with executable fixed-camera placement')
args = p.parse_args(argv)

import bpy
from mathutils import Vector

_dbg = open("/tmp/render_debug.log", "w", buffering=1)
def _log(m):
    _dbg.write(m + "\n"); _dbg.flush()

_log("step0: 脚本开始")

# ---------- 1. 清空场景 ----------
bpy.ops.wm.read_factory_settings(use_empty=True)

# ---------- 2. 导入 Mixamo FBX（支持多动作，合并到同一角色）----------
fbx_list = [x.strip() for x in args.fbx.split(",") if x.strip()]
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bpy.ops.import_scene.fbx(filepath=os.path.join(PROJECT_DIR, fbx_list[0]) if not os.path.isabs(fbx_list[0]) else fbx_list[0])

scene = bpy.context.scene
armature = None
for o in scene.objects:
    if o.type == "ARMATURE":
        armature = o
        break

if armature is None:
    raise RuntimeError("FBX 中未找到 Armature")

# 收集所有动作（第一个 + 后续导入的）
actions = []
first_action = armature.animation_data.action if armature.animation_data else None
if first_action is not None:
    first_action.use_fake_user = True
    actions.append(first_action)

for fbx in fbx_list[1:]:
    path = os.path.join(PROJECT_DIR, fbx) if not os.path.isabs(fbx) else fbx
    existing = set(scene.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    new_objs = [o for o in scene.objects if o not in existing]
    new_arm = next((o for o in new_objs if o.type == "ARMATURE"), None)
    if new_arm is not None and new_arm.animation_data and new_arm.animation_data.action:
        act = new_arm.animation_data.action
        act.use_fake_user = True
        actions.append(act)
        _log(f"导入动作 {os.path.basename(fbx)}: 帧范围 {act.frame_range}")
    # 删除重复的 mesh + armature（动作已提取到 actions）
    for o in new_objs:
        if o.type in ("ARMATURE", "MESH"):
            bpy.data.objects.remove(o, do_unlink=True)

_log(f"共 {len(actions)} 个动作")

# 动画帧范围（多动作时取总时长）
frame_start = 1
if args.orbit_seconds is not None and args.orbit_seconds > 0:
    orbit_sec = min(args.orbit_seconds, 15.0)
    total_frames = int(orbit_sec * args.fps)
else:
    # 跟随动作总长（至少 2 秒）
    total_frames = sum(int(a.frame_range[1] - a.frame_range[0]) + 1 for a in actions)
    total_frames = max(total_frames, int(2.0 * args.fps))
frame_end = frame_start + total_frames - 1

if args.frames > 0:
    frame_end = min(frame_end, frame_start + args.frames - 1)

print(f"动作数: {len(actions)}, 总帧数: {frame_start}-{frame_end}")

# ---------- 3. 计算场景包围盒（含世界变换，处理 Mixamo 0.01 缩放）----------
def scene_bounds():
    xs, ys, zs = [], [], []
    for o in scene.objects:
        if o.type == "MESH":
            for c in o.bound_box:
                w = o.matrix_world @ Vector(c)
                xs.append(w.x); ys.append(w.y); zs.append(w.z)
    if not xs:
        return (0, 1, 0, 1, 0, 1)
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))

# 需先更新场景以获得正确 bound_box
bpy.context.view_layer.update()
b = scene_bounds()
cx, cy = (b[0]+b[1])/2, (b[2]+b[3])/2
height = max(b[5]-b[4], 0.01)
print(f"包围盒: x=[{b[0]:.2f},{b[1]:.2f}] y=[{b[2]:.2f},{b[3]:.2f}] z=[{b[4]:.2f},{b[5]:.2f}] 高={height:.2f}")

# ---------- 4. 白膜材质 ----------
clay_color = (0.85, 0.85, 0.87, 1.0)
clay_mat = bpy.data.materials.new("WhiteModelClay")
_log("step4a: 材质创建")
clay_mat.use_nodes = True
nodes = clay_mat.node_tree.nodes
links = clay_mat.node_tree.links
nodes.clear()
_log("step4b: use_nodes")
bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
_log("step4c: bsdf 节点")
bsdf.inputs["Base Color"].default_value = clay_color
bsdf.inputs["Roughness"].default_value = 0.6
out = nodes.new(type="ShaderNodeOutputMaterial")
links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
_log("step4d: 节点连接")

for o in scene.objects:
    if o.type == "MESH":
        if o.data.materials:
            o.data.materials.clear()
        o.data.materials.append(clay_mat)
_log("step4e: 材质已应用到所有 mesh")

# ---------- 5. 相机与灯光（自适应包围盒 + 参数化构图）----------
pitch = math.radians(args.pitch_deg)

# 相机用垂直 FOV 直接控制视角（保证人物完整入镜）
half_vfov = math.radians(args.fov) / 2.0

# 环绕半径：取"用户意图距离"和"保证人物完整入镜的最小距离"的较大值。
min_dist = (height * 0.85) / math.tan(half_vfov)
cam_r = max(height * args.distance_ratio, min_dist)

# 取景中心 = 角色几何中心（保证任何角度下人物都完整居中入镜）
center_z = b[4] + height * 0.5

# 相机高度：仰视(pitch>0)放低相机，俯视(pitch<0)抬高相机；不低于地面附近
cam_h = center_z - math.sin(pitch) * cam_r * 0.5
cam_h = max(cam_h, b[4] + height * 0.05)

# 三点光
def add_area_light(loc, energy, size, name):
    d = bpy.data.lights.new(name, "AREA")
    d.energy = energy
    d.size = size
    lo = bpy.data.objects.new(name, d)
    lo.location = loc
    scene.collection.objects.link(lo)

s = height
add_area_light((cx + s*2, cy - s*2, b[4] + s*2.5), s*s*80,  s*2,   "Key")
add_area_light((cx - s*2, cy - s*2, b[4] + s*1.2), s*s*40,  s*2.5, "Fill")
add_area_light((cx,        cy + s*2, b[4] + s*3),  s*s*60,  s*1.5, "Rim")

# 世界环境光
world = bpy.data.worlds.new("World")
_log("step5a: world 创建")
world.use_nodes = True
_bg = world.node_tree.nodes.get("Background")
_log(f"step5b: Background 节点存在={_bg is not None}, 节点名列表={[n.name for n in world.node_tree.nodes]}")
if _bg is not None:
    _bg.inputs[1].default_value = 0.25
scene.world = world
_log("step5c: world 已设置")

# 相机 + 环绕 Empty
cam_data = bpy.data.cameras.new("Cam")
cam_data.sensor_fit = "VERTICAL"          # 用垂直 FOV 控制
cam_data.angle = math.radians(args.fov)   # 垂直视角 = fov 度
cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam

empty = bpy.data.objects.new("Orbit", None)
empty.location = (cx, cy, center_z)   # 环绕中心 = 角色几何中心
scene.collection.objects.link(empty)
cam.parent = empty
# 相机相对环绕中心：水平距离 cam_r，垂直偏移由仰俯视角决定
cam.location = (0, -cam_r, cam_h - center_z)
cam.rotation_euler = (math.radians(90), 0, math.radians(-90))
# Track 约束看向环绕中心
tc = cam.constraints.new(type="TRACK_TO")
_log("step5d: 约束创建")
tc.target = empty
tc.track_axis = "TRACK_NEGATIVE_Z"
tc.up_axis = "UP_Y"
_log("step5e: 约束参数设置")

# ---------- 多动作 NLA 排布：跨轨道重叠，直接混合两个动作，避免淡回默认姿势 ----------
if len(actions) > 1:
    _log(f"多动作 NLA 排布: {len(actions)} 个动作, 总 {frame_end - frame_start + 1} 帧, blend={args.blend_frames}")
    strips = sequence_actions(armature, actions, frame_start, frame_end, args.blend_frames)
    for i, st in enumerate(strips):
        _log(f"  strip{i}: {st.action.name} 范围={st.frame_start:.2f}-{st.frame_end:.2f} repeat={st.repeat:.2f} blend_in={st.blend_in} blend_out={st.blend_out}")
    _log("多动作 NLA 排布完成")
else:
    # 单动作：循环填满
    _anim_len = max(1, int(actions[0].frame_range[1] - actions[0].frame_range[0]) + 1) if actions else 1
    if (frame_end - frame_start + 1) > _anim_len and actions:
        ad = armature.animation_data
        action = actions[0]
        track = ad.nla_tracks.new()
        track.name = "Loop"
        strip = track.strips.new(name="Loop", start=frame_start, action=action)
        strip.repeat = int((frame_end - frame_start + 1) // _anim_len) + 1
        ad.action = None
        _log(f"动画循环: {_anim_len}帧 -> 循环填满 {frame_end - frame_start + 1} 帧 (repeat={strip.repeat})")

# 环绕动画：empty 绕 Z 轴 360°（覆盖整个时长）
scene.frame_start = frame_start
scene.frame_end = frame_end
empty.rotation_euler = (0, 0, 0)
if args.camera_policy == 'orbit':
    empty.keyframe_insert(data_path="rotation_euler", frame=frame_start)
    empty.rotation_euler = (0, 0, math.radians(360))
    empty.keyframe_insert(data_path="rotation_euler", frame=frame_end)
# Blender 5.x 中 Action 不再有 .fcurves，跳过手动设置 LINEAR 插值（用默认）
_log(f"相机参数: pitch={args.pitch_deg}° ratio={args.distance_ratio} fov={args.fov} 帧范围 {frame_start}-{frame_end}")

# ---------- 6. 渲染设置 ----------
_log("step6: 渲染设置开始")
res = args.resolution.split("x")
scene.render.resolution_x = int(res[0])
scene.render.resolution_y = int(res[1])
scene.render.fps = args.fps
scene.render.engine = args.engine
_log("step6a: 分辨率/引擎")

if args.scene_plan or args.camera_plan:
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from scene_plan import validate_scene
    from scene_objects import SceneObjects,scene_quality
    from duel_plan import atomic_json
    from duel_scene import setup_camera,setup_stage,evaluated_corners
    frames_in_scene=frame_end-frame_start+1
    environment=None
    if args.scene_plan:
        raw=json.loads(Path(args.scene_plan).read_text())
        environment=validate_scene(raw,raw['job_id'],1,frames_in_scene/args.fps)
        if environment['interactions']: raise ValueError('物品交互须使用交互执行器')
    actor_meshes=[o for o in scene.objects if o.type=='MESH']
    setup_stage()
    props=SceneObjects(environment) if environment else None
    actors={'A':SimpleNamespace(arm=armature,root=armature,meshes=actor_meshes)}
    focus_meshes=actor_meshes+(props.focus_meshes() if props else [])
    if args.camera_plan:
        from single_plan import validate_single
        from reference_camera import setup_reference_camera, verify_reference_camera
        raw_single=json.loads(Path(args.camera_plan).read_text())
        camera_spec=validate_single(raw_single,raw_single['job_id'])['camera']
        camera_bounds=setup_reference_camera(focus_meshes,camera_spec,actors,props)
        camera_quality=verify_reference_camera(focus_meshes,camera_spec,actors,props)
    else:
        camera_bounds=setup_camera(focus_meshes,{'camera':{'azimuth_deg':-20}},actors)
        camera_quality=None
    quality=scene_quality(props,{'A':armature},frames_in_scene) if props else {'passed':True}
    if camera_quality is not None:
        quality['camera_placement']=camera_quality
        quality['passed'] &= camera_quality['passed']
    quality['camera_bounds']=camera_bounds
    quality['frame_count']=frames_in_scene
    quality['kind']='single_scene' if props else 'single_camera'
    import hashlib
    source_names=['render_white_model.py','single_plan.py','camera_plan.py']
    if args.camera_plan:source_names.append('reference_camera.py')
    quality['engine_sources_sha256']={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in source_names}
    plan_folder=Path(args.camera_plan or args.scene_plan).parent
    atomic_json(plan_folder/'quality.json',quality)
    if not quality['passed']: raise RuntimeError('单人物品场景检查失败，查看quality.json')
    scene.frame_set(1)
    if args.save_scene:
        bpy.ops.wm.save_as_mainfile(filepath=args.save_scene)
        print('SINGLE_SCENE_SAVED '+args.save_scene,flush=True)
        sys.exit(0)

if args.save_scene:
    scene.frame_set(frame_start)
    bpy.ops.wm.save_as_mainfile(filepath=args.save_scene)
    print('SINGLE_SCENE_SAVED '+args.save_scene, flush=True)
    sys.exit(0)

# Blender 5.x 已移除内置 FFMPEG 视频输出，改为渲染 PNG 序列 + 外部 ffmpeg 编码
outdir = os.path.dirname(os.path.abspath(args.output))
base = os.path.splitext(os.path.basename(args.output))[0]
os.makedirs(outdir, exist_ok=True)
frames_dir = os.path.join(outdir, base + "_frames")
os.makedirs(frames_dir, exist_ok=True)

scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.filepath = os.path.join(frames_dir, base + "_")
_log(f"step6b: PNG 序列输出到 {frames_dir}")

# ---------- 7. 渲染动画 ----------
_log(f"step7: 开始渲染 {frame_end - frame_start + 1} 帧 @ {args.fps}fps -> PNG 序列")
print(f"开始渲染: {frame_end - frame_start + 1} 帧 @ {args.fps}fps")
bpy.ops.render.render(animation=True)
_log("step7b: 渲染完成，开始 ffmpeg 编码")

# ---------- 8. ffmpeg 编码 mp4 ----------
import subprocess
ffmpeg_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ffmpeg")
if not os.path.exists(ffmpeg_bin):
    ffmpeg_bin = "ffmpeg"
cmd = [
    ffmpeg_bin, "-y",
    "-framerate", str(args.fps),
    "-i", os.path.join(frames_dir, base + "_%04d.png"),
    "-c:v", "libx264", "-threads", "8", "-pix_fmt", "yuv420p", "-crf", "18",
    args.output,
]
_log(f"step8: {cmd}")
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0:
    _log(f"ffmpeg 失败: {r.stderr[-800:]}")
    raise RuntimeError(f"FFMPEG_FAILED {r.stderr[-500:]}")
else:
    _log(f"RENDER_DONE {args.output}")
    print(f"RENDER_DONE {args.output}")

