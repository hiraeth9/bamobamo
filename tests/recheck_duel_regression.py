"""Re-evaluate generated plans against the final controller after bug fixes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from duel_plan import PROJECT, atomic_json, job_directory
from duel_worker import child_environment


def intent_errors(case, plan):
    attacks = [e for e in plan['events'] if e['kind'] == 'strike']
    errors = []
    required = {
        0: [('A', 'jab', 'hit')], 1: [('A', None, 'dodge'), ('B', 'kick', 'block')],
        2: [('B', 'hook', 'dodge'), ('A', None, None)],
        5: [('A', 'uppercut', 'dodge'), ('B', None, None)],
        8: [('B', 'jab', 'block'), ('A', 'kick', 'hit')],
        9: [('A', 'jab', 'block'), ('B', 'hook', 'dodge')],
        10: [('A', 'jab', 'hit')], 11: [('A', 'jab', 'block')], 12: [('A', 'jab', 'dodge')],
        13: [('A', 'uppercut', 'dodge'), ('B', 'jab', None)],
        14: [('B', 'kick', 'hit'), ('A', 'hook', 'block')],
        15: [('A', 'kick', 'dodge'), ('B', 'uppercut', 'block')],
        18: [('A', 'jab', 'block'), ('A', 'hook', 'dodge'), ('A', 'kick', 'hit')],
        19: [('B', 'jab', None), ('A', 'uppercut', None)],
    }.get(case, [])
    offset = 0
    for actor, motion, outcome in required:
        matched = next((i for i in range(offset, len(attacks)) if attacks[i]['actor'] == actor and (motion is None or attacks[i]['motion'] == motion) and (outcome is None or attacks[i]['outcome'] == outcome)), None)
        if matched is None:
            errors.append(f'缺少或顺序错误：{actor}/{motion}/{outcome}')
            break
        offset = matched + 1
    if case in (0, 10, 11, 12) and len(attacks) != 1:
        errors.append('只要求一记直拳，不能增加其他攻击')
    if case in (3, 4, 7) and {e['actor'] for e in attacks} != {'A', 'B'}:
        errors.append('需要双方参与攻防')
    if case in (4, 7) and (not any(e['motion'] == 'kick' for e in attacks) or not any(e['motion'] in ('jab', 'hook') for e in attacks)):
        errors.append('需要拳与踢两类运动')
    if case in (6, 16) and not any(e['kind'] == 'move' and e['direction'] == 'apart' for e in plan['events']):
        errors.append('缺少收尾拉开距离')
    if case == 16:
        for actor in ('A', 'B'):
            if sum(e['actor'] == actor and e['motion'] == 'jab' and e['outcome'] == 'block' for e in attacks) != 3:
                errors.append('双方各三拳被格挡的数量不符')
    if case == 9:
        if not (attacks[0]['side'] == 'left' and attacks[1]['side'] == 'right'):
            errors.append('左右侧没有覆盖要求')
    if case == 14 and attacks[0]['side'] != 'right':
        errors.append('需要 B 右腿')
    if case == 15 and attacks[0]['side'] != 'left':
        errors.append('需要 A 左腿')
    if case == 5 and not any(e['actor']=='A' and e['outcome']=='dodge' and e['dodge']=='duck' for e in attacks):
        errors.append('缺少低头下潜闪避')
    return errors


def recheck(record, code_hash):
    folder = job_directory(record['job_id'])
    plan = json.loads((folder / 'plan.json').read_text())
    record['intent_errors'] = intent_errors(record['case'], plan)
    with (folder / 'final_regression_build.log').open('w') as log:
        built = subprocess.run([str(PROJECT / 'blender'), '-b', '-t', '4', '--python-exit-code', '1', '--python', str(PROJECT / 'scripts' / 'duel_scene.py'), '--', '--plan', str(folder / 'plan.json')], cwd=PROJECT, env=child_environment(), stdout=log, stderr=subprocess.STDOUT, timeout=300)
    record['quality'] = json.loads((folder / 'quality.json').read_text())
    record['passed'] = built.returncode == 0 and record['quality']['passed'] and not record['intent_errors'] and record['quality']['scene_code_sha256'] == code_hash
    return record


if __name__ == '__main__':
    path = PROJECT / 'output' / 'duel_natural_regression.json'
    report = json.loads(path.read_text())
    atomic_json(path.with_name('duel_natural_regression_initial.json'), report)
    code_hash = hashlib.sha256((PROJECT / 'scripts' / 'duel_scene.py').read_bytes()).hexdigest()
    records = report['records']
    report['records'] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(recheck, dict(record), code_hash) for record in records]
        for future in as_completed(futures):
            result = future.result()
            report['records'].append(result)
            report['passed_count'] = sum(r['passed'] for r in report['records'])
            atomic_json(path, report)
            print('RECHECK', len(report['records']), '/', len(records), 'passed', report['passed_count'], flush=True)
    signatures = {json.dumps(r['events'], sort_keys=True) for r in report['records'] if r['passed']}
    report['unique_event_sequences'] = len(signatures)
    report['passed'] = report['passed_count'] == 60 and len(signatures) >= 10
    report['final_scene_code_sha256'] = code_hash
    report['recheck_elapsed_seconds'] = round(time.time() - started, 2)
    atomic_json(path, report)
    sys.exit(0 if report['passed'] else 1)
