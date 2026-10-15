"""Retargeted/mirrored full-body punches and independent source root motion."""
import bpy
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from duel_plan import catalog, atomic_json
from duel_motion_sources import MotionSources
from duel_scene import Actor, point


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sources = MotionSources(catalog()['motions'], needs_fall=True)
    actor = Actor('SourceTest',sources,0,0,.6)
    checks = []
    for name in ('jab','cross'):
        for mirrored in (False,True):
            definition = sources.definitions[name]
            side = definition['side']
            if mirrored:
                side = 'right' if side=='left' else 'left'
            actor.set_pose(sources.sample(name,definition['source_peak'],mirrored))
            bpy.context.view_layer.update()
            a,b,c = [point(actor.arm,side.title()+part) for part in ('Arm','ForeArm','Hand')]
            angle = math.degrees((a-b).angle(c-b))
            checks.append({'motion':name,'side':side,'mirrored':mirrored,'elbow_deg':angle,'passed':angle>170})
    root = sources.root_at('fall_back',sources.ends['fall_back'])-sources.root_at('fall_back',1)
    checks.append({'motion':'fall_back','root_displacement_m':list(root),'passed':root.xy.length>.5})
    result = {'passed':all(c['passed'] for c in checks),'checks':checks,
              'scope':'Source-pose transfer, mirroring and root extraction; no contact-controller claims.'}
    atomic_json(ROOT/'reports/duel_motion_source_regression.json',result)
    for check in checks:
        print('MOTION_SOURCE',check,flush=True)
    if not result['passed']:
        raise RuntimeError('Motion source transfer check failed')


if __name__=='__main__':
    main()
