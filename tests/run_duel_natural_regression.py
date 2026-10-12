"""20 natural-language requests x 3 plans; real Hermes and Blender, no canned parser.

Plans are generated in groups of three in one Hermes session per request. This
checks prompt coverage and executable variants, not 60 independent model sessions
or 60 fully encoded movies. Representative full movies are tested separately.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from duel_plan import PROJECT, atomic_json, job_directory, planning_prompt, validate_plan
from duel_worker import child_environment
from duel_cases import PROMPTS


def check_case(index, prompt, repeats, run_id):
    ids = [f'nlp_{run_id}_{index:02d}_{repeat}' for repeat in range(repeats)]
    folder = job_directory(ids[0])
    folder.mkdir(parents=True, exist_ok=True)
    instruction = planning_prompt(prompt, ids[0])
    instruction += f'\n这是内部回归检查。同一个自然语言需求生成 {repeats} 个计划，分别提交任务ID {json.dumps(ids)}。每个都用 print(bpy.wm_duel_plan(对应ID, plan)) 单独调用。三个计划都必须覆盖用户明确要求，允许改变准备恢复节奏及seed，不能改变关键顺序、角色、部位和结果。全部返回ok=true再结束。无需渲染。'
    with (folder / 'hermes_regression.log').open('w') as log:
        result = subprocess.run(['hermes', '-z', instruction], cwd=PROJECT, env=child_environment(), stdout=log, stderr=subprocess.STDOUT, timeout=600)
    records = []
    for job_id in ids:
        path = job_directory(job_id)
        record = {'case': index, 'job_id': job_id, 'prompt': prompt, 'passed': False}
        if result.returncode or not (path / 'plan.json').exists():
            record['error'] = 'Hermes未提交计划，或规划进程失败'
            records.append(record)
            continue
        try:
            plan = validate_plan(json.loads((path / 'plan.json').read_text()), job_id)
            record['events'] = [{k: e.get(k) for k in ('kind', 'actor', 'motion', 'outcome', 'region', 'side', 'dodge', 'direction')} for e in plan['events']]
            record['frame_count'] = plan['frame_count']
            with (path / 'regression_build.log').open('w') as log:
                command = [str(PROJECT / 'blender'), '-b', '-t', '4', '--python-exit-code', '1', '--python', str(PROJECT / 'scripts' / 'duel_scene.py'), '--', '--plan', str(path / 'plan.json')]
                built = subprocess.run(command, cwd=PROJECT, env=child_environment(), stdout=log, stderr=subprocess.STDOUT, timeout=300)
            record['quality'] = json.loads((path / 'quality.json').read_text()) if (path / 'quality.json').exists() else {}
            record['passed'] = built.returncode == 0 and record['quality'].get('passed', False)
        except Exception as error:
            record['error'] = str(error)
        records.append(record)
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--workers', type=int, default=4)
    options = parser.parse_args()
    run_id = str(int(time.time()))[-6:]
    report_path = PROJECT / 'output' / 'duel_natural_regression.json'
    report = {'run_id': run_id, 'started_at': time.time(), 'scope': '20 prompts x 3 executable plans, three plans per Hermes session; per-frame geometry and scene saving. Full movie encoding is covered by separate representative jobs.', 'records': []}
    atomic_json(report_path, report)
    with ThreadPoolExecutor(max_workers=options.workers) as pool:
        futures = [pool.submit(check_case, index, prompt, options.repeats, run_id) for index, prompt in enumerate(PROMPTS[:options.limit])]
        for future in as_completed(futures):
            try:
                records = future.result()
            except Exception as error:
                records = [{'passed': False, 'error': str(error)}]
            report['records'].extend(records)
            report['passed_count'] = sum(r['passed'] for r in report['records'])
            atomic_json(report_path, report)
            print('NATURAL_REGRESSION', len(report['records']), '/', options.limit * options.repeats, 'passed', report['passed_count'], flush=True)
    signatures = {json.dumps(r.get('events'), sort_keys=True) for r in report['records'] if r.get('passed')}
    report['unique_event_sequences'] = len(signatures)
    report['elapsed_seconds'] = round(time.time() - report['started_at'], 2)
    report['passed'] = report['passed_count'] == options.limit * options.repeats and len(signatures) >= min(10, options.limit)
    atomic_json(report_path, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}, ensure_ascii=False))
    sys.exit(0 if report['passed'] else 1)
