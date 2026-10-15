"""Restore the saved free-director baseline, preserving all job/media history.

Dry run is the default. --restore writes only this change's code/knowledge;
the current versions are archived first. --target supports an isolated drill.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
MANAGED = [
    'scripts/agent_director.py', 'scripts/agent_reference.py',
    'scripts/preload_helpers.py', 'scripts/agent_scene.py',
    'scripts/agent_scene_probe.py', 'scripts/duel_worker.py',
    'scripts/agent_character.py', 'scripts/scan_robot_assets.py',
    'scripts/agent_motion_catalog.py', 'scripts/agent_motion.py',
    'scripts/agent_motion_controls.py', 'scripts/agent_motion_timeline.py',
    'scripts/agent_locomotion.py',
    'scripts/agent_motion_quality.py', 'white-model-director/SKILL.md',
    'scripts/agent_motion_cameras.py', 'scripts/agent_camera_quality.py',
    'docs/机器人混合动作工具说明.md', 'README.md',
    'scripts/agent_preview.py',
    '动作库机器人与混合动作生成实施计划.md', '动作库机器人与混合动作生成实施计划.docx',
    'knowledge/characters/catalog.json', 'knowledge/motions/catalog.json',
    'tests/test_agent_director.py', 'tests/test_agent_scene.py',
    'tests/test_hermes_worker.py', 'tests/test_agent_character.py',
    'tests/test_agent_character_blender.py', 'tests/test_agent_motion_catalog.py',
    'tests/test_agent_character_material_blender.py',
    'tests/test_agent_character_remove_blender.py',
    'tests/test_agent_motion_blender.py', 'tests/test_agent_motion_timeline.py',
    'tests/test_robot_hybrid_blender.py', 'tests/test_robot_recipe_adapters_blender.py',
    'tests/test_robot_pov_blender.py', 'tests/test_agent_motion_performance_blender.py',
    'tests/test_agent_preview.py', 'tests/test_agent_preview_blender.py',
    'tests/test_agent_motion_local_capture_blender.py',
    'tests/test_agent_locomotion_blender.py',
    'tests/test_agent_camera_quality.py', 'tests/test_agent_camera_quality_blender.py',
    'tests/test_agent_camera_quality_perf_blender.py',
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(backup, target=PROJECT):
    backup, target = Path(backup).resolve(), Path(target).resolve()
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('kind') != 'source_assets_reports_and_job_snapshot':
        raise ValueError('不是本次改造开始前的备份')
    for name, expected in manifest.get('source_sha256', {}).items():
        source = backup / name
        if not source.is_file() or sha(source) != expected:
            raise ValueError('备份源码损坏：' + name)
    entries = []
    for name in MANAGED:
        source, current = backup / name, target / name
        if source.is_symlink() or current.is_symlink():
            raise ValueError('回退文件不能使用符号链接：' + name)
        if source.is_file():
            operation = 'restore' if not current.is_file() or sha(current) != sha(source) else 'unchanged'
        else:
            operation = 'archive_and_remove' if current.is_file() else 'absent'
        entries.append({'path': name, 'operation': operation})
    return {'backup': str(backup), 'target': str(target), 'entries': entries,
            'job_and_media_history_preserved': True,
            'character_cache_preserved': True,
            'services_restart_required': target == PROJECT.resolve()}


def restore(backup, target=PROJECT):
    plan = prepare(backup, target)
    backup, target = Path(plan['backup']), Path(plan['target'])
    lock = None
    try:
        if target == PROJECT.resolve():
            lock = (target / 'output/jobs/.pipeline.lock').open('a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('正在生成任务；请待任务结束再回退') from error
        archive = target.parent / ('robot-hybrid-rollback-archive-' + str(time.time_ns()))
        archive.mkdir(mode=0o700, parents=True)
        for entry in plan['entries']:
            name, operation = entry['path'], entry['operation']
            if operation in ('unchanged', 'absent'):
                continue
            source, current = backup / name, target / name
            if current.is_file():
                archived = archive / name
                archived.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current, archived)
            if operation == 'archive_and_remove':
                current.unlink()
            else:
                current.parent.mkdir(parents=True, exist_ok=True)
                handle, temp_name = tempfile.mkstemp(prefix='.robot_restore_', dir=current.parent)
                os.close(handle)
                temp = Path(temp_name)
                try:
                    shutil.copy2(source, temp)
                    os.replace(temp, current)
                finally:
                    temp.unlink(missing_ok=True)
        if target == PROJECT.resolve():
            installed = Path('/root/.hermes/skills/creative/white-model-director/SKILL.md')
            if installed.is_file():
                shutil.copy2(installed, archive / 'installed-white-model-director.SKILL.md')
            installed.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / 'installed-white-model-director.SKILL.md'
                         if (backup / 'installed-white-model-director.SKILL.md').is_file()
                         else target / 'white-model-director/SKILL.md', installed)
        for entry in plan['entries']:
            current, source = target / entry['path'], backup / entry['path']
            if source.is_file() and sha(source) != sha(current):
                raise RuntimeError('回退校验失败：' + entry['path'])
            if not source.is_file() and current.exists():
                raise RuntimeError('新文件仍存在：' + entry['path'])
        plan.update(restored=True, archive=str(archive))
        (archive / 'rollback_result.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2)+'\n')
        return plan
    finally:
        if lock is not None:
            lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True)
    parser.add_argument('--target', type=Path, default=PROJECT)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    result = restore(args.backup, args.target) if args.restore else prepare(args.backup, args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
