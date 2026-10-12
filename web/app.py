"""
白膜视频生成系统 - 轻量后端
流程：网页 POST 提示词 -> subprocess 调 hermes -z -> Hermes 自主选动作并渲染 -> 返回视频
支持 SSE 流式进度推送（/api/generate 返回 text/event-stream）
"""
import json
import os
import re
import subprocess

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context

PROJECT = "/root/rivermind-data/bamo"
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


def build_prompt(user_text):
    actions = list_actions()
    action_list = "\n".join(f"- {a}" for a in actions)
    return f"""你是白膜（白模/素模）参考视频生成导演，请使用 white-model-director 技能来完成这个任务。

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


def sse(payload):
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


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
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt 不能为空"}), 400

    before = set(os.listdir(OUTPUT_DIR)) if os.path.isdir(OUTPUT_DIR) else set()
    full_prompt = build_prompt(prompt)

    @stream_with_context
    def gen():
        yield sse({"type": "status", "stage": "starting", "message": "Agent 已启动，正在解析需求…"})

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

        stdout = "\n".join(stdout_buf)

        # 定位新生成的 mp4
        after = set(os.listdir(OUTPUT_DIR)) if os.path.isdir(OUTPUT_DIR) else set()
        new_files = [f for f in after - before if f.endswith(".mp4")]
        if new_files:
            new_files.sort(key=lambda f: os.path.getmtime(os.path.join(OUTPUT_DIR, f)), reverse=True)
            video = new_files[0]
        else:
            # 兜底：从 stdout 里找 output/xxx.mp4
            m = re.search(r"output/[A-Za-z0-9_\-]+\.mp4", stdout)
            video = m.group(0).split("/")[-1] if m else None

        if video:
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
