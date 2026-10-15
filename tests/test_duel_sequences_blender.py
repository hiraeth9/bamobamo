"""Replay real failed natural-language sequences, including recoil into kick.

Fixtures preserve all attacks, recipients, reactions, timing and camera cuts.
This regression checks execution geometry and persistence; it does not rerun
the language model or certify visual realism.
"""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from duel_plan import atomic_json, validate_plan
from duel_scene import build, point


def main():
    checks = []
    for fixture in sorted((ROOT / 'tests/fixtures/combat_sequences').glob('*.json')):
        raw = json.loads(fixture.read_text())
        plan = validate_plan(raw, fixture.stem)
        with tempfile.TemporaryDirectory(prefix='bamo-sequence-') as name:
            folder = Path(name)
            with contextlib.redirect_stdout(io.StringIO()):
                quality = build(plan, folder)
            expected = []
            for event in plan['events']:
                if event['kind'] == 'strike':
                    frame = round(event['peak'] * plan['fps']) + 1
                    bpy.context.scene.frame_set(frame)
                    expected.append((frame, bpy.context.scene.camera.name,
                        {actor: point(bpy.data.objects[actor+'_Rig'], 'Head').copy() for actor in ('A','B')}))
            bpy.ops.wm.open_mainfile(filepath=str(folder/'scene.blend'))
            reload_error = 0
            cameras_retained = True
            for frame, camera, heads in expected:
                bpy.context.scene.frame_set(frame)
                cameras_retained &= bpy.context.scene.camera.name == camera
                reload_error = max(reload_error, max(
                    (point(bpy.data.objects[actor+'_Rig'], 'Head')-heads[actor]).length for actor in heads))
            check = {'fixture': fixture.name, 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
                     'passed': quality['passed'] and reload_error < 1e-5 and cameras_retained,
                     'reload_error_m': reload_error, 'cameras_retained': cameras_retained, 'quality': quality}
            checks.append(check)
            print('SEQUENCE', fixture.stem, 'PASS' if check['passed'] else 'FAIL', flush=True)
            atomic_json(ROOT/'reports/duel_sequences_regression.json', {'checks': checks})
    report = {'passed': all(c['passed'] for c in checks), 'checks': checks,
              'scope': 'Three real 15-second plans; original/repaired dense combat and POV, complete geometry QA and saved-scene reload.'}
    atomic_json(ROOT/'reports/duel_sequences_regression.json', report)
    if not report['passed']:
        raise RuntimeError('Combat sequence regression failed')


if __name__ == '__main__':
    main()
