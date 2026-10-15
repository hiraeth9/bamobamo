"""Durable job worker. A process lock serializes planning/building/rendering."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import hashlib
import math

from duel_plan import PROJECT, JOBS, atomic_json, job_directory, planning_prompt, validate_plan
from single_plan import validate_single, single_planning_prompt
from scene_cast import validate_cast, automatic_planning_prompt
from scene_plan import load_scene
from scene_intents import validate_intent_coverage
from locomotion_plan import validate_locomotion_coverage
from semantic_review import semantic_review_prompt, load_review


def review_planned_request(folder):
    """Check requested meaning independently of the planner's own flags.

    This is a text/plan audit, not certification of rendered motion quality.
    Geometry and media checks still run separately after scene construction.
    """
    request_path=folder/'request.json'
    if not request_path.exists() or json.loads(request_path.read_text()).get('mode')!='auto':
        return None
    path=folder/'semantic_review.json'
    if not path.exists():
        set_status(folder,'reviewing','正在核对提示词中的全部动作、顺序与目标关系…')
        # The audit has its complete capability contract in this prompt. Keep
        # director skills and workspace rules out of the independent review.
        run_logged(folder,['hermes','--ignore-rules','-z',semantic_review_prompt(folder.name)],'semantic_review.log',240)
    if not path.exists():
        raise RuntimeError('没有提交提示词与计划的独立复核，停止生成')
    review=load_review(folder.name)
    if not review['passed']:
        missing='；'.join(item['description']+'：'+item['evidence'] for item in review['requirements'] if item['status']!='covered')
        raise RuntimeError('提示词与执行能力不匹配，停止生成：'+missing)
    return review


def semantic_result_fields(folder):
    path=folder/'semantic_review.json'
    if not path.exists(): return {}
    try:
        review=load_review(folder.name)
        passed=review['passed']
    except (ValueError,TypeError,KeyError,OSError):
        passed=False
    return {'semantic_review_url':f'/output/jobs/{folder.name}/semantic_review.json',
            'semantic_review_passed':passed}


def render_scene_job(folder,kind,actor_count,frames,fps):
    review_planned_request(folder)
    quality=json.loads((folder/'quality.json').read_text())
    if not quality['passed']: raise RuntimeError('接触、支撑、物品碰撞或构图检查未通过')
    for stage,resolution,output in [('preview','640x360',folder/'preview.mp4'),('rendering','1280x720',folder/'final.mp4')]:
        (folder/'render_progress.json').unlink(missing_ok=True)
        set_status(folder,stage,'正在生成低清预览…' if stage=='preview' else '检查已通过，正在渲染正式视频…')
        run_logged(folder,[str(PROJECT/'blender'),'-b',str(folder/'scene.blend'),'-t','8','--python-exit-code','1',
                   '--python',str(PROJECT/'scripts/render_saved_scene.py'),'--','--output',str(output),
                   '--fps',str(fps),'--resolution',resolution,'--progress',str(folder/'render_progress.json')],stage+'.log',900)
        media=verify_video(output,frames,fps,tuple(map(int,resolution.split('x'))))
        shutil.rmtree(output.with_name(output.stem+'_frames'),ignore_errors=True)
    set_status(folder,'verifying','正在核验视频帧数、时长和完整解码…')
    review_planned_request(folder)
    destination=PROJECT/'output'/f'{kind}_{folder.name}.mp4'; temporary=destination.with_suffix('.tmp')
    shutil.copy2(folder/'final.mp4',temporary);os.replace(temporary,destination)
    scene_path=folder/'scene_plan.json'
    scene=json.loads(scene_path.read_text()) if scene_path.exists() else None
    result={'ok':True,'kind':kind,'actor_count':actor_count,'job_id':folder.name,'video':str(destination.relative_to(PROJECT)),
            'video_url':f'/output/{destination.name}','preview_url':f'/output/jobs/{folder.name}/preview.mp4',
            'plan_url':f'/output/jobs/{folder.name}/'+('single_plan.json' if kind=='single' else 'plan.json'),
            'quality_url':f'/output/jobs/{folder.name}/quality.json','media':media,'quality_passed':True,'visual_review_required':True,
            'object_count':len(scene['objects']) if scene else 0,'interaction_count':len(scene['interactions']) if scene else 0,
            'report':'视频已生成；几何、构图与媒体检查通过。规划语义复核与实际动作观感分别核验，动作观感请查看预览。',
            **semantic_result_fields(folder)}
    if scene: result['scene_plan_url']=f'/output/jobs/{folder.name}/scene_plan.json'
    if (folder/'executed_scene_plan.json').exists():
        result['executed_scene_plan_url']=f'/output/jobs/{folder.name}/executed_scene_plan.json'
    if (folder/'executed_locomotion_plan.json').exists():
        result['executed_locomotion_plan_url']=f'/output/jobs/{folder.name}/executed_locomotion_plan.json'
    atomic_json(folder/'result.json',result)
    set_status(folder,'complete','视频已完成',result=result)
    return result


def set_status(folder, stage, message, **extra):
    old = json.loads((folder / 'status.json').read_text()) if (folder / 'status.json').exists() else {}
    old.update({'job_id': folder.name, 'stage': stage, 'message': message, 'updated_at': time.time(), **extra})
    atomic_json(folder / 'status.json', old)


def child_environment():
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    # Software Mesa can otherwise spawn hundreds of threads from host CPU
    # discovery even when this container has only one CPU of quota.
    try:
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota != 'max':
            limit = max(1, (int(quota) + int(period) - 1) // int(period))
            env.setdefault('LP_NUM_THREADS', str(min(8, limit)))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    for key in ('DISPLAY', 'GHOST_BACKEND', 'XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY'):
        env.pop(key, None)
    return env


def verify_video(path, frames, fps=30, resolution=None):
    """Count decoded frames, validate metadata and fully decode, not just exists."""
    if frames < 1:
        raise RuntimeError('视频帧数必须为正数')
    ffprobe = str(PROJECT / 'ffprobe') if (PROJECT / 'ffprobe').exists() else 'ffprobe'
    result = subprocess.run([ffprobe, '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                             '-show_entries', 'stream=width,height,r_frame_rate,nb_read_frames,duration', '-of', 'json', str(path)],
                            capture_output=True, text=True, timeout=90)
    if result.returncode or not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError('视频不存在或不能解码')
    streams = json.loads(result.stdout).get('streams', [])
    if not streams:
        raise RuntimeError('没有视频流')
    stream = streams[0]
    numerator, denominator = map(int, stream['r_frame_rate'].split('/'))
    if int(stream.get('nb_read_frames', 0)) != frames or abs(numerator / denominator - fps) > .001:
        raise RuntimeError('视频帧数或帧率不符合计划')
    if abs(float(stream.get('duration', 0)) - frames / fps) > 1 / fps + .001:
        raise RuntimeError('视频时长不符合计划')
    if resolution and (stream['width'], stream['height']) != tuple(resolution):
        raise RuntimeError('视频分辨率不符合计划')
    ffmpeg = str(PROJECT / 'ffmpeg') if (PROJECT / 'ffmpeg').exists() else 'ffmpeg'
    decode = subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'], capture_output=True, text=True, timeout=90)
    if decode.returncode:
        raise RuntimeError('视频完整解码失败')
    return stream


def run_logged(folder, command, log_name, timeout):
    with (folder / log_name).open('w', encoding='utf-8') as logfile:
        process = subprocess.Popen(command, cwd=PROJECT, env=child_environment(), stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
        existing = json.loads((folder / 'status.json').read_text())
        set_status(folder, existing['stage'], existing['message'], subprocess_pid=process.pid)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise RuntimeError(f'{log_name} 超时，任务已停止')
    if code:
        lines = (folder / log_name).read_text(errors='replace').splitlines()
        raise RuntimeError(f'{log_name} 失败（退出码 {code}）：' + '\n'.join(lines[-8:]))


def render_hermes_scene(folder, manifest):
    """Render the submitted scene so runtime errors can return to the director."""
    manifest_path = folder / 'agent_scene.json'
    scene = folder / 'scene.blend'
    scene_hash = hashlib.sha256(scene.read_bytes()).hexdigest()
    if scene_hash != manifest['scene_sha256']:
        raise RuntimeError('场景在提交后发生变化，需要重新提交')
    frames, fps = manifest['frame_count'], manifest['fps']
    final_width, final_height = manifest.get('resolution', [1280, 720])
    # H.264 YUV420 needs even dimensions; rounding up one pixel is disclosed.
    output_width, output_height = final_width + final_width % 2, final_height + final_height % 2
    scale = min(1, 640 / output_width, 360 / output_height)
    preview_size = (max(2, round(output_width * scale / 2) * 2),
                    max(2, round(output_height * scale / 2) * 2))
    final_size = (output_width, output_height)
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    analysis_path = folder / 'agent_analysis.json'
    for path, expected in ((analysis_path, manifest['analysis_sha256']),
                           (folder / 'request.json', manifest['request_sha256'])):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('场景的分析或原始提示词已经变化，需要重新提交')
    for stage, size in (('preview', preview_size), ('rendering', final_size)):
        resolution = f'{size[0]}x{size[1]}'
        (folder / 'render_progress.json').unlink(missing_ok=True)
        set_status(folder, stage, '正在渲染 Hermes 创建的真实场景…')
        output = folder / ('preview.mp4' if stage == 'preview' else 'final.mp4')
        run_logged(folder, [str(PROJECT / 'blender'), '-b', str(scene), '-t', '8', '--python-exit-code', '1',
                   '--python', str(PROJECT / 'scripts/render_saved_scene.py'), '--', '--output', str(output),
                   '--preserve-scene-fps', '--resolution', resolution, '--progress', str(folder / 'render_progress.json')],
                   stage + '.log', max(900, frames * 5))
        media = verify_video(output, frames, fps, tuple(map(int, resolution.split('x'))))
        shutil.rmtree(output.with_name(output.stem + '_frames'), ignore_errors=True)
    return media, final_size, (final_width, final_height), scene_hash, manifest_hash


def camera_repair_motion_signature(manifest):
    """Recorded actor channels and timing; props/physical motion are separate QA."""
    actors = []
    for item in manifest.get('motion_execution', {}).get('actors', []):
        pose = item.get('pose_animation', {}).get('channel_values_sha256')
        root = item.get('root_animation', {}).get('channel_values_sha256')
        if not item.get('actor_id') or not isinstance(pose, str) or not isinstance(root, str):
            return {'status': 'unknown', 'reason': '缺少真实人物pose/root通道哈希；不能宣称运动已保全'}
        actors.append({'actor_id': item['actor_id'], 'pose_channels_sha256': pose,
                       'root_channels_sha256': root})
    if not actors:
        return {'status': 'unknown', 'reason': '没有人物实际通道证据；物品与静态姿态仍需独立核对'}
    return {'status': 'available',
            'timing': {key: manifest.get(key) for key in
                       ('frame_start', 'frame_end', 'frame_count', 'fps', 'fps_base', 'duration_seconds')},
            'actors': sorted(actors, key=lambda item: item['actor_id']),
            'scope': 'actual_actor_pose_root_keyed_channels_and_timing_only; not_props_or_allframe_physics'}


def camera_only_probe_failure(probe, initial_feedback=None):
    """Never freeze motion when feedback also asks for action corrections."""
    if initial_feedback and initial_feedback.get('repair_scope') != 'camera_only':
        return False
    errors = (probe or {}).get('errors', [])
    return bool(errors) and all(isinstance(item, dict) and
                               (str(item.get('code', '')).startswith('camera_') or
                                item.get('code') == 'missing_camera') for item in errors)


def run_hermes_scene_job(folder, already_planned=False):
    """Let Hermes author a real MCP scene; verify its delivery, not a whitelist."""
    from agent_director import director_prompt
    request = json.loads((folder / 'request.json').read_text())
    original_request_sha = hashlib.sha256((folder / 'request.json').read_bytes()).hexdigest()
    manifest_path = folder / 'agent_scene.json'
    seed_feedback = folder / 'agent_seed_feedback.json'
    feedback = json.loads(seed_feedback.read_text()) if seed_feedback.is_file() else None
    initial_feedback = feedback
    execution_errors = []
    manifest = None
    protected_camera_motion = None
    if initial_feedback and initial_feedback.get('repair_scope') == 'camera_only' and manifest_path.is_file():
        protected_camera_motion = camera_repair_motion_signature(json.loads(manifest_path.read_text()))
    # Repairs concern a missing or unreproducible saved scene. Action choice,
    # character count and subjective quality never become frontend gates.
    for attempt in range(3):
        director_log = 'hermes.log' if attempt == 0 else f'agent_repair_{attempt}.log'
        probe = None
        report_path = None
        try:
            if not (already_planned and attempt == 0 and manifest_path.exists()):
                prior_submission_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
                repairing = attempt > 0 or feedback is not None
                stage = 'repairing' if repairing else 'planning'
                message = '正在将实际场景错误交回 Hermes，通过 MCP 修正…' if repairing else 'Hermes 正在完整分析提示词，并通过 MCP 构建 Blender 场景…'
                set_status(folder, stage, message)
                # Keep Hermes' configured provider and toolsets. Bound only the
                # auxiliary vision transport so a failed endpoint cannot stall
                # Blender scene construction with repeated SDK retries.
                command = [str(PROJECT / 'hermes/.venv/bin/python'),
                           str(PROJECT / 'scripts/hermes_director_cli.py'), '-z',
                           director_prompt(request['prompt'], folder.name, feedback)]
                run_logged(folder, command,
                           director_log, director_timeout_seconds())
                current_submission_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
                if current_submission_sha is not None and current_submission_sha == prior_submission_sha:
                    _check_director_authentication(folder / director_log)
                    raise RuntimeError('Hermes 本轮没有重新提交场景；旧 agent_scene.json 不能作为本次修复完成的证据，请修正后重新 wm_agent_scene')
            if not manifest_path.is_file():
                _check_director_authentication(folder / director_log)
                raise RuntimeError('Hermes 尚未通过 wm_agent_scene 提交真实场景；请继续构建并提交，不用旧unsupported标记结束')
            if hashlib.sha256((folder / 'request.json').read_bytes()).hexdigest() != original_request_sha:
                raise RuntimeError('修复改变了原始request/prompt；请恢复原提示词，在实际场景中修复后重新提交')
            submitted = json.loads(manifest_path.read_text())
            if (protected_camera_motion and protected_camera_motion['status'] == 'available'
                    and camera_repair_motion_signature(submitted) != protected_camera_motion):
                raise RuntimeError('仅相机修复改变了原人物实际pose/root通道或时长；请恢复已正确的运动，只修机位/镜头并重新提交')
            set_status(folder, 'verifying', '正在重载真实场景，核验相机、帧范围与资源…')
            report_path = folder / f'agent_probe_{attempt}.json'
            if report_path.exists():
                archive = folder / 'attempts' / 'probes'
                archive.mkdir(parents=True, exist_ok=True)
                report_path.replace(archive / f'{time.time_ns()}_{report_path.name}')
            run_logged(folder, [str(PROJECT / 'blender'), '-b', '-t', '4', '--python-exit-code', '1',
                       '--python', str(PROJECT / 'scripts/agent_scene_probe.py'), '--',
                       '--manifest', str(manifest_path), '--report', str(report_path)], f'agent_probe_{attempt}.log', 180)
            probe = json.loads(report_path.read_text())
            if not probe['passed']:
                raise RuntimeError('保存场景无法稳定重载：' + json.dumps(probe.get('errors', []), ensure_ascii=False))
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('camera_visibility_version') == 1:
                visibility = probe.get('camera_visibility', {})
                if visibility.get('passed') is not True:
                    probe.setdefault('errors', []).append({'code': 'camera_visibility_missing',
                                                           'message': '新场景没有通过的镜头需求检查'})
                    raise RuntimeError('新场景缺少通过的实际镜头检查，不能从重载成功推断人物与物品可见：' +
                                       json.dumps(visibility, ensure_ascii=False))
            media, final_size, saved_size, scene_hash, manifest_hash = render_hermes_scene(folder, manifest)
            break
        except (RuntimeError, ValueError, KeyError, OSError) as error:
            if isinstance(error, DirectorAuthenticationError):
                raise
            # The probe writes its structured report before exiting with status
            # 1 on failure. Keep that evidence when run_logged raises, but never
            # use a report left over from a previous submission.
            if probe is None and report_path is not None and report_path.is_file():
                try:
                    recorded_probe = json.loads(report_path.read_text())
                    if isinstance(recorded_probe, dict):
                        probe = recorded_probe
                except (ValueError, OSError):
                    pass
            if not manifest_path.is_file():
                _check_director_authentication(folder / director_log)
            execution_errors.append({'error': str(error), 'attempt': attempt + 1})
            feedback = {'error': str(error), 'attempt': attempt + 1,
                        'initial_feedback': initial_feedback,
                        'previous_execution_errors': execution_errors[:-1],
                        'instruction': '保留原提示词、此前实际反馈和正确分析。检查当前MCP场景及已保存场景，只修正真实错误后重新wm_agent_scene；不能为修复新的运行错误而撤销已经完成的动作要求。若旧组件限制不适用，可以自由bpy构建。'}
            if camera_only_probe_failure(probe, initial_feedback):
                protected_camera_motion = camera_repair_motion_signature(json.loads(manifest_path.read_text()))
            elif probe and probe.get('errors'):
                protected_camera_motion = None
            if protected_camera_motion:
                feedback.update(repair_scope='camera_only',
                                preserve_motion_signature=protected_camera_motion,
                                preservation_scope=protected_camera_motion.get('scope', 'unknown'),
                                camera_instruction='保留人物、椅子/门等物品及地面和墙体的实际运动、原时长。读取失败帧/遮挡物后只修镜头；明确位置/角度不能偷改，模糊机位可合理调整。不得删动作、删墙或擅自透明化来过检查。人物通道哈希可独立核对；物品及全片几何仍需实际检查，不能宣称已由此哈希证明。')
            if probe and probe.get('camera_visibility'):
                feedback['camera_visibility'] = probe['camera_visibility']
            if (folder / 'scene.blend').is_file():
                feedback['scene_path'] = str(folder / 'scene.blend')
            if (folder / 'agent_analysis.json').is_file():
                feedback['analysis_path'] = str(folder / 'agent_analysis.json')
            atomic_json(folder / f'agent_feedback_{attempt}.json', feedback)
            if attempt == 2:
                raise RuntimeError('Hermes 已尝试构建及修复，但场景仍无法交付：' + str(error)) from error
    if manifest is None:
        raise RuntimeError('没有可渲染的实际场景')
    scene = folder / 'scene.blend'
    analysis_path = folder / 'agent_analysis.json'
    final_width, final_height = saved_size
    output_width, output_height = final_size
    if hashlib.sha256(scene.read_bytes()).hexdigest() != scene_hash or hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_hash:
        raise RuntimeError('渲染期间已提交场景发生变化，不能发布混合版本的视频')
    for path, expected in ((analysis_path, manifest['analysis_sha256']),
                           (folder / 'request.json', manifest['request_sha256'])):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('渲染期间场景的分析或原始提示词发生变化')
    analysis_document = json.loads(analysis_path.read_text())
    analysis = analysis_document.get('analysis', {})
    warnings = [value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                for value in manifest.get('warnings', [])]
    if final_size != (final_width, final_height):
        warnings.append(f'H.264编码需要偶数尺寸，视频输出调整为{output_width}×{output_height}像素')
    for key in ('warnings', 'unfulfilled', 'approximate'):
        values = analysis.get(key, [])
        if not isinstance(values, list):
            values = [values] if values else []
        warnings.extend(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) for value in values)
    warnings = list(dict.fromkeys(warnings))
    destination = PROJECT / 'output' / f'agent_{folder.name}.mp4'
    temporary = destination.with_suffix('.tmp')
    shutil.copy2(folder / 'final.mp4', temporary)
    os.replace(temporary, destination)
    result = {'ok': True, 'kind': 'agent', 'job_id': folder.name,
              'actor_count': manifest.get('actor_count'), 'execution_strategy': 'hermes_mcp',
              'video': str(destination.relative_to(PROJECT)), 'video_url': f'/output/{destination.name}',
              'preview_url': f'/output/jobs/{folder.name}/preview.mp4',
              'analysis_url': f'/output/jobs/{folder.name}/agent_analysis.json',
              'scene_manifest_url': f'/output/jobs/{folder.name}/agent_scene.json',
              'scene_probe_url': f'/output/jobs/{folder.name}/agent_probe_{attempt}.json',
              'media': media, 'media_verified': True, 'scene_reload_verified': True,
              'visual_review_required': True, 'warnings': warnings,
              'report': 'Hermes 已通过 MCP 构建实际场景并生成视频，重载与媒体核验通过；动作及画面效果请查看预览。'}
    if manifest.get('character_policy') == 'library_beta':
        characters = manifest.get('character_execution', {})
        actors = characters.get('actors', [])
        result['character_policy'] = 'library_beta'
        result['character_identity_verified'] = bool(actors) and all(
            item.get('identity_verified') and item.get('binding_verified') for item in actors)
        result['character_identity_scope'] = 'original_asset_and_binding; not_pixel_visibility'
        result['motion_sources'] = [
            {'actor_id': item.get('actor_id'),
             'sources': (item.get('timeline', {}).get('last_commit') or {}).get('sources', []),
             'actual_pose_animation': item.get('has_actual_pose_animation', False)}
            for item in manifest.get('motion_execution', {}).get('actors', [])]
        if result['character_identity_verified']:
            result['report'] = '已使用动作库机器人并生成视频，人物绑定、场景重载与媒体核验通过；动作及画面效果请查看预览。'
    if manifest.get('camera_visibility_version') == 1:
        result['camera_visibility_verified'] = True
        result['camera_visibility_scope'] = probe['camera_visibility'].get('scope')
        result['camera_visibility_report_url'] = f'/output/jobs/{folder.name}/agent_probe_{attempt}.json'
    if protected_camera_motion:
        result['camera_only_actor_motion_preservation'] = {
            'status': 'actor_channels_verified' if protected_camera_motion['status'] == 'available' else 'unknown',
            'scope': protected_camera_motion.get('scope', 'actual actor channel evidence unavailable'),
            'reason': protected_camera_motion.get('reason')}
    atomic_json(folder / 'result.json', result)
    set_status(folder, 'complete', '视频已完成', result=result)
    return result


def director_timeout_seconds():
    """Allow lengthy MCP choreography without changing render/probe budgets."""
    value = os.environ.get('BAMO_DIRECTOR_TIMEOUT_SECONDS', '3600')
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError('BAMO_DIRECTOR_TIMEOUT_SECONDS 必须为有限正秒数') from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('BAMO_DIRECTOR_TIMEOUT_SECONDS 必须为有限正秒数')
    return seconds


class DirectorAuthenticationError(RuntimeError):
    """A provider credential error cannot be repaired by Blender operations."""


def _check_director_authentication(log_path):
    import re
    path = Path(log_path)
    if not path.is_file():
        return
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            if (re.search(r'^\s*HTTP\s+401\s*:', line)
                    and re.search(r'API[- ]key|Unauthorized|authentication', line, re.I)):
                raise DirectorAuthenticationError('模型接口 HTTP 401：API Key 无效或认证失败。请更新 Hermes 模型凭证后重新生成；未启动 Blender 场景修复。')


def run_job(job_id, already_planned=False):
    folder = job_directory(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'result.json').exists():
        result = json.loads((folder / 'result.json').read_text())
        if result.get('ok') and (PROJECT / result['video']).is_file():
            return result
    set_status(folder, 'queued', '等待前一个生成任务完成…', pid=os.getpid())
    JOBS.mkdir(parents=True, exist_ok=True)
    with (JOBS / '.pipeline.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if (folder / 'result.json').exists():
                existing = json.loads((folder / 'result.json').read_text())
                if existing.get('ok') and (PROJECT / existing['video']).is_file():
                    set_status(folder, 'complete', '任务已完成，无需重复执行', result=existing)
                    return existing
            if shutil.disk_usage(PROJECT).free < 1024**3:
                raise RuntimeError('可用空间不足 1 GB，无法生成')
            request_path = folder / 'request.json'
            request = json.loads(request_path.read_text()) if request_path.exists() else {}
            if request.get('execution_strategy') == 'hermes_mcp':
                return run_hermes_scene_job(folder, already_planned)
            if not already_planned:
                request = json.loads((folder / 'request.json').read_text())
                set_status(folder, 'planning', 'Hermes 正在理解人物关系、动作与节奏…')
                instruction = planning_prompt(request['prompt'], job_id)
                if request.get('mode') == 'auto':
                    single_instruction = single_planning_prompt(request['prompt'], job_id)
                    instruction = automatic_planning_prompt(request['prompt'], job_id, instruction, single_instruction)
                run_logged(folder, ['hermes', '-z', instruction], 'hermes.log', 300 if request.get('mode') == 'auto' else 240)
                if request.get('mode') == 'auto':
                    cast_path = folder / 'cast.json'
                    if not cast_path.exists():
                        raise RuntimeError('Hermes 尚未提交人物识别结果，拒绝猜测人数生成')
                    cast = validate_cast(json.loads(cast_path.read_text()), job_id)
                    if not cast['supported']:
                        raise RuntimeError(cast['message'])
                    expected = 'single_plan.json' if cast['actor_count'] == 1 else 'plan.json'
                    unexpected = 'plan.json' if cast['actor_count'] == 1 else 'single_plan.json'
                    if not (folder / expected).exists() or (folder / unexpected).exists():
                        raise RuntimeError(f"已识别 {cast['actor_count']} 人，但动作计划人数不一致，停止生成")
                    if cast['requires_scene'] and not (folder/'scene_plan.json').exists():
                        raise RuntimeError('提示词要求物品或房间，但没有提交场景计划，停止生成')
                    if (folder/'scene_plan.json').exists():
                        base=json.loads((folder/expected).read_text())
                        environment=load_scene(folder,job_id,cast['actor_count'],base['duration_seconds'])
                        if cast['requires_interaction'] and not environment['interactions']:
                            raise RuntimeError('提示词要求物品交互，但场景没有交互动作，停止生成')
                if not (folder / 'plan.json').exists() and not (folder / 'single_plan.json').exists():
                    single_video = PROJECT / 'output' / ('single_' + job_id + '.mp4')
                    if request.get('mode') == 'auto' and single_video.is_file():
                        probe = subprocess.run([str(PROJECT / 'ffprobe'), '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(single_video)], capture_output=True, text=True, timeout=90, check=True)
                        frames = int(json.loads(probe.stdout)['streams'][0]['nb_read_frames'])
                        media = verify_video(single_video, frames, 30, (1280, 720))
                        shutil.rmtree(single_video.with_name(single_video.stem + '_frames'), ignore_errors=True)
                        result = {'ok': True, 'kind': 'single', 'job_id': job_id, 'video': str(single_video.relative_to(PROJECT)), 'video_url': f'/output/{single_video.name}', 'media': media, 'report': '单人视频已生成并通过媒体核验。'}
                        atomic_json(folder / 'result.json', result)
                        set_status(folder, 'complete', '单人视频已完成', result=result)
                        return result
                    raise RuntimeError('Hermes 没有提交可执行计划；详情见任务日志，关键动作可能超出支持范围')
            # Also enforce the contract when resuming a pre-planned job. Older
            # plans without an intent ledger must still pass an independent
            # audit against the original request before any new render.
            cast_path=folder/'cast.json'
            if cast_path.exists():
                cast=validate_cast(json.loads(cast_path.read_text()),job_id)
                if not cast['supported']: raise RuntimeError(cast['message'])
                base_path=folder/('single_plan.json' if cast['actor_count']==1 else 'plan.json')
                other_path=folder/('plan.json' if cast['actor_count']==1 else 'single_plan.json')
                if not base_path.exists() or other_path.exists():
                    raise RuntimeError(f"已识别 {cast['actor_count']} 人，但动作计划人数不一致，停止生成")
                raw_base=json.loads(base_path.read_text())
                base=validate_single(raw_base,job_id) if cast['actor_count']==1 else validate_plan(raw_base,job_id)
                if raw_base!=base: atomic_json(base_path,base)
                environment=load_scene(folder,job_id,cast['actor_count'],base['duration_seconds'])
                if cast['requires_scene'] and environment is None:
                    raise RuntimeError('提示词要求物品或房间，但没有提交场景计划，停止生成')
                if 'object_intents' in cast:
                    validate_intent_coverage(cast,environment,base if cast['actor_count']==1 else None)
                validate_locomotion_coverage(cast,environment,base if cast['actor_count']==1 else None)
            review_planned_request(folder)
            if (folder / 'single_plan.json').exists():
                if (folder / 'plan.json').exists():
                    raise RuntimeError('任务同时提交了单人和双人计划，拒绝含糊执行')
                single = validate_single(json.loads((folder / 'single_plan.json').read_text()), job_id)
                environment=load_scene(folder,job_id,1,single['duration_seconds'])
                if single.get('locomotion'):
                    if environment and environment['interactions']:
                        raise RuntimeError('通用移动与物品操作混编尚未接入，不能忽略交互')
                    set_status(folder,'building','正在创建移动路线、连续步态与脚部支撑…')
                    command=[str(PROJECT/'blender'),'-b','-t','8','--python-exit-code','1',
                             '--python',str(PROJECT/'scripts/locomotion_scene.py'),
                             '--','--plan',str(folder/'single_plan.json')]
                    run_logged(folder,command,'build.log',300)
                    return render_scene_job(folder,'single',1,single['frame_count'],single['fps'])
                if environment or single.get('camera'):
                    if not single.get('camera') and (single['camera_policy']!='fixed' or single['pitch_deg']!=0 or single['distance_ratio']!=3.5 or single['fov']!=50):
                        raise RuntimeError('物品场景当前使用固定斜侧全景；自定义俯仰、环绕与近景尚未接入')
                    # load_scene already normalizes the saved plan. Preserve
                    # its original bytes because the audit binds to this file.
                    set_status(folder,'building','正在创建人物、物品场景与交互轨迹…')
                    if environment and environment['interactions']:
                        vault=any(event['action']=='vault_window' for event in environment['interactions'])
                        door_passage=any(event['action']=='pass_door' for event in environment['interactions'])
                        from scene_intents import BODY_SOURCES,VAULT_BODY_SOURCES
                        allowed=VAULT_BODY_SOURCES if vault else BODY_SOURCES
                        if any(p not in allowed for p in single['animations']) or single['camera_policy']!='fixed':
                            raise RuntimeError('翻窗使用Idle/Walk/Run身体来源与固定全景，普通Jump不能代替翻窗' if vault else '物品交互使用Idle/Walk身体来源与固定全景；其它动作混编尚未接入')
                        executor='window_vault_scene.py' if vault else ('door_passage_scene.py' if door_passage else 'prop_interaction_scene.py')
                        command=[str(PROJECT/'blender'),'-b','-t','8','--python-exit-code','1','--python',str(PROJECT/'scripts'/executor),
                                 '--','--plan',str(folder/'single_plan.json')]
                    else:
                        command=[str(PROJECT/'blender'),'-b','-t','8','--python-exit-code','1','--python',str(PROJECT/'scripts/render_white_model.py'),'--',
                                 '--fbx',','.join(single['animations']),'--output',str(folder/'final.mp4'),'--resolution','1280x720','--fps','30',
                                 '--frames',str(single['frame_count']),'--orbit_seconds',str(single['duration_seconds']),
                                 '--camera_policy',single['camera_policy'],'--pitch_deg',str(single['pitch_deg']),
                                 '--distance_ratio',str(single['distance_ratio']),'--fov',str(single['fov']),
                                 '--save-scene',str(folder/'scene.blend')]
                        if environment:
                            command += ['--scene-plan',str(folder/'scene_plan.json')]
                        if single.get('camera'):
                            command += ['--camera-plan',str(folder/'single_plan.json')]
                    run_logged(folder,command,'build.log',300)
                    return render_scene_job(folder,'single',1,single['frame_count'],single['fps'])
                video = PROJECT / 'output' / f'single_{job_id}.mp4'
                set_status(folder, 'rendering', '正在独立渲染单人视频…')
                command = [str(PROJECT / 'blender'), '-b', '-t', '8', '--python-exit-code', '1', '--python', str(PROJECT / 'scripts' / 'render_white_model.py'), '--',
                           '--fbx', ','.join(single['animations']), '--output', str(video), '--resolution', '1280x720', '--fps', '30',
                           '--frames', str(single['frame_count']), '--orbit_seconds', str(single['duration_seconds']), '--camera_policy', single['camera_policy'],
                           '--pitch_deg', str(single['pitch_deg']), '--distance_ratio', str(single['distance_ratio']), '--fov', str(single['fov'])]
                run_logged(folder, command, 'single_render.log', 900)
                media = verify_video(video, single['frame_count'], 30, (1280,720))
                review_planned_request(folder)
                shutil.rmtree(video.with_name(video.stem + '_frames'), ignore_errors=True)
                result = {'ok': True, 'kind': 'single', 'actor_count': 1, 'job_id': job_id, 'video': str(video.relative_to(PROJECT)), 'video_url': f'/output/{video.name}', 'media': media, 'report': '单人视频已生成并通过媒体核验，动作观感请查看视频。', **semantic_result_fields(folder)}
                atomic_json(folder / 'result.json', result)
                set_status(folder, 'complete', '单人视频已完成', result=result)
                return result
            raw_plan=json.loads((folder / 'plan.json').read_text())
            plan = validate_plan(raw_plan, job_id)
            if raw_plan!=plan: atomic_json(folder / 'plan.json', plan)
            set_status(folder, 'building', '正在创建双人角色、动作时间线及手脚 IK…')
            build_command = [str(PROJECT / 'blender'), '-b', '-t', '8', '--python-exit-code', '1',
                             '--python', str(PROJECT / 'scripts' / 'duel_scene.py'), '--', '--plan', str(folder / 'plan.json')]
            for attempt in range(3):
                try:
                    run_logged(folder, build_command, 'build.log', 300)
                    break
                except RuntimeError:
                    if already_planned or attempt == 2 or not (folder / 'quality.json').exists():
                        raise
                    quality = json.loads((folder / 'quality.json').read_text())
                    if quality.get('passed'):
                        raise
                    revision = attempt + 1
                    atomic_json(folder / f'quality_v{attempt}.json', quality)
                    atomic_json(folder / f'plan_v{attempt}.json', plan)
                    set_status(folder, 'repairing', f'检查发现不可达目标或支撑问题，正在进行第 {revision}/2 次修正…')
                    repair = planning_prompt(request['prompt'], job_id)
                    repair += f'\n本次为修正，原计划：{json.dumps(plan,ensure_ascii=False)}\n几何检查：{json.dumps(quality,ensure_ascii=False)}\n必须保留用户要求的动作、角色、部位、结果与时长。通过调整间隔、加入必要的小步靠近、缩小不必要移动解决不可达目标。不要降低检查阈值。提交改用 print(bpy.wm_duel_plan("{job_id}", plan, revision={revision}))。'
                    run_logged(folder, ['hermes', '-z', repair], f'repair_{revision}.log', 240)
                    candidate = folder / f'plan_candidate_{revision}.json'
                    if not candidate.exists():
                        raise RuntimeError('Hermes 未提交有效修正计划')
                    proposed = validate_plan(json.loads(candidate.read_text()), job_id)
                    before = [(e['actor'],e['motion'],e['region'],e['outcome'],e['side'],e.get('reaction')) for e in plan['events'] if e['kind']=='strike']
                    after = [(e['actor'],e['motion'],e['region'],e['outcome'],e['side'],e.get('reaction')) for e in proposed['events'] if e['kind']=='strike']
                    if before != after or plan['duration_seconds'] != proposed['duration_seconds'] or plan['camera'] != proposed['camera'] or plan['pace'] != proposed['pace']:
                        raise RuntimeError('修正改变了明确的攻击要求，拒绝替换')
                    plan = proposed
                    for filename in ('semantic_review.json','semantic_review.log'):
                        path=folder/filename
                        if path.exists(): path.rename(folder/(f'semantic_review_v{attempt}'+path.suffix))
                    atomic_json(folder / 'plan.json', plan)
                    review_planned_request(folder)
                    (folder / 'quality.json').unlink(missing_ok=True)
                    set_status(folder, 'building', '正在执行修正后的 IK 时间线…')
            return render_scene_job(folder,'duel',2,plan['frame_count'],plan['fps'])
        except Exception as error:
            result = {'ok': False, 'job_id': job_id, 'error': str(error),**semantic_result_fields(folder)}
            atomic_json(folder / 'result.json', result)
            set_status(folder, 'failed', str(error), result=result)
            return result


def submit(prompt, idempotency_key=None, mode='duel'):
    JOBS.mkdir(parents=True, exist_ok=True)
    if idempotency_key:
        if not isinstance(idempotency_key, str) or len(idempotency_key) > 200:
            raise ValueError('幂等键格式不正确')
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        index = JOBS / ('.request_' + digest + '.json')
        with (JOBS / '.submit.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if index.exists():
                previous = json.loads(index.read_text())
                if previous['prompt'] != prompt or previous.get('mode', 'duel') != mode:
                    raise ValueError('相同幂等键不能用于不同提示词')
                return previous['job_id']
            job_id = submit(prompt, mode=mode)
            atomic_json(index, {'prompt': prompt, 'job_id': job_id, 'mode': mode})
            return job_id
    job_id = uuid.uuid4().hex[:16]
    folder = job_directory(job_id)
    folder.mkdir(parents=True)
    request = {'prompt': prompt, 'mode': mode, 'created_at': time.time(),
               'generation_contract_version': 3 if mode == 'auto' else 1}
    if mode == 'auto':
        request['execution_strategy'] = 'hermes_mcp'
        request['character_policy'] = 'library_beta'
    atomic_json(folder / 'request.json', request)
    set_status(folder, 'queued', '任务已提交，等待生成…')
    spawn(job_id)
    return job_id


def spawn(job_id, planned=False):
    folder = job_directory(job_id)
    with (folder / 'worker.log').open('a') as logfile:
        command = [sys.executable, str(PROJECT / 'scripts' / 'duel_worker.py'), '--job', job_id]
        if planned:
            command.append('--planned')
        subprocess.Popen(command, cwd=PROJECT, stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)


def status(job_id):
    folder = job_directory(job_id)
    result = json.loads((folder / 'status.json').read_text())
    if result['stage'] not in ('complete', 'failed') and result.get('pid'):
        try:
            os.kill(result['pid'], 0)
        except ProcessLookupError:
            set_status(folder, 'failed', '任务进程已退出，请根据日志重试')
            result = json.loads((folder / 'status.json').read_text())
    if (folder / 'render_progress.json').exists() and result['stage'] in ('preview', 'rendering'):
        result['render_progress'] = json.loads((folder / 'render_progress.json').read_text())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True)
    parser.add_argument('--planned', action='store_true')
    options = parser.parse_args()
    outcome = run_job(options.job, options.planned)
    print(json.dumps(outcome, ensure_ascii=False))
    sys.exit(0 if outcome['ok'] else 1)
