"""
白膜视频生成系统 - 轻量后端
流程：统一提示词 -> 后台任务 -> Hermes 识别人数并规划 -> 单人/双人执行器 -> 核验视频
支持 SSE 流式进度推送（/api/generate 返回 text/event-stream）
"""
import json
import os
import re
import subprocess
import sys
import time
import uuid
import fcntl
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context

PROJECT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, os.path.join(PROJECT, 'scripts'))
from duel_plan import JOBS, job_directory
from duel_worker import submit as submit_duel, status as duel_status, verify_video
OUTPUT_DIR = os.path.join(PROJECT, "output")
WEB_DIR = os.path.join(PROJECT, "web")
MIXAMO_DIR = os.path.join(PROJECT, "mixamo_fbx")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


def list_actions():
    """列出 mixamo_fbx 下所有动作 FBX（相对路径）"""
    actions = []
    for root, _, files in os.walk(MIXAMO_DIR):
        for f in sorted(files):
            if f.lower().endswith(".fbx"):
                rel = os.path.relpath(os.path.join(root, f), PROJECT)
                actions.append(rel)
    return actions


def build_prompt(user_text, output_name=None):
    actions = list_actions()
    action_list = "\n".join(f"- {a}" for a in actions)
    result = f"""你是白膜（白模/素模）参考视频生成导演，请使用 white-model-director 技能来完成这个任务。

用户使用自然语言描述画面、动作、时长和镜头。动作选材、素材路径和相机参数由你自动决定；不要要求用户提供动作文件名、FBX 路径或 Blender 参数。
动作库用于内部选材，完成后用中文说明实际呈现的动作。用户明确要求多个动作时，要覆盖这些动作意图；不要只循环一个动作，也不要为了凑动作数量加入无关招式。
选择动作时同时考虑描述是否匹配、起收姿势是否接近和是否适合循环。相同类别不代表可以自然衔接，不能仅凭文件名宣称衔接已经验证。
已实测：Kicking.fbx 有明显整体位移且首尾姿势不同，不适合直接循环；原地徒手格斗的踢击可优先考虑 Mma Kick.fbx。Lead Jab.fbx 与 Hook.fbx 首尾姿势接近，可作为拳法组合候选。
完整招式编排、自动姿势匹配和落脚修正尚未实现，不能宣称已有这些能力；遇到素材或渲染能力限制时要如实说明，不能把技术选材问题交给用户。
用户要求人物全身入镜时，默认平视全景：pitch_deg=0、distance_ratio=3.5、fov=50；用户指定时长时将其映射到 orbit_seconds（当前上限15秒）。

内部可用的动作 FBX（相对项目根目录 /root/rivermind-data/bamo 的路径）：
{action_list}

请严格按照技能里的工作流执行：通过 Blender MCP 的 execute_code 依次调用 bpy.wm_clear / wm_import / wm_clay / wm_lighting / wm_camera / wm_render，全程不得调用任何本地 shell 命令或外部脚本。

完成后报告：动作名 + 输出文件 output/xxx.mp4 + 相机参数（俯仰/距离/环绕）+ 是否成功。

用户需求：{user_text}"""
    if output_name:
        result += f'\n本次输出名必须为 {output_name}，调用 bpy.wm_render("{output_name}")，不得改名。'
    return result


def sse(payload):
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def duel_events(job_id):
    yield sse({'type': 'job', 'job_id': job_id})
    previous = None
    while True:
        state = duel_status(job_id)
        progress = state.get('render_progress', {})
        key = (state['stage'], progress.get('frame'))
        if key != previous:
            message = state['message']
            if progress:
                message += f" {progress['frame']}/{progress['total']} 帧"
            yield sse({'type': 'status', 'stage': state['stage'], 'message': message, 'job_id': job_id})
            previous = key
        if state['stage'] == 'complete':
            result = state['result']
            yield sse({'type': 'video', 'video': os.path.basename(result['video']), **result})
            return
        if state['stage'] == 'failed':
            yield sse({'type': 'error', 'message': state['message'], 'job_id': job_id})
            return
        yield ': heartbeat\n\n'
        time.sleep(1)


@app.route('/api/jobs', methods=['POST'])
def create_duel_job():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': '请求必须是 JSON 对象'}), 400
    prompt = data.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        return jsonify({'error': '请输入 1 至 4000 字的自然语言描述'}), 400
    try:
        job_id = submit_duel(prompt.strip(), request.headers.get('Idempotency-Key'), mode='auto')
    except ValueError as error:
        return jsonify({'error': str(error)}), 409
    return jsonify({'job_id': job_id, 'status_url': f'/api/jobs/{job_id}'}), 202


@app.route('/api/jobs/<job_id>')
def read_duel_job(job_id):
    try:
        return jsonify(duel_status(job_id))
    except (ValueError, FileNotFoundError):
        return jsonify({'error': '任务不存在'}), 404


@app.route('/api/jobs/<job_id>/events')
def read_duel_events(job_id):
    try:
        duel_status(job_id)
    except (ValueError, FileNotFoundError):
        return jsonify({'error': '任务不存在'}), 404
    return Response(stream_with_context(duel_events(job_id)), mimetype='text/event-stream')


@app.route("/api/actions", methods=["GET"])
def api_actions():
    return jsonify({"actions": list_actions()})


@app.route("/api/videos", methods=["GET"])
def api_videos():
    """列出历史视频（output/*.mp4），按修改时间倒序，支持分页。"""
    page = max(1, int(request.args.get("page", 1)))
    page_size = max(1, min(50, int(request.args.get("page_size", 6))))

    videos = []
    if os.path.isdir(OUTPUT_DIR):
        for f in os.listdir(OUTPUT_DIR):
            if f.lower().endswith(".mp4"):
                p = os.path.join(OUTPUT_DIR, f)
                videos.append({
                    "name": f,
                    "url": f"/output/{f}",
                    "size": os.path.getsize(p),
                    "mtime": os.path.getmtime(p),
                })
    videos.sort(key=lambda v: v["mtime"], reverse=True)

    total = len(videos)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = start + page_size

    return jsonify({
        "videos": videos[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    })


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': '请求必须是 JSON 对象'}), 400
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or len(prompt) > 4000:
        return jsonify({'error': 'prompt 必须是最多 4000 字的文本'}), 400
    prompt = prompt.strip()
    if not prompt:
        return jsonify({"error": "prompt 不能为空"}), 400

    # Every public request uses the same semantic person-count protocol.
    try:
        job_id = submit_duel(prompt, request.headers.get('Idempotency-Key'), mode='auto')
    except ValueError as error:
        return jsonify({'error': str(error)}), 409
    return Response(stream_with_context(duel_events(job_id)), mimetype='text/event-stream')


@app.route('/api/generate-single', methods=['POST'])
def api_generate_single_legacy():
    data = request.get_json(silent=True) or {}
    prompt = data.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        return jsonify({'error': '请输入 1 至 4000 字的自然语言描述'}), 400
    prompt = prompt.strip()
    output_name = 'single_' + uuid.uuid4().hex[:16]
    full_prompt = build_prompt(prompt, output_name)

    @stream_with_context
    def gen():
        yield sse({"type": "status", "stage": "starting", "message": "Agent 已启动，正在解析需求…"})

        JOBS.mkdir(parents=True, exist_ok=True)
        lock = (JOBS / '.pipeline.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX)
        proc = subprocess.Popen(
            ["hermes", "-z", full_prompt],
            cwd=PROJECT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # 收集全部输出用于报告
        stdout_buf = []

        # 逐行读取 hermes 输出并实时推送
        for line in proc.stdout:
            line = line.rstrip("\n")
            stdout_buf.append(line)
            if line.strip():
                yield sse({"type": "log", "line": line})
        proc.wait()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

        stdout = "\n".join(stdout_buf)

        # Only return this request's verified output; never guess from directory changes.
        video = output_name + '.mp4'
        path = Path(OUTPUT_DIR) / video
        valid = proc.returncode == 0 and path.is_file()
        if valid:
            try:
                probe = subprocess.run([str(Path(PROJECT) / 'ffprobe'), '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(path)], capture_output=True, text=True, timeout=60, check=True)
                frames = int(json.loads(probe.stdout)['streams'][0]['nb_read_frames'])
                verify_video(path, frames)
            except Exception:
                valid = False

        if valid:
            report = ""
            m2 = re.search(r"输出文件[^\n]*", stdout)
            if m2:
                report = m2.group(0).strip()
            yield sse({
                "type": "video",
                "video": video,
                "video_url": f"/output/{video}",
                "report": report,
            })
        else:
            yield sse({
                "type": "error",
                "message": "未生成视频（Hermes 未产出 mp4）",
                "detail": (stdout or "")[-1200:],
            })

    return Response(gen(), mimetype="text/event-stream")


@app.route("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    app.run(host="0.0.0.0", port=port, threaded=True)
