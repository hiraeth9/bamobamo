"""Impact/recoil/pursuit/fall and cinematic camera persistence integration checks."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
from duel_plan import atomic_json, validate_plan
from duel_scene import build, point
from duel_cameras import shot_at
from test_duel_impacts import impact_plan


def cases():
    for attacker in ('A', 'B'):
        raw = impact_plan()
        # Fractional cuts must begin on the first frame at/after their requested time.
        raw['camera']['shots'][0]['end'] = 4.75
        raw['camera']['shots'][1]['start'] = 4.75
        if attacker == 'A':
            for event in raw['events']:
                event['actor'] = 'B' if event['actor'] == 'A' else 'A'
            raw['camera']['shots'][1]['actor'] = 'A'
        yield 'fall_pov_' + attacker, raw
    for motion, actor in (('kick', 'A'), ('kick', 'B'), ('uppercut', 'A'), ('hook', 'B')):
        other = 'B' if actor == 'A' else 'A'
        raw = {'version': 1, 'duration_seconds': 5, 'events': [
            {'kind': 'strike', 'actor': actor, 'motion': motion, 'side': 'left', 'region': 'body', 'outcome': 'hit',
             'intensity': .95, 'reaction': 'stagger', 'start': .2, 'peak': .9, 'end': 1.7},
            {'kind': 'strike', 'actor': actor, 'motion': 'jab', 'side': 'right', 'region': 'head', 'outcome': 'hit',
             'intensity': .4, 'reaction': 'recoil', 'start': 2, 'peak': 2.6, 'end': 3.3}]}
        yield 'pursuit_' + motion + '_' + actor, raw
    for outcome in ('block', 'dodge'):
        yield 'no_hit_' + outcome, {'version': 1, 'duration_seconds': 3, 'events': [
            {'kind': 'strike', 'actor': 'B', 'motion': 'jab', 'region': 'head', 'outcome': outcome,
             'intensity': 1, 'start': .2, 'peak': .9, 'end': 1.7}]}


def main():
    started = time.monotonic()
    report = {'checks': [], 'scope': 'True Blender geometry and saved camera/visibility/IK state; visual review remains required.'}
    with tempfile.TemporaryDirectory(prefix='bamo-impact-tests-') as name:
        folder = Path(name)
        for key, raw in cases():
            plan = validate_plan(raw, key.lower())
            with contextlib.redirect_stdout(io.StringIO()):
                quality = build(plan, folder)
            errors = []
            trajectory = json.loads((folder / 'trajectory.json').read_text())['frames']
            for event, contact in zip([e for e in plan['events'] if e['kind'] == 'strike'], quality['contacts']):
                if event['outcome'] == 'hit':
                    if contact['reaction_sync_frames'] > 2:
                        errors.append('late reaction')
                    if event['reaction'] == 'stagger' and contact['recoil_step_m'] < .4:
                        errors.append('no persistent backward step')
                elif contact.get('reaction') in ('stagger', 'knockdown'):
                    errors.append('non-hit causes impact')
            falling = any(e.get('reaction') == 'knockdown' for e in plan['events'])
            sample_frames = (145, 154, 170, plan['frame_count']) if falling else (28, 55, 79, plan['frame_count'])
            expected = []
            for frame in sample_frames:
                bpy.context.scene.frame_set(frame)
                expected.append((frame, bpy.context.scene.camera.name,
                                 {k: point(bpy.data.objects[k + '_Rig'], 'Head').copy() for k in ('A', 'B')}))
            bpy.ops.wm.open_mainfile(filepath=str(folder / 'scene.blend'))
            for frame, camera, points in expected:
                bpy.context.scene.frame_set(frame)
                if bpy.context.scene.camera.name != camera:
                    errors.append('camera cut lost on reload')
                shot = shot_at(plan, frame)
                for actor_id in ('A', 'B'):
                    if (point(bpy.data.objects[actor_id + '_Rig'], 'Head') - points[actor_id]).length > 1e-5:
                        errors.append('pose changed on reload')
                    head = bpy.data.objects.get(actor_id + '_POV_Head')
                    if head and head.hide_render != (shot['kind'] == 'pov' and shot['actor'] == actor_id):
                        errors.append('POV self-head visibility lost')
            if falling:
                victim = 'A' if plan['events'][-1]['actor'] == 'B' else 'B'
                bpy.context.scene.frame_set(plan['frame_count'])
                rig = bpy.data.objects[victim + '_Rig']
                if point(rig, 'Head').z > .45 or point(rig, 'Hips').z > .35:
                    errors.append('victim stood up after knockdown')
                if any(c.influence > .001 for bone in rig.pose.bones for c in bone.constraints):
                    errors.append('standing IK remains active after fall')
                root_end = trajectory[-1]['roots'][victim]
                root_near_end = trajectory[-15]['roots'][victim]
                if any(abs(a-b) > 1e-5 for a,b in zip(root_end, root_near_end)):
                    errors.append('fallen root still moves')
            entry = {'case': key, 'passed': quality['passed'] and not errors, 'errors': errors, 'quality': quality}
            report['checks'].append(entry)
            print('IMPACT', key, 'PASS' if entry['passed'] else 'FAIL', errors, flush=True)
            atomic_json(ROOT / 'output/duel_impact_regression.json', report)
    report['passed'] = all(e['passed'] for e in report['checks'])
    report['elapsed_seconds'] = round(time.monotonic() - started, 2)
    atomic_json(ROOT / 'output/duel_impact_regression.json', report)
    if not report['passed']:
        raise RuntimeError('Impact regression failed; see output/duel_impact_regression.json')


if __name__ == '__main__':
    main()
