"""Snapshot-and-spawn previews: the MCP/GUI process never calls render.

The child preserves the saved engine, shaders, cameras, animation and FPS.
Only explicitly requested preview pixel size and PNG output settings differ.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
ID_PATTERN = re.compile(r'^[0-9a-f]{32}$')
_RUNNING = {}


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _job(job_id):
    from agent_scene import validate_job
    return validate_job(job_id)


def _preview_folder(job_id, preview_id, create=False):
    if not isinstance(preview_id, str) or not ID_PATTERN.fullmatch(preview_id):
        raise ValueError('preview_id 必须是当前任务返回的32位标识')
    job = _job(job_id)
    root = job / 'agent_previews'
    if root.is_symlink():
        raise ValueError('预览目录不得指向其它位置')
    if create:
        root.mkdir(exist_ok=True)
    folder = root / preview_id
    if folder.is_symlink() or (folder.exists() and folder.resolve().parent != root.resolve()):
        raise ValueError('预览目录不属于本任务')
    if create:
        folder.mkdir()
    if not folder.is_dir():
        raise ValueError('预览不存在，请使用当前任务的 preview_id')
    for name in ('status.json', '.status.lock', 'snapshot.blend', 'render.log'):
        if (folder / name).is_symlink():
            raise ValueError('预览文件不得使用符号链接')
    return folder


def _read(folder):
    document = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
    if (not isinstance(document, dict) or document.get('job_id') != folder.parent.parent.name
            or document.get('preview_id') != folder.name):
        raise ValueError('预览状态的任务或标识不一致')
    return document


def _update(folder, changes, initial=None):
    with (folder / '.status.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        document = _read(folder) if (folder / 'status.json').exists() else dict(initial or {})
        document.update(changes)
        document['updated_at'] = time.time()
        temporary = folder / ('.status_' + uuid.uuid4().hex + '.json')
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        os.replace(temporary, folder / 'status.json')
    return document


def _process_identity(pid):
    try:
        # The executable's name can contain spaces or parentheses.
        tail = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return {'pid': int(pid), 'start_ticks': tail[19], 'boot_id': boot, 'state': tail[0]}
    except (OSError, ValueError, TypeError, IndexError):
        return None


def _alive(identity):
    if not isinstance(identity, dict):
        return False
    actual = _process_identity(identity.get('pid'))
    return bool(actual and actual['state'] not in ('Z', 'X') and
                actual['start_ticks'] == identity.get('start_ticks') and actual['boot_id'] == identity.get('boot_id'))


def _frames(scene, frames):
    if frames is None:
        frames = [scene.frame_start, (scene.frame_start + scene.frame_end) // 2, scene.frame_end]
    if not isinstance(frames, (list, tuple)) or not frames:
        raise ValueError('frames 必须是非空实际帧列表')
    result = []
    for value in frames:
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or int(value) != value or
                not scene.frame_start <= value <= scene.frame_end):
            raise ValueError('预览帧必须是场景实际范围内的整数')
        if int(value) not in result:
            result.append(int(value))
    return result


def _resolution(resolution):
    if resolution is None:
        return None
    if (not isinstance(resolution, (list, tuple)) or len(resolution) != 2 or
            any(type(value) is not int or value <= 0 for value in resolution)):
        raise ValueError('resolution 必须是两个正整数，None保持原分辨率')
    return list(resolution)


def _summary(scene):
    return {'scene': scene.name, 'render_engine': scene.render.engine,
            'fps': scene.render.fps, 'fps_base': scene.render.fps_base,
            'frame_start': scene.frame_start, 'frame_end': scene.frame_end,
            'source_resolution': [scene.render.resolution_x, scene.render.resolution_y],
            'source_resolution_percentage': scene.render.resolution_percentage,
            'camera': scene.camera.name if scene.camera else None,
            'meshes': sorted([{'object': obj.name, 'vertices': len(obj.data.vertices),
                               'materials': [slot.material.name if slot.material else None for slot in obj.material_slots]}
                              for obj in scene.objects if obj.type == 'MESH'], key=lambda item: item['object'])}


def _environment():
    from duel_worker import child_environment
    return child_environment()


def preview_start(job_id, frames=None, resolution=(640, 360)):
    import bpy
    scene = bpy.context.scene
    _job(job_id)
    owner = scene.get('wm_actor_job_id')
    if owner and owner != job_id:
        raise ValueError('当前人物场景不属于该预览任务')
    requested_frames = _frames(scene, frames)
    requested_resolution = _resolution(resolution)
    preview_id = uuid.uuid4().hex
    folder = _preview_folder(job_id, preview_id, create=True)
    snapshot = folder / 'snapshot.blend'
    original_filepath = bpy.data.filepath
    source = _summary(scene)
    started = time.monotonic()
    document = {'version': 1, 'job_id': job_id, 'preview_id': preview_id,
                'status': 'saving_snapshot', 'created_at': time.time(),
                'requested_frames': requested_frames, 'preview_resolution': requested_resolution,
                'source': source, 'source_gui_filepath': original_filepath,
                'request_sha256': _sha(_job(job_id) / 'request.json'), 'images': [],
                'scope': 'Actual independent snapshot frames; not semantic or naturalness certification'}
    _update(folder, {}, initial=document)
    try:
        # Relative path remapping applies to the saved copy, not the current
        # scene filename. No render, frame_set, material edit or pack occurs.
        result = bpy.ops.wm.save_as_mainfile(filepath=str(snapshot), copy=True,
                                            relative_remap=True, compress=True, check_existing=False)
        if 'FINISHED' not in result or not snapshot.is_file() or not snapshot.stat().st_size:
            raise RuntimeError('Blender未保存实际预览快照')
        if bpy.data.filepath != original_filepath or _summary(scene) != source:
            raise RuntimeError('预览快照操作改变了当前场景或文件路径')
        _update(folder, {'status': 'queued', 'snapshot_sha256': _sha(snapshot)})
        command = [str(PROJECT / 'blender'), '-b', '-t', '1', '--python-exit-code', '1',
                   '--python', str(Path(__file__).resolve()), '--', '--render', job_id, preview_id]
        log = (folder / 'render.log').open('w', encoding='utf-8')
        try:
            process = subprocess.Popen(command, cwd=PROJECT, env=_environment(),
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        finally:
            log.close()
        _RUNNING[(job_id, preview_id)] = process
        identity = _process_identity(process.pid)
        _update(folder, {'process': identity, 'start_return_elapsed_seconds': time.monotonic() - started,
                         'render_started_by_gui': False, 'child_threads': 1})
        return {'ok': True, 'job_id': job_id, 'preview_id': preview_id,
                'status': 'queued', 'snapshot_sha256': _sha(snapshot),
                'requested_frames': requested_frames, 'preview_resolution': requested_resolution,
                'start_return_elapsed_seconds': time.monotonic() - started,
                'message': '真实快照已保存，后台独立渲染预览；调用preview_status查询，不在MCP内等待或render。'}
    except Exception as error:
        _update(folder, {'status': 'failed', 'error': f'{type(error).__name__}: {error}'})
        raise


def preview_status(job_id, preview_id):
    folder = _preview_folder(job_id, preview_id)
    document = _read(folder)
    process = _RUNNING.get((job_id, preview_id))
    exit_code = process.poll() if process is not None else None
    alive = exit_code is None and _alive(document.get('process'))
    if document['status'] not in ('complete', 'failed') and not alive:
        # A short spawn race can occur before the child records its identity.
        if time.time() - document.get('updated_at', 0) > 3:
            document = _update(folder, {'status': 'failed', 'error': '预览子进程已终止且未完成请求帧',
                                        'process_exit_code': exit_code})
    images = []
    for record in document.get('images', []):
        name = record.get('filename')
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.png'):
            raise ValueError('预览图片路径不属于该预览')
        path = folder / name
        if path.is_symlink():
            raise ValueError('预览图不得使用符号链接')
        available = path.is_file() and _sha(path) == record.get('sha256')
        item = {**record, 'path': str(path), 'available': available}
        try:
            relative = path.relative_to(PROJECT)
            item['url'] = '/' + str(relative)
        except ValueError:
            pass
        images.append(item)
    result = {**document, 'ok': document['status'] != 'failed', 'process_alive': alive,
              'process_exit_code': exit_code if exit_code is not None else document.get('process_exit_code'), 'images': images,
              'completed_frames': len([item for item in images if item['available']]),
              'requested_frame_count': len(document['requested_frames']),
              'request_matches_current': document['request_sha256'] == _sha(_job(job_id) / 'request.json')}
    if document['status'] == 'complete' and (not all(item['available'] for item in images)
            or len(images) != len(document['requested_frames'])):
        result.update(ok=False, status='failed', error='预览图缺失或内容已改变，不能视为完整预览')
    if not result['request_matches_current']:
        result.update(ok=False, status='failed', error='任务原请求已改变，预览只属于保存时的请求')
    return result


def _png(path):
    with path.open('rb') as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR':
        raise RuntimeError('渲染没有生成实际PNG')
    width, height = struct.unpack('>II', header[16:24])
    if width <= 0 or height <= 0:
        raise RuntimeError('预览PNG尺寸无效')
    return [width, height]


def _render(job_id, preview_id):
    import bpy
    from agent_scene_probe import _camera_for_frame
    folder = _preview_folder(job_id, preview_id)
    document = _read(folder)
    snapshot = folder / 'snapshot.blend'
    if not snapshot.is_file() or _sha(snapshot) != document.get('snapshot_sha256'):
        raise ValueError('预览快照缺失或哈希不一致')
    _update(folder, {'status': 'loading', 'process': _process_identity(os.getpid())})
    try:
        result = bpy.ops.wm.open_mainfile(filepath=str(snapshot), load_ui=False, use_scripts=False)
        if 'FINISHED' not in result:
            raise RuntimeError('Blender不能独立加载预览快照')
        scene = bpy.context.scene
        actual = _summary(scene)
        if actual != document['source']:
            raise RuntimeError('预览重载改变了原引擎、相机、FPS或原始网格/材质归属')
        resolution = document['preview_resolution']
        if resolution is not None:
            scene.render.resolution_x, scene.render.resolution_y = resolution
            scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = 'PNG'
        scene.render.use_file_extension = True
        # Auxiliary File Output nodes are side effects, not the image's
        # composition. Prevent writing into original/historical directories.
        suppressed = []
        compositor = getattr(scene, 'node_tree', None) or getattr(scene, 'compositing_node_group', None)
        if compositor:
            for node in compositor.nodes:
                if node.type == 'OUTPUT_FILE' and not node.mute:
                    node.mute = True
                    suppressed.append(node.name)
        _update(folder, {'loaded_source': actual, 'auxiliary_file_outputs_suppressed': suppressed,
                         'status': 'rendering'})
        images = []
        for frame in document['requested_frames']:
            scene.frame_set(frame)
            camera = _camera_for_frame(scene, frame)
            if camera is None or camera.type != 'CAMERA':
                raise ValueError(f'预览第{frame}帧缺少实际相机')
            scene.camera = camera
            filename = f'frame_{frame:+07d}.png'
            path = folder / filename
            if path.is_symlink():
                raise ValueError('预览图片路径不得使用符号链接')
            scene.render.filepath = str(path)
            _update(folder, {'status': 'rendering', 'current_frame': frame})
            bpy.ops.render.render(write_still=True)
            record = {'frame': frame, 'filename': filename, 'sha256': _sha(path),
                      'resolution': _png(path), 'camera': camera.name,
                      'camera_matrix_world': [list(row) for row in camera.matrix_world],
                      'render_engine': scene.render.engine,
                      'fps': scene.render.fps, 'fps_base': scene.render.fps_base}
            images.append(record)
            _update(folder, {'images': images})
        if _sha(snapshot) != document['snapshot_sha256']:
            raise RuntimeError('预览渲染期间快照已变化')
        _update(folder, {'status': 'complete', 'completed_at': time.time(), 'current_frame': None,
                         'process_exit_code': 0})
    except Exception as error:
        _update(folder, {'status': 'failed', 'error': f'{type(error).__name__}: {error}', 'process_exit_code': 1})
        raise


if __name__ == '__main__':
    sys.path.insert(0, str(PROJECT / 'scripts'))
    parser = argparse.ArgumentParser()
    parser.add_argument('--render', nargs=2, metavar=('JOB_ID', 'PREVIEW_ID'), required=True)
    arguments = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    _render(*arguments.render)
