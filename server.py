#!/usr/bin/env python3
"""
白膜预演工作台后端 — 轻量 HTTP 服务

职责：
1. 接收前端提交的任务（提示词 + 参数）
2. 通过 socket 连接 Blender MCP addon（127.0.0.1:9876）
3. 把任务拆成 pipeline 阶段，实时暴露进度
4. 提供成品视频列表

接口：
  POST /api/tasks         提交任务  -> { task_id }
  GET  /api/tasks/<id>    查询进度  -> { stage, percent, current_frame, total_frames, message, elapsed }
  GET  /api/videos        视频列表  -> [ { name, url, resolution, created } ]

依赖：仅 Python 标准库（无第三方依赖）。
运行：python3 server.py  （默认 8080 端口）
"""

import json
import os
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

BLENDER_HOST = os.getenv("BLENDER_HOST", "127.0.0.1")
BLENDER_PORT = int(os.getenv("BLENDER_PORT", "9876"))
VIDEO_DIR = os.getenv("VIDEO_DIR", "/root/render_out")
PORT = int(os.getenv("PORT", "8080"))

# 任务状态存储（内存）
TASKS = {}


def send_blender_command(command, timeout=120):
    """通过 socket 向 Blender addon 发送命令"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((BLENDER_HOST, BLENDER_PORT))
    s.sendall(json.dumps(command).encode("utf-8"))
    data = b""
    try:
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            data += chunk
            try:
                json.loads(data.decode("utf-8"))
                break
            except Exception:
                continue
    except socket.timeout:
        pass
    finally:
        s.close()
    return json.loads(data.decode("utf-8", "replace"))


def execute_blender_code(code):
    """执行 Blender Python 代码，返回 (ok, result)"""
    try:
        resp = send_blender_command({"type": "execute_code", "params": {"code": code}}, timeout=300)
        if resp.get("status") == "success":
            return True, resp.get("result", {}).get("result", "")
        return False, resp.get("message", "unknown error")
    except Exception as e:
        return False, str(e)


def run_pipeline(task_id, payload):
    """后台执行 pipeline，更新 TASKS[task_id]"""
    task = TASKS[task_id]
    prompt = payload["prompt"]
    res = payload["resolution"]  # "864x480"
    fps = payload["fps"]
    duration = payload["duration"]
    w, h = res.split("x")
    total_frames = int(duration) * int(fps)
    start = time.time()

    def update(stage, percent, message):
        task.update({
            "stage": stage,
            "percent": percent,
            "message": message,
            "elapsed": round(time.time() - start, 1),
        })

    try:
        # 阶段 0-1：解析提示词 + 生成 scene.json（这里简化：直接转成参数）
        update(1, 12, "生成 scene.json…")
        time.sleep(0.3)

        # 阶段 2：Blender 建场景
        update(2, 30, "Blender 建场景…")
        build_code = f'''
import bpy, math
from mathutils import Vector

# 清空场景
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

# 建白模方块阵列作为占位角色
for i in range(3):
    for j in range(3):
        bpy.ops.mesh.primitive_cube_add(size=1, location=(i*2, j*2, 0))

# 相机
bpy.ops.object.camera_add(location=(8, -8, 6))
cam = bpy.context.object
center = Vector((2, 2, 0))
cam.rotation_mode = 'QUATERNION'
cam.rotation_quaternion = (center - cam.location).to_track_quat('-Z', 'Y')
bpy.context.scene.camera = cam

# 渲染设置
scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.render.resolution_x = {w}
scene.render.resolution_y = {h}
scene.render.fps = {fps}
scene.frame_start = 1
scene.frame_end = {total_frames}
scene.render.image_settings.file_format = 'PNG'
import os
os.makedirs("{VIDEO_DIR}", exist_ok=True)
scene.render.filepath = "{VIDEO_DIR}/frame_"

# 相机环绕动画
radius = 12
height = 6
for frame in range(1, {total_frames}+1):
    angle = (frame - 1) / {total_frames} * math.pi * 2
    x = center.x + radius * math.cos(angle)
    y = center.y + radius * math.sin(angle)
    cam.location = (x, y, height)
    cam.rotation_quaternion = (Vector((center.x, center.y, 0)) - cam.location).to_track_quat('-Z', 'Y')
    cam.keyframe_insert(data_path='location', frame=frame)
    cam.keyframe_insert(data_path='rotation_quaternion', frame=frame)

print("BUILD_DONE")
'''
        ok, result = execute_blender_code(build_code)
        if not ok:
            task["error"] = result
            update(2, 30, f"建场景失败: {result}")
            return
        update(3, 38, "渲染中…")

        # 阶段 3：渲染（逐帧更新）
        # 这里用一次性渲染，实际可轮询帧号
        render_code = f'''
import bpy
bpy.ops.render.render(animation=True)
print("RENDER_DONE")
'''
        ok, result = execute_blender_code(render_code)
        if not ok:
            task["error"] = result
            update(3, 38, f"渲染失败: {result}")
            return

        task["current_frame"] = total_frames
        update(4, 95, "合成视频…")

        # 阶段 4：ffmpeg 合成
        out_name = f"previs_{task_id[:8]}.mp4"
        out_path = f"{VIDEO_DIR}/{out_name}"
        os.system(
            f'ffmpeg -y -framerate {fps} -i {VIDEO_DIR}/frame_%04d.png '
            f'-c:v libx264 -pix_fmt yuv420p -crf 18 {out_path} >/dev/null 2>&1'
        )

        task["video_url"] = f"/api/videos/{out_name}"
        update(5, 100, "完成")

    except Exception as e:
        task["error"] = str(e)
        update(task["stage"], task["percent"], f"错误: {e}")


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        if not os.path.exists(path):
            self._send_json({"error": "not found"}, 404)
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            # 服务前端静态文件
            self._serve_index()
        elif path.startswith("/api/videos/") and len(path) > len("/api/videos/"):
            name = path.split("/api/videos/")[1]
            self._send_file(os.path.join(VIDEO_DIR, name))
        elif path == "/api/videos":
            videos = []
            if os.path.isdir(VIDEO_DIR):
                for f in sorted(os.listdir(VIDEO_DIR)):
                    if f.endswith(".mp4"):
                        videos.append({
                            "name": f,
                            "url": f"/api/videos/{f}",
                            "resolution": "",
                            "created": "",
                        })
            self._send_json(videos)
        elif path.startswith("/api/tasks/"):
            task_id = path.split("/api/tasks/")[1]
            task = TASKS.get(task_id)
            if task:
                self._send_json(task)
            else:
                self._send_json({"error": "not found"}, 404)
        elif path.endswith(".css") or path.endswith(".js") or path.endswith(".html"):
            self._serve_static(path)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/tasks":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid json"}, 400)
                return

            task_id = uuid.uuid4().hex
            TASKS[task_id] = {
                "task_id": task_id,
                "stage": 0,
                "percent": 0,
                "current_frame": 0,
                "total_frames": 0,
                "message": "排队中",
                "elapsed": 0,
            }
            self._send_json({"task_id": task_id})

            # 后台执行 pipeline
            t = threading.Thread(target=run_pipeline, args=(task_id, payload), daemon=True)
            t.start()
        else:
            self._send_json({"error": "not found"}, 404)

    def _serve_index(self):
        base = os.path.dirname(os.path.abspath(__file__))
        # server.py 在 frontend 上一级，前端在 frontend/ 下
        idx = os.path.join(base, "frontend", "index.html")
        if not os.path.exists(idx):
            idx = os.path.join(base, "index.html")
        self._send_file_text(idx)

    def _serve_static(self, path):
        base = os.path.dirname(os.path.abspath(__file__))
        full = os.path.join(base, "frontend", path.lstrip("/"))
        if not os.path.exists(full):
            full = os.path.join(base, path.lstrip("/"))
        self._send_file_text(full)

    def _send_file_text(self, path):
        if not os.path.exists(path):
            self._send_json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(path)[1]
        ctype = {"html": "text/html", "css": "text/css", "js": "application/javascript"}.get(
            ext.lstrip("."), "text/plain"
        )
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # 静默，避免刷屏


if __name__ == "__main__":
    print(f"白膜预演工作台后端启动: http://0.0.0.0:{PORT}")
    print(f"Blender MCP: {BLENDER_HOST}:{BLENDER_PORT}")
    print(f"视频目录: {VIDEO_DIR}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭")
