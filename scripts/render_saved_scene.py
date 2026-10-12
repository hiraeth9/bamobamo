"""
渲染已保存的 .blend 场景为 mp4（headless 子进程）。
被 preload_helpers.py 的 wm_render() 调用。

用法：
  blender -b <scene>.blend --python render_saved_scene.py -- --output /path/out.mp4 --fps 30
"""
import argparse
import os
import subprocess
import sys

argv = sys.argv
if "--" in argv:
    argv = argv[argv.index("--") + 1:]
else:
    argv = []

p = argparse.ArgumentParser()
p.add_argument("--output", required=True)
p.add_argument("--fps", type=int, default=30)
args = p.parse_args(argv)

import bpy

scene = bpy.context.scene

# 渲染 PNG 序列
outdir = os.path.dirname(os.path.abspath(args.output))
base = os.path.splitext(os.path.basename(args.output))[0]
frames_dir = os.path.join(outdir, base + "_frames")
os.makedirs(frames_dir, exist_ok=True)

scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.filepath = os.path.join(frames_dir, base + "_")

print(f"渲染 {scene.frame_start}-{scene.frame_end} 帧 @ {args.fps}fps")
bpy.ops.render.render(animation=True)

# ffmpeg 编码
ffmpeg_bin = "/root/rivermind-data/bamo/ffmpeg"
if not os.path.exists(ffmpeg_bin):
    ffmpeg_bin = "ffmpeg"
cmd = [
    ffmpeg_bin, "-y",
    "-framerate", str(args.fps),
    "-i", os.path.join(frames_dir, base + "_%04d.png"),
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
    args.output,
]
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0:
    print(f"FFMPEG_FAILED {r.stderr[-500:]}")
else:
    print(f"RENDER_DONE {args.output}")
