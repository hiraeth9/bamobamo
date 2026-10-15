"""Contact, block and dodge for the newly registered full-body cross punch."""
import bpy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from duel_plan import validate_plan,atomic_json
from duel_scene import build


def main():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bamo-cross-checks-') as name:
        folder=Path(name)
        for actor,side,region in (('A','right','head'),('B','left','body')):
            for outcome in ('hit','block','dodge'):
                job='cross_'+actor.lower()+'_'+outcome
                plan=validate_plan({'version':1,'duration_seconds':3,'events':[
                    {'kind':'strike','actor':actor,'motion':'cross','side':side,'region':region,
                     'outcome':outcome,'start':.2,'peak':.75,'end':1.5,'intensity':.6}]},job)
                with contextlib.redirect_stdout(io.StringIO()):
                    quality=build(plan,folder)
                check={'actor':actor,'side':side,'region':region,'outcome':outcome,
                       'passed':quality['passed'],'quality':quality}
                checks.append(check)
                print('CROSS',actor,side,region,outcome,'PASS' if check['passed'] else 'FAIL',flush=True)
                atomic_json(ROOT/'reports/duel_cross_regression.json',{'checks':checks})
    result={'passed':all(c['passed'] for c in checks),'checks':checks,
            'scope':'Cross contact/block/dodge, swapped actors, mirrored hand and fractional contact time.'}
    atomic_json(ROOT/'reports/duel_cross_regression.json',result)
    if not result['passed']:
        raise RuntimeError('Cross regression failed')


if __name__=='__main__':
    main()
