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

from duel_plan import PROJECT, JOBS, atomic_json, job_directory, planning_prompt, validate_plan
from single_plan import validate_single, single_planning_prompt
from scene_cast import validate_cast, automatic_planning_prompt


def set_status(folder, stage, message, **extra):
    old = json.loads((folder / 'status.json').read_text()) if (folder / 'status.json').exists() else {}
    old.update({'job_id': folder.name, 'stage': stage, 'message': message, 'updated_at': time.time(), **extra})
    atomic_json(folder / 'status.json', old)


def child_environment():
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
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
            if (folder / 'single_plan.json').exists():
                if (folder / 'plan.json').exists():
                    raise RuntimeError('任务同时提交了单人和双人计划，拒绝含糊执行')
                single = validate_single(json.loads((folder / 'single_plan.json').read_text()), job_id)
                video = PROJECT / 'output' / f'single_{job_id}.mp4'
                set_status(folder, 'rendering', '正在独立渲染单人视频…')
                command = [str(PROJECT / 'blender'), '-b', '-t', '8', '--python-exit-code', '1', '--python', str(PROJECT / 'scripts' / 'render_white_model.py'), '--',
                           '--fbx', ','.join(single['animations']), '--output', str(video), '--resolution', '1280x720', '--fps', '30',
                           '--frames', str(single['frame_count']), '--orbit_seconds', str(single['duration_seconds']), '--camera_policy', single['camera_policy'],
                           '--pitch_deg', str(single['pitch_deg']), '--distance_ratio', str(single['distance_ratio']), '--fov', str(single['fov'])]
                run_logged(folder, command, 'single_render.log', 900)
                media = verify_video(video, single['frame_count'], 30, (1280,720))
                shutil.rmtree(video.with_name(video.stem + '_frames'), ignore_errors=True)
                result = {'ok': True, 'kind': 'single', 'actor_count': 1, 'job_id': job_id, 'video': str(video.relative_to(PROJECT)), 'video_url': f'/output/{video.name}', 'media': media, 'report': '单人视频已生成并通过媒体核验。'}
                atomic_json(folder / 'result.json', result)
                set_status(folder, 'complete', '单人视频已完成', result=result)
                return result
            plan = validate_plan(json.loads((folder / 'plan.json').read_text()), job_id)
            atomic_json(folder / 'plan.json', plan)
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
                    atomic_json(folder / 'plan.json', plan)
                    (folder / 'quality.json').unlink(missing_ok=True)
                    set_status(folder, 'building', '正在执行修正后的 IK 时间线…')
            quality = json.loads((folder / 'quality.json').read_text())
            if not quality['passed']:
                raise RuntimeError('接触、支撑或构图检查未通过')
            for stage, resolution, output in [('preview', '640x360', folder / 'preview.mp4'), ('rendering', '1280x720', folder / 'final.mp4')]:
                (folder / 'render_progress.json').unlink(missing_ok=True)
                set_status(folder, stage, '正在生成低清预览…' if stage == 'preview' else '检查已通过，正在渲染正式视频…')
                run_logged(folder, [str(PROJECT / 'blender'), '-b', str(folder / 'scene.blend'), '-t', '8', '--python-exit-code', '1',
                                    '--python', str(PROJECT / 'scripts' / 'render_saved_scene.py'), '--', '--output', str(output),
                                    '--fps', str(plan['fps']), '--resolution', resolution, '--progress', str(folder / 'render_progress.json')], stage + '.log', 900)
                media = verify_video(output, plan['frame_count'], plan['fps'], tuple(map(int, resolution.split('x'))))
                shutil.rmtree(output.with_name(output.stem + '_frames'), ignore_errors=True)
            set_status(folder, 'verifying', '正在核验视频帧数、时长和完整解码…')
            destination = PROJECT / 'output' / f'duel_{job_id}.mp4'
            temporary = destination.with_suffix('.tmp')
            shutil.copy2(folder / 'final.mp4', temporary)
            os.replace(temporary, destination)
            result = {'ok': True, 'kind': 'duel', 'actor_count': 2, 'job_id': job_id, 'video': str(destination.relative_to(PROJECT)),
                      'video_url': f'/output/{destination.name}', 'preview_url': f'/output/jobs/{job_id}/preview.mp4',
                      'plan_url': f'/output/jobs/{job_id}/plan.json', 'quality_url': f'/output/jobs/{job_id}/quality.json',
                      'media': media, 'quality_passed': True, 'visual_review_required': True,
                      'report': '双人 IK 视频已生成；自动接触、落脚、构图与媒体检查通过，动作自然度请查看预览。'}
            atomic_json(folder / 'result.json', result)
            set_status(folder, 'complete', '双人视频已完成', result=result)
            return result
        except Exception as error:
            result = {'ok': False, 'job_id': job_id, 'error': str(error)}
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
    atomic_json(folder / 'request.json', {'prompt': prompt, 'mode': mode, 'created_at': time.time()})
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
