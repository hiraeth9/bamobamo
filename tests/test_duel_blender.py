"""Blender integration matrix: true IK, contacts, feet, camera and saved-scene reload."""
import argparse
import contextlib
import io
import itertools
import json
from pathlib import Path
import sys
import tempfile
import time
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from duel_plan import atomic_json, validate_plan
from duel_scene import build, point


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    choices = itertools.product(('jab', 'hook', 'kick', 'uppercut'), ('hit', 'block', 'dodge'), ('A', 'B'), ('head', 'body'), ('left', 'right'))
    if not args.full:
        choices = [(m, o, 'A' if i % 2 else 'B', 'head' if i % 3 else 'body', 'left' if i % 2 else 'right') for i, (m, o) in enumerate(itertools.product(('jab', 'hook', 'kick', 'uppercut'), ('hit', 'block', 'dodge')))]
    report = {'checks': [], 'started_at': time.time(), 'scope': 'Fixed-plan Blender execution, not natural-language end-to-end acceptance.'}
    with tempfile.TemporaryDirectory(prefix='bamo-ik-tests-') as directory:
        folder = Path(directory)
        for index, (motion, outcome, actor, region, side) in enumerate(choices):
            plan = validate_plan({'version': 1, 'duration_seconds': 2, 'events': [{'kind': 'strike', 'actor': actor, 'motion': motion, 'outcome': outcome, 'region': region, 'side': side, 'start': .2, 'peak': .85, 'end': 1.8}]}, f'matrix_{index:03d}')
            with contextlib.redirect_stdout(io.StringIO()):
                result = build(plan, folder)
            entry = {'motion': motion, 'outcome': outcome, 'actor': actor, 'region': region, 'side': side,
                     'passed': result['passed'], 'quality': result}
            if result['passed']:
                arm = bpy.data.objects[actor + '_Rig']
                bpy.context.scene.frame_set(27)
                expected = point(arm, side.title() + ('Foot' if motion == 'kick' else 'Hand'))
                bpy.ops.wm.open_mainfile(filepath=str(folder / 'scene.blend'))
                bpy.context.scene.frame_set(27)
                error = (point(bpy.data.objects[actor + '_Rig'], side.title() + ('Foot' if motion == 'kick' else 'Hand')) - expected).length
                entry['reload_error_m'] = error
                entry['passed'] &= error < 1e-5
            report['checks'].append(entry)
            print('MATRIX', index, motion, outcome, actor, region, side, 'PASS' if entry['passed'] else 'FAIL', flush=True)
            atomic_json(Path(__file__).resolve().parents[1] / 'output' / 'duel_blender_regression.json', report)
    failures = [e for e in report['checks'] if not e['passed']]
    report['passed'] = not failures
    report['elapsed_seconds'] = time.time() - report['started_at']
    atomic_json(Path(__file__).resolve().parents[1] / 'output' / 'duel_blender_regression.json', report)
    if failures:
        raise RuntimeError(f'{len(failures)} IK matrix cases failed')


if __name__ == '__main__':
    main()
