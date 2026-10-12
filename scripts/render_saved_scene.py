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
from pathlib import Path
import json

argv = sys.argv
if "--" in argv:
    argv = argv[argv.index("--") + 1:]
else:
    argv = []

p = argparse.ArgumentParser()
p.add_argument("--output", required=True)
p.add_argument("--fps", type=int, default=30)
p.add_argument("--resolution", default="1280x720")
p.add_argument("--progress")
args = p.parse_args(argv)

import bpy

scene = bpy.context.scene
scene.render.fps = args.fps
scene.render.fps_base = 1
width, height = map(int, args.resolution.split('x'))
if width <= 0 or height <= 0 or width % 2 or height % 2:
    raise ValueError('分辨率需要正偶数')
scene.render.resolution_x = width
scene.render.resolution_y = height
scene.render.resolution_percentage = 100
if hasattr(scene, 'eevee') and hasattr(scene.eevee, 'taa_render_samples'):
    scene.eevee.taa_render_samples = 16 if width < 1000 else 32

# 渲染 PNG 序列
outdir = os.path.dirname(os.path.abspath(args.output))
base = os.path.splitext(os.path.basename(args.output))[0]
frames_dir = os.path.join(outdir, base + "_frames")
os.makedirs(frames_dir, exist_ok=True)

scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.image_settings.compression = 15
scene.render.filepath = os.path.join(frames_dir, base + "_")

def progress(scene, *_):
    if args.progress:
        path = Path(args.progress)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'frame': scene.frame_current, 'total': scene.frame_end, 'resolution': args.resolution}))
        os.replace(temporary, path)

bpy.app.handlers.render_write.append(progress)
print(f"渲染 {scene.frame_start}-{scene.frame_end} 帧 @ {args.fps}fps", flush=True)
bpy.ops.render.render(animation=True)
expected = scene.frame_end - scene.frame_start + 1
if len(list(Path(frames_dir).glob(base + '_*.png'))) != expected:
    raise RuntimeError('渲染序列帧数不正确')

# ffmpeg 编码
ffmpeg_bin = str(Path(__file__).resolve().parents[1] / 'ffmpeg')
if not os.path.exists(ffmpeg_bin):
    ffmpeg_bin = "ffmpeg"
cmd = [
    ffmpeg_bin, "-y", "-v", "error",
    "-framerate", str(args.fps),
    "-start_number", str(scene.frame_start),
    "-i", os.path.join(frames_dir, base + "_%04d.png"),
    "-frames:v", str(expected), "-c:v", "libx264", "-threads", "8", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
    args.output + '.encoding.mp4',
]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
if r.returncode != 0:
    raise RuntimeError(f"FFMPEG_FAILED {r.stderr[-1000:]}")
else:
    os.replace(args.output + '.encoding.mp4', args.output)
    print(f"RENDER_DONE {args.output}")
