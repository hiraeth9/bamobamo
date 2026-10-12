"""Async single-actor dispatch around the existing animation/render implementation."""
import json
from duel_plan import PROJECT, atomic_json, job_directory, number


def validate_single(raw, job_id):
    if not isinstance(raw, dict) or raw.get('version') != 1:
        raise ValueError('单人计划需要 version=1')
    assets = raw.get('animations')
    if not isinstance(assets, list) or not 1 <= len(assets) <= 16:
        raise ValueError('需要 1 至 16 个真实动作资源')
    for asset in assets:
        if not isinstance(asset, str):
            raise ValueError('动作资源路径必须是文本')
        path = (PROJECT / asset).resolve()
        if not path.is_relative_to(PROJECT / 'mixamo_fbx') or path.suffix.lower() != '.fbx' or not path.is_file():
            raise ValueError('动作路径不存在或越界')
    policy = raw.get('camera_policy', 'fixed')
    if policy not in ('fixed', 'orbit'):
        raise ValueError('单人镜头支持 fixed 或 orbit')
    duration = number(raw.get('duration_seconds', 6), '时长', 1, 15)
    return {'version': 1, 'job_id': job_directory(job_id).name, 'animations': assets,
            'duration_seconds': duration, 'frame_count': round(duration * 30), 'fps': 30,
            'camera_policy': policy, 'pitch_deg': number(raw.get('pitch_deg', 0), '俯仰', -80, 80),
            'distance_ratio': number(raw.get('distance_ratio', 3.5), '距离比例', 1.2, 6),
            'fov': number(raw.get('fov', 50), '视角', 20, 90)}


def write_single(job_id, raw):
    plan = validate_single(raw, job_id)
    path = job_directory(job_id) / 'single_plan.json'
    if path.exists() and json.loads(path.read_text()) != plan:
        raise ValueError('任务已有不同单人计划')
    atomic_json(path, plan)
    return plan


def single_planning_prompt(prompt, job_id):
    assets = '\n'.join(str(p.relative_to(PROJECT)) for p in sorted((PROJECT / 'mixamo_fbx').rglob('*.fbx')))
    return f'''单个角色的自然语言任务使用已有动作素材。只通过 MCP execute_code 调用 print(bpy.wm_single_plan("{job_id}", plan))，ok=true后结束，不调用wm_clear/import/camera/render，不执行shell，不等待视频。
内部计划：version=1, animations=[真实资源路径...], duration_seconds=1..15(未指定默认6), camera_policy="fixed"或"orbit", pitch_deg=-80..80, distance_ratio=1.2..6, fov=20..90。
用户要求全身时 distance_ratio=3.5, pitch_deg=0,fov=50；未要求环绕时选择fixed。动作意图、顺序和资源由你解释，不要求用户选文件。现有单人多动作使用原有NLA混合与等长重定时，不保证任意组合自然。
已核实 Kicking 有较大位移，原地踢击优先Mma Kick。多个意图选择多个相关资源，不增加无关招式。
可用资源：\n{assets}\n用户需求：{prompt}'''
