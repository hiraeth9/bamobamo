"""Guard the original immutable snapshot when rolling back from IK jobs."""
import argparse
import fcntl
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
BASELINE = PROJECT.parent / 'bamo-backups/20261012-101959-single-character-v1'


def active_jobs():
    scripts = {'duel_worker.py', 'duel_scene.py', 'render_saved_scene.py', 'render_white_model.py',
               'test_duel_blender.py', 'run_duel_natural_regression.py', 'recheck_duel_regression.py'}
    found = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            args = [a.decode(errors='replace') for a in (path / 'cmdline').read_bytes().split(b'\0') if a]
            if args and ((any(Path(a).name == 'hermes' for a in args) and '-z' in args) or any(Path(a).name in scripts for a in args)):
                found.append(int(path.name))
        except OSError:
            continue
    return found


def main():
    parser = argparse.ArgumentParser(description='默认仅核验单人备份；--restore 才执行回退并保留当前版本。')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    command = [sys.executable, str(BASELINE / 'restore.py')]
    if not args.restore:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        print(result.stdout.replace('bash ' + str(BASELINE / 'restore.sh') + ' --restore',
                                   sys.executable + ' ' + str(PROJECT / 'scripts/rollback_to_single.py') + ' --restore'), end='')
        return
    if active_jobs():
        raise RuntimeError('仍有生成或回归任务，请结束后再回退。')
    lock_path = PROJECT / 'output/jobs/.pipeline.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('生成流程正在运行，请结束后再回退。')
        if active_jobs():
            raise RuntimeError('有新任务启动，请结束后再回退。')
        subprocess.run([*command, '--restore'], check=True)


if __name__ == '__main__':
    main()
