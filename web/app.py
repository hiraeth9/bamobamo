"""
白膜视频生成系统 - 轻量后端
流程：原始提示词 -> Hermes 分析 -> MCP 构建真实场景 -> 保存重载 -> 视频核验
支持 SSE 流式进度推送（/api/generate 返回 text/event-stream）
"""
import json
import os
import sys
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context

PROJECT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, os.path.join(PROJECT, 'scripts'))
from duel_worker import submit as submit_duel, status as duel_status
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
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({'error': '请输入自然语言描述'}), 400
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
    if not isinstance(prompt, str):
        return jsonify({'error': 'prompt 必须是文本'}), 400
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
    # The old URL shares the same general director and durable job handling.
    return api_generate()


@app.route("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, threaded=True)
