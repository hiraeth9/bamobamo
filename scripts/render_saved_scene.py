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
import math
import shutil
from fractions import Fraction

argv = sys.argv
if "--" in argv:
    argv = argv[argv.index("--") + 1:]
else:
    argv = []

p = argparse.ArgumentParser()
p.add_argument("--output", required=True)
p.add_argument("--fps", type=int, default=30)
p.add_argument("--preserve-scene-fps", action="store_true",
               help="Use the saved fps/fps_base without retiming the animation")
p.add_argument("--resolution", default="1280x720")
p.add_argument("--progress")
args = p.parse_args(argv)

import bpy

scene = bpy.context.scene
if not args.preserve_scene_fps:
    if args.fps <= 0:
        raise ValueError('FPS需要正数')
    scene.render.fps = args.fps
    scene.render.fps_base = 1
if scene.render.fps <= 0 or not math.isfinite(scene.render.fps_base) or scene.render.fps_base <= 0:
    raise ValueError('场景FPS/fps_base无效')
fps = scene.render.fps / scene.render.fps_base
# fps_base is stored as float32 in Blender; recover a usable ffmpeg rational.
rate = Fraction(scene.render.fps, 1) / Fraction(str(scene.render.fps_base)).limit_denominator(1000000)
if scene.frame_end < scene.frame_start:
    raise ValueError('连续视频需要有效帧区间')
scene.frame_step = 1  # Export every timeline frame; saved preview step is metadata only.
expected = scene.frame_end - scene.frame_start + 1
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
if Path(frames_dir).is_symlink():
    raise ValueError('临时帧目录不能为符号链接')
if os.path.exists(frames_dir):
    shutil.rmtree(frames_dir)
os.makedirs(frames_dir, exist_ok=True)

scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.image_settings.compression = 15
scene.render.filepath = os.path.join(frames_dir, base + "_")
scene.render.use_overwrite = True
expected_names = {Path(scene.render.frame_path(frame=frame)).name
                  for frame in range(scene.frame_start, scene.frame_end + 1)}

def progress(scene, *_):
    if args.progress:
        path = Path(args.progress)
        temporary = path.with_suffix('.tmp')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({'frame': scene.frame_current,
                                        'done': scene.frame_current - scene.frame_start + 1,
                                        'total': expected, 'frame_start': scene.frame_start,
                                        'frame_end': scene.frame_end, 'fps': fps,
                                        'resolution': args.resolution}))
        os.replace(temporary, path)

bpy.app.handlers.render_write.append(progress)
print(f"渲染 {scene.frame_start}-{scene.frame_end} 帧 @ {fps}fps", flush=True)
bpy.ops.render.render(animation=True)
actual_names = {path.name for path in Path(frames_dir).glob('*.png')}
if actual_names != expected_names or any((Path(frames_dir) / name).stat().st_size == 0 for name in actual_names):
    raise RuntimeError('渲染序列帧号集合不正确或存在空帧')

# ffmpeg 编码
ffmpeg_bin = str(Path(__file__).resolve().parents[1] / 'ffmpeg')
if not os.path.exists(ffmpeg_bin):
    ffmpeg_bin = "ffmpeg"
cmd = [
    ffmpeg_bin, "-y", "-v", "error",
    "-framerate", str(rate.numerator) + '/' + str(rate.denominator),
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
