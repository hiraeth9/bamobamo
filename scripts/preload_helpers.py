"""
白膜视频生成 —— Blender 预置 helper 函数库
通过 start_server.py 导入后，把高层函数挂到 bpy 模块上，
让 Hermes 通过 MCP 的 execute_code 调用 bpy.wm_* 函数，避免手写易错的底层 Blender API。

这些 helper 把"场景搭建/材质/灯光/相机/渲染"封装成带明确参数的函数，
Hermes 只需根据提示词解析出构图参数（视角/距离/环绕速度/时长）传进来即可。
"""
import math
import os
import subprocess

import bpy
from mathutils import Vector

PROJECT = "/root/rivermind-data/bamo"

# 内部状态：记录 Hermes 本次任务的决策（fbx 路径 + 构图参数），渲染时自动读取
_STATE = {
    "fbx": [],           # 动作列表（支持多动作）
    "pitch_deg": 0.0,
    "distance_ratio": 2.2,
    "orbit_seconds": None,
    "fov": 50.0,
}


def _log(msg):
    with open("/tmp/helper_debug.log", "a") as f:
        f.write(msg + "\n")


# ---------------------------------------------------------------------------
# 1. 清空场景
# ---------------------------------------------------------------------------
def wm_clear():
    """清空整个场景（回到空场景）。"""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _STATE.update({"fbx": [], "pitch_deg": 0.0, "distance_ratio": 2.2,
                   "orbit_seconds": None, "fov": 50.0})
    return {"ok": True, "message": "场景已清空"}


# ---------------------------------------------------------------------------
# 2. 导入动作 FBX（支持单个或多个，多个用逗号分隔）
# ---------------------------------------------------------------------------
def wm_import(fbx_rel_path):
    """
    导入 Mixamo 动作 FBX。fbx_rel_path 为相对项目根的路径。
    支持多个动作（逗号分隔，如 "a.fbx,b.fbx"），用于制作多动作连贯视频。
    """
    fbx_list = [x.strip() for x in fbx_rel_path.split(",") if x.strip()]
    if not fbx_list:
        return {"ok": False, "message": "未指定 FBX 路径"}

    for f in fbx_list:
        path = os.path.join(PROJECT, f)
        if not os.path.exists(path):
            return {"ok": False, "message": f"文件不存在: {path}"}

    # 导入第一个（保留角色）
    bpy.ops.import_scene.fbx(filepath=os.path.join(PROJECT, fbx_list[0]))
    bpy.context.view_layer.update()

    # 导入后续动作：只提取 action，删除重复 mesh/armature
    for f in fbx_list[1:]:
        existing = set(bpy.context.scene.objects)
        bpy.ops.import_scene.fbx(filepath=os.path.join(PROJECT, f))
        new_objs = [o for o in bpy.context.scene.objects if o not in existing]
        new_arm = next((o for o in new_objs if o.type == "ARMATURE"), None)
        if new_arm is not None and new_arm.animation_data and new_arm.animation_data.action:
            new_arm.animation_data.action.use_fake_user = True
        for o in new_objs:
            if o.type in ("ARMATURE", "MESH"):
                bpy.data.objects.remove(o, do_unlink=True)

    _STATE["fbx"] = fbx_list
    objs = [o.name for o in bpy.context.scene.objects if o.type in ("ARMATURE", "MESH")]
    return {"ok": True, "message": f"已导入 {len(fbx_list)} 个动作，对象: {objs}"}


# ---------------------------------------------------------------------------
# 包围盒计算（供相机/灯光使用）
# ---------------------------------------------------------------------------
def _bounds():
    xs, ys, zs = [], [], []
    for o in bpy.context.scene.objects:
        if o.type == "MESH":
            for c in o.bound_box:
                w = o.matrix_world @ Vector(c)
                xs.append(w.x); ys.append(w.y); zs.append(w.z)
    if not xs:
        return 0.0, 0.0, 0.0, 1.0
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    zmin = min(zs)
    h = max(zs) - zmin
    return cx, cy, zmin, h


def _armature():
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE":
            return o
    return None


# ---------------------------------------------------------------------------
# 3. 白膜材质
# ---------------------------------------------------------------------------
def wm_clay(color=(0.85, 0.85, 0.87), roughness=0.6):
    """给所有 MESH 施加统一白膜（浅灰 Principled）材质。"""
    mat = bpy.data.materials.new("WhiteModelClay")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    out = nodes.new(type="ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    n = 0
    for o in bpy.context.scene.objects:
        if o.type == "MESH":
            if o.data.materials:
                o.data.materials.clear()
            o.data.materials.append(mat)
            n += 1
    return {"ok": True, "message": f"白膜材质已应用到 {n} 个 mesh"}


# ---------------------------------------------------------------------------
# 4. 三点光
# ---------------------------------------------------------------------------
def wm_lighting():
    """Key/Fill/Rim 三点光，位置基于包围盒。"""
    cx, cy, zmin, h = _bounds()
    s = max(h, 0.5)

    def add(loc, energy, size, name):
        d = bpy.data.lights.new(name, "AREA")
        d.energy = energy
        d.size = size
        lo = bpy.data.objects.new(name, d)
        lo.location = loc
        bpy.context.scene.collection.objects.link(lo)

    add((cx + s * 2, cy - s * 2, zmin + h * 2.5), s * s * 80, s * 2, "Key")
    add((cx - s * 2, cy - s * 2, zmin + h * 1.2), s * s * 40, s * 2.5, "Fill")
    add((cx, cy + s * 2, zmin + h * 3), s * s * 60, s * 1.5, "Rim")

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[1].default_value = 0.25
    bpy.context.scene.world = world
    return {"ok": True, "message": "三点光 + 世界环境已设置"}


# ---------------------------------------------------------------------------
# 5. 相机与构图（核心：响应提示词）
# ---------------------------------------------------------------------------
def _anim_len():
    """返回动画长度（帧数），默认 1。"""
    arm = _armature()
    if arm and arm.animation_data and arm.animation_data.action:
        fr = arm.animation_data.action.frame_range
        return max(1, int(fr[1]) - int(fr[0]) + 1)
    return 1


def _loop_animation(armature, total_frames):
    """把动画设为循环，填满 total_frames（用 NLA strip repeat）。"""
    ad = armature.animation_data
    if not ad or not ad.action:
        return
    action = ad.action
    anim_len = max(1, int(action.frame_range[1]) - int(action.frame_range[0]) + 1)
    if total_frames <= anim_len:
        return  # 无需循环

    # 把 action 移入 NLA track 并设置 repeat
    track = None
    for t in ad.nla_tracks:
        if t.name == "Loop":
            track = t
            break
    if track is None:
        track = ad.nla_tracks.new()
        track.name = "Loop"
    # 清空旧 strips
    for s in list(track.strips):
        track.strips.remove(s)

    strip = track.strips.new(name="Loop", start=1, action=action)
    strip.repeat = int(total_frames // anim_len) + 1
    # 让 NLA 接管（取消直接 action）
    ad.action = None
    _log(f"动画循环已设置: 动画{anim_len}帧 -> 循环填满{total_frames}帧 (repeat={strip.repeat})")


def wm_camera(pitch_deg=0.0, distance_ratio=2.2, orbit_seconds=None, fov=50.0):
    """
    设置相机 + 环绕动画。

    参数（Hermes 根据提示词解析后传入）：
      pitch_deg        : 俯仰角（度）。正=仰视(相机放低向上看)，负=俯视(相机抬高向下看)，0=平视
      distance_ratio   : 环绕半径 = 角色高度 * ratio。特写≈1.2，默认≈2.2，远景≈3.5
      orbit_seconds    : 环绕 360° 的时长（秒）。None=跟随动画。慢环绕给更大值（上限 6 秒）
      fov              : 视角（度），特写可给小值（如 35）制造长焦压缩感
    """
    cx, cy, zmin, h = _bounds()
    h = max(h, 0.5)
    pitch = math.radians(pitch_deg)

    # 垂直 FOV + 保证人物完整入镜的最小距离
    half_vfov = math.radians(fov) / 2.0
    min_dist = (h * 0.85) / math.tan(half_vfov)
    cam_r = max(h * distance_ratio, min_dist)

    # 取景中心 = 角色几何中心；仰视(pitch>0)放低相机，俯视抬高
    center_z = zmin + h * 0.5
    cam_h = center_z - math.sin(pitch) * cam_r * 0.5
    cam_h = max(cam_h, zmin + h * 0.05)

    # 相机 + 环绕 Empty
    cam_data = bpy.data.cameras.new("Cam")
    cam_data.sensor_fit = "VERTICAL"
    cam_data.angle = math.radians(fov)
    cam = bpy.data.objects.new("Cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam

    empty = bpy.data.objects.new("Orbit", None)
    empty.location = (cx, cy, center_z)
    bpy.context.scene.collection.objects.link(empty)
    cam.parent = empty
    cam.location = (0, -cam_r, cam_h - center_z)
    cam.rotation_euler = (math.radians(90), 0, math.radians(-90))

    tc = cam.constraints.new(type="TRACK_TO")
    tc.target = empty
    tc.track_axis = "TRACK_NEGATIVE_Z"
    tc.up_axis = "UP_Y"

    # 环绕动画：Empty 绕 Z 轴 360°
    scene = bpy.context.scene
    fs = scene.frame_start
    anim_len = _anim_len()
    if orbit_seconds is not None and orbit_seconds > 0:
        orbit_seconds = min(orbit_seconds, 15.0)  # 上限 15 秒，避免帧数爆炸
        fe = fs + int(orbit_seconds * scene.render.fps) - 1
    else:
        # 默认：跟随动画长度，但至少 2 秒（60 帧）让环绕有意义
        fe = fs + max(anim_len, int(2.0 * scene.render.fps)) - 1
    scene.frame_end = fe

    total = fe - fs + 1
    # 若环绕帧数超过动画长度，则循环动画填满
    arm = _armature()
    if arm is not None:
        _loop_animation(arm, total)

    empty.rotation_euler = (0, 0, 0)
    empty.keyframe_insert(data_path="rotation_euler", frame=fs)
    empty.rotation_euler = (0, 0, math.radians(360))
    empty.keyframe_insert(data_path="rotation_euler", frame=fe)

    # 记录构图参数，供 wm_render 自动读取
    _STATE.update({
        "pitch_deg": pitch_deg,
        "distance_ratio": distance_ratio,
        "orbit_seconds": orbit_seconds,
        "fov": fov,
    })

    return {
        "ok": True,
        "message": f"相机已设置: 俯仰={pitch_deg}° 半径={cam_r:.2f}(ratio={distance_ratio}) 环绕帧数={total} 环绕时长={total / scene.render.fps:.1f}s fov={fov}",
    }


# ---------------------------------------------------------------------------
# 6. 渲染 + 编码（headless 子进程，用 GPU 加速）
# ---------------------------------------------------------------------------
def wm_render(output_name, fbx_rel_path=None, pitch_deg=None, distance_ratio=None,
              orbit_seconds=None, fov=None, resolution="1280x720", fps=30):
    """
    用独立 headless blender 进程渲染白膜视频（GPU 加速）。
    构图参数默认自动读取前面 wm_import / wm_camera 记录的决策，也可显式传入。

    参数：
      output_name      : 英文小写输出名（不含扩展名）
      fbx_rel_path     : 覆盖动作 FBX 路径（默认用 wm_import 记录的）
      pitch_deg / distance_ratio / orbit_seconds / fov : 覆盖构图参数（默认用 wm_camera 记录的）
    """
    # 合并参数：显式传入优先，否则用 _STATE 记录的决策
    fbx_list = _STATE.get("fbx") or []
    if fbx_rel_path:
        fbx_list = [x.strip() for x in fbx_rel_path.split(",") if x.strip()]
    if not fbx_list:
        fbx_list = ["mixamo_fbx/basic/Idle.fbx"]
    fbx_arg = ",".join(fbx_list)

    pitch = _STATE["pitch_deg"] if pitch_deg is None else pitch_deg
    ratio = _STATE["distance_ratio"] if distance_ratio is None else distance_ratio
    orbit = _STATE["orbit_seconds"] if orbit_seconds is None else orbit_seconds
    fov_v = _STATE["fov"] if fov is None else fov

    out_path = os.path.join(PROJECT, "output", output_name + ".mp4")
    if output_name.startswith('single_'):
        from duel_plan import job_directory
        from duel_worker import set_status
        job_id = output_name[len('single_'):]
        if job_directory(job_id).exists():
            set_status(job_directory(job_id), 'rendering', '正在渲染单人视频…')

    cmd = [
        os.path.join(PROJECT, "blender"), "-b",
        "--python-exit-code", "1",
        "--python", os.path.join(PROJECT, "scripts", "render_white_model.py"),
        "--",
        "--fbx", fbx_arg,
        "--output", out_path,
        "--resolution", resolution,
        "--fps", str(fps),
        "--pitch_deg", str(pitch),
        "--distance_ratio", str(ratio),
        "--fov", str(fov_v),
    ]
    if orbit is not None and orbit > 0:
        cmd += ["--orbit_seconds", str(min(orbit, 15.0))]

    _log(f"headless GPU 渲染: fbx={fbx_arg} pitch={pitch} ratio={ratio} orbit={orbit} fov={fov_v}")

    # 关键：清除显示相关环境变量，避免 headless 子进程继承 GUI 的 DISPLAY/GHOST_BACKEND
    # 导致 blender -b 异常（GUI 进程跑在 Xvfb + GHOST_BACKEND=X11 下，这些会污染子进程）
    env = os.environ.copy()
    for k in ("DISPLAY", "GHOST_BACKEND", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY"):
        env.pop(k, None)

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        _log(f"渲染失败: {r.stderr[-800:]}")
        return {"ok": False, "message": "渲染失败: " + r.stderr[-400:]}
    if not os.path.exists(out_path):
        return {"ok": False, "message": "渲染完成但未找到输出文件"}
    return {"ok": True, "message": f"已生成 {os.path.relpath(out_path, PROJECT)}"}


# ---------------------------------------------------------------------------
# 挂到 bpy 模块，供 execute_code 里 bpy.wm_xxx 调用
# ---------------------------------------------------------------------------
bpy.wm_clear = wm_clear
bpy.wm_import = wm_import
bpy.wm_clay = wm_clay
bpy.wm_lighting = wm_lighting
bpy.wm_camera = wm_camera
bpy.wm_render = wm_render

# Dual-person jobs use independent saved scenes; no long render on the MCP thread.
def wm_duel_plan(job_id, plan, revision=0):
    try:
        from duel_plan import write_plan
        accepted = write_plan(job_id, plan, revision)
        return {"ok": True, "job_id": job_id, "message": "动态计划已提交，任务管理器将检查并渲染", "events": len(accepted['events'])}
    except (ValueError, TypeError, KeyError) as error:
        return {"ok": False, "message": str(error)}


def wm_duel_status(job_id):
    try:
        from duel_worker import status
        return {"ok": True, **status(job_id)}
    except (ValueError, FileNotFoundError) as error:
        return {"ok": False, "message": str(error)}


bpy.wm_duel_plan = wm_duel_plan
bpy.wm_duel_status = wm_duel_status

def wm_single_plan(job_id, plan):
    try:
        from single_plan import write_single
        accepted = write_single(job_id, plan)
        return {'ok': True, 'job_id': job_id, 'message': '单人计划已提交，后台将独立渲染', 'animations': accepted['animations']}
    except (ValueError, TypeError, KeyError) as error:
        return {'ok': False, 'message': str(error)}


bpy.wm_single_plan = wm_single_plan

def wm_scene_cast(job_id, cast):
    try:
        from scene_cast import write_cast
        accepted = write_cast(job_id, cast)
        return {'ok': True, **accepted}
    except (ValueError, TypeError, KeyError) as error:
        return {'ok': False, 'message': str(error)}


bpy.wm_scene_cast = wm_scene_cast


def wm_scene_plan(job_id, scene):
    try:
        from scene_plan import write_scene
        accepted=write_scene(job_id,scene)
        return {'ok':True,'job_id':job_id,'message':'物品与交互场景已提交',
                'objects':len(accepted['objects']),'interactions':len(accepted['interactions'])}
    except (ValueError,TypeError,KeyError) as error:
        return {'ok':False,'message':str(error)}


bpy.wm_scene_plan=wm_scene_plan


def wm_semantic_review(job_id,review):
    try:
        from semantic_review import write_review
        accepted=write_review(job_id,review)
        return {'ok':True,'job_id':job_id,'passed':accepted['passed'],
                'requirements':len(accepted['requirements']),'message':accepted['summary']}
    except (ValueError,TypeError,KeyError) as error:
        return {'ok':False,'message':str(error)}


bpy.wm_semantic_review=wm_semantic_review


# Agent-directed jobs persist the actual MCP scene instead of rebuilding it.
def wm_agent_analyze(job_id, analysis):
    try:
        from agent_scene import analyze_job
        return analyze_job(job_id, analysis)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        return {'ok': False, 'message': str(error)}


def wm_agent_scene(job_id, analysis=None):
    try:
        from agent_scene import submit_scene
        return submit_scene(job_id, analysis)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        return {'ok': False, 'message': str(error)}


def wm_agent_lighting(job_id=None, force=False):
    """确保当前 EEVEE/Cycles 场景有实际可渲染照明（World+三点光），避免黑屏。

    场景已有照明且 force=False 时不做任何改动。清空场景或自建场景后应调用。
    """
    try:
        import bpy
        from agent_scene import ensure_scene_lighting, illumination_facts
        scene = bpy.context.scene
        if not force and illumination_facts(scene).get('illuminated'):
            return {'ok': True, 'applied': False, 'reason': 'already_lit',
                    'message': '场景已有可渲染照明，未重复添加'}
        result = ensure_scene_lighting(scene, force=force)
        return {'ok': True, **result}
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        return {'ok': False, 'message': str(error)}


def wm_agent_reference_info():
    try:
        from agent_reference import reference_info
        return reference_info()
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        return {'ok': False, 'message': str(error)}


def wm_agent_build(job_id, single_plan=None, scene_plan=None, duel_plan=None):
    try:
        from agent_reference import start_build
        return start_build(job_id, single_plan=single_plan, scene_plan=scene_plan, duel_plan=duel_plan)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        return {'ok': False, 'message': str(error)}


def wm_agent_build_status(job_id):
    try:
        from agent_reference import build_status
        return build_status(job_id)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        return {'ok': False, 'message': str(error)}


def wm_agent_load(job_id):
    try:
        from agent_reference import load_scene
        return load_scene(job_id)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        return {'ok': False, 'message': str(error)}


bpy.wm_agent_analyze = wm_agent_analyze
bpy.wm_agent_scene = wm_agent_scene
bpy.wm_agent_lighting = wm_agent_lighting
bpy.wm_agent_reference_info = wm_agent_reference_info
bpy.wm_agent_build = wm_agent_build
bpy.wm_agent_build_status = wm_agent_build_status
bpy.wm_agent_load = wm_agent_load


def _agent_component(module, function, *args, **kwargs):
    """Lazy imports keep legacy helpers independent of the robot toolchain."""
    import importlib
    try:
        return getattr(importlib.import_module(module), function)(*args, **kwargs)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, ImportError) as error:
        return {'ok': False, 'message': str(error)}


def wm_agent_character_info(character_id='beta', detail=False):
    return _agent_component('agent_character', 'character_info', character_id, detail=detail)


def wm_agent_actor_ensure(job_id, actor_id, character_id='beta', **options):
    return _agent_component('agent_character', 'actor_ensure', job_id, actor_id,
                            character_id=character_id, **options)


def wm_agent_actor_info(job_id, actor_id):
    return _agent_component('agent_character', 'actor_info', job_id, actor_id)


def wm_agent_actor_list(job_id):
    return _agent_component('agent_character', 'actor_list', job_id)


def wm_agent_actor_remove(job_id, actor_id):
    return _agent_component('agent_character', 'actor_remove', job_id, actor_id)


def wm_agent_actor_adopt(job_id, actor_id, armature_name, root_name=None, **options):
    return _agent_component('agent_character', 'actor_adopt', job_id, actor_id,
                            armature_name, root_name=root_name, **options)


def wm_agent_motion_search(query, limit=8):
    return _agent_component('agent_motion_catalog', 'search_motions', query, limit=limit)


def wm_agent_motion_info(motion_id):
    return _agent_component('agent_motion_catalog', 'motion_info', motion_id)


def wm_agent_motion_apply(job_id, actor_id, segment):
    return _agent_component('agent_motion', 'motion_apply', job_id, actor_id, segment)


def wm_agent_locomotion_apply(job_id, actor_id, path, event_id='locomotion_base', **options):
    return _agent_component('agent_locomotion', 'apply_locomotion', job_id, actor_id,
                            path, event_id=event_id, **options)


def wm_agent_actor_controls(job_id, actor_id):
    return _agent_component('agent_motion_controls', 'controls_ensure', job_id, actor_id)


def wm_agent_controls_match(job_id, actor_id, chain=None):
    return _agent_component('agent_motion_controls', 'controls_match', job_id, actor_id, chain=chain)


def wm_agent_timeline_event(job_id, actor_id, event_id, event):
    return _agent_component('agent_motion_timeline', 'register_event', job_id, actor_id, event_id, event)


def wm_agent_timeline_info(job_id, actor_id, detail=False):
    result = _agent_component('agent_motion_timeline', 'timeline_info', job_id, actor_id)
    if detail or result.get('ok') is False:
        return result
    return {key: value for key, value in result.items() if key not in ('baseline', 'events')} | {
        'events': [{key: value for key, value in event.items()
                    if key not in ('keyframes', 'root_path', 'targets',
                                   'locomotion_path', 'locomotion_measurements')}
                   | {'pose_keyframes_count': len(event.get('keyframes', [])),
                      'root_path_count': len(event.get('root_path', [])),
                      'locomotion_path_count': len(event.get('locomotion_path', [])),
                      'target_names': list(event.get('targets', {}))}
                   for event in result.get('events', [])],
        'detail_available': True,
    }


def wm_agent_motion_commit(job_id, actor_id, **options):
    return _agent_component('agent_motion_timeline', 'motion_commit', job_id, actor_id, **options)


def wm_agent_camera_pov(job_id, actor_id, target_actor_id, start_frame, end_frame,
                        fov=75, return_camera=None, shot_id=None):
    return _agent_component('agent_motion_cameras', 'camera_pov', job_id, actor_id,
                            target_actor_id, start_frame, end_frame, fov=fov,
                            return_camera=return_camera, shot_id=shot_id)


def wm_agent_camera_full(job_id, camera_spec, actor_ids=None, focus_objects=None):
    return _agent_component('agent_motion_cameras', 'camera_full', job_id, camera_spec,
                            actor_ids=actor_ids, focus_objects=focus_objects)


def wm_agent_camera_check(job_id, requirements=None, frame_start=None, frame_end=None,
                          detail=False):
    return _agent_component('agent_camera_quality', 'check_camera_visibility', job_id,
                            requirements=requirements, frame_start=frame_start,
                            frame_end=frame_end, detail=detail)


def wm_agent_preview(job_id, frames=None, resolution=(640, 360)):
    return _agent_component('agent_preview', 'preview_start', job_id, frames=frames,
                            resolution=resolution)


def wm_agent_preview_status(job_id, preview_id):
    return _agent_component('agent_preview', 'preview_status', job_id, preview_id)


def wm_agent_motion_check(job_id, actor_id=None, detail=False, **options):
    result = _agent_component('agent_motion_quality', 'motion_check', job_id, actor_id, **options)
    if detail or result.get('ok') is False:
        return result

    def compact(value):
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items()
                    if key not in ('snapshots', 'channels', 'keyframe_values', 'timeline')}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value

    return {**compact(result), 'detail_available': True}


bpy.wm_agent_character_info = wm_agent_character_info
bpy.wm_agent_actor_ensure = wm_agent_actor_ensure
bpy.wm_agent_actor_info = wm_agent_actor_info
bpy.wm_agent_actor_list = wm_agent_actor_list
bpy.wm_agent_actor_remove = wm_agent_actor_remove
bpy.wm_agent_actor_adopt = wm_agent_actor_adopt
bpy.wm_agent_motion_search = wm_agent_motion_search
bpy.wm_agent_motion_info = wm_agent_motion_info
bpy.wm_agent_motion_apply = wm_agent_motion_apply
bpy.wm_agent_locomotion_apply = wm_agent_locomotion_apply
bpy.wm_agent_actor_controls = wm_agent_actor_controls
bpy.wm_agent_controls_match = wm_agent_controls_match
bpy.wm_agent_timeline_event = wm_agent_timeline_event
bpy.wm_agent_timeline_info = wm_agent_timeline_info
bpy.wm_agent_motion_commit = wm_agent_motion_commit
bpy.wm_agent_motion_check = wm_agent_motion_check
bpy.wm_agent_camera_pov = wm_agent_camera_pov
bpy.wm_agent_camera_full = wm_agent_camera_full
bpy.wm_agent_camera_check = wm_agent_camera_check
bpy.wm_agent_preview = wm_agent_preview
bpy.wm_agent_preview_status = wm_agent_preview_status

_log("preload_helpers 已加载，helper 函数已挂到 bpy")
