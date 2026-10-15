"""Real fixed-skin FBX/GLB sampling, root equivalence and failure cleanup.

Run headlessly with --report /absolute/report.json.  This never opens/saves GUI
state, source asset caches, or historical output jobs.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))


def main():
    import bpy
    from mathutils import Vector
    import duel_plan
    import agent_scene
    from agent_character import actor_ensure, resolve_actor, binding_facts
    from agent_motion import prepare_motion, sample_motion

    parser = argparse.ArgumentParser()
    parser.add_argument('--report', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    temporary = tempfile.TemporaryDirectory(prefix='bamo_motion_test_')
    duel_plan.JOBS = Path(temporary.name)
    agent_scene.JOBS = duel_plan.JOBS
    job_id = 'motion_source_test'
    (duel_plan.JOBS / job_id).mkdir()
    (duel_plan.JOBS / job_id / 'request.json').write_text(json.dumps({'prompt': '固定动作库人物采样验证'}), encoding='utf-8')
    actor_ensure(job_id, 'A', position=(1, 2, 0), yaw=.6)
    actor_ensure(job_id, 'B', position=(-2, 0, 0), yaw=-.4)
    a = resolve_actor(job_id, 'A')
    b = resolve_actor(job_id, 'B')
    signature = binding_facts(a['meshes'], a['armature'])['signature']
    scene.render.fps, scene.render.fps_base = 24, 1.001
    scene.frame_set(11, subframe=.25)
    before_objects = set(bpy.data.objects.keys())
    before_actions = set(bpy.data.actions.keys())
    before_b = {pb.name: tuple(tuple(row) for row in pb.matrix_basis) for pb in b['armature'].pose.bones}
    before_fps = scene.render.fps, scene.render.fps_base
    checks = []

    def apply(sample):
        for name, value in sample['pose'].items():
            pb = a['armature'].pose.bones[name]
            pb.rotation_mode = 'QUATERNION'
            pb.location = value['location']
            pb.rotation_quaternion = value['rotation_quaternion']
            pb.scale = value['scale']
        bpy.context.view_layer.update()

    def points():
        return {name: a['armature'].matrix_world @ a['armature'].pose.bones[name].head
                for name in ('mixamorig:Hips', 'mixamorig:LeftFoot', 'mixamorig:RightHand', 'mixamorig:Head')}

    for motion_id in ('mixamo.basic.walk', 'mixamo.basic.run', 'mixamo.combat_melee.hook',
                      'quaternius.punch_jab', 'quaternius.punch_cross', 'quaternius.death01'):
        info = prepare_motion(a['armature'], motion_id)
        self_clean = (before_objects == set(bpy.data.objects.keys()) and before_actions == set(bpy.data.actions.keys()))
        roots, errors, pose_changes = [], [], []
        initial = sample_motion(a['armature'], motion_id, 0.)
        for source_time in (0., info['duration_s'] * .25, info['duration_s'] * .5, info['duration_s']):
            preserved = sample_motion(a['armature'], motion_id, source_time, root_mode='preserve')
            extracted = sample_motion(a['armature'], motion_id, source_time)
            base = Vector((1, 2, 0))
            a['root'].location = base
            apply(preserved)
            reference = points()
            # source root is actor-local metres; rotate once by actor yaw.
            a['root'].location = base + a['root'].rotation_euler.to_matrix() @ Vector(extracted['root'])
            apply(extracted)
            current = points()
            errors.append(max((current[name] - reference[name]).length for name in current))
            roots.append(extracted['root'])
            pose_changes.append(max(abs(value - initial['pose'][bone]['rotation_quaternion'][i])
                                    for bone in extracted['pose'] for i, value in enumerate(extracted['pose'][bone]['rotation_quaternion'])))
        passed = (self_clean and max(errors) < 2e-5 and max(pose_changes) > .02
                  and set(bpy.data.objects.keys()) == before_objects
                  and set(bpy.data.actions.keys()) == before_actions
                  and (scene.render.fps, scene.render.fps_base) == before_fps
                  and scene.frame_current == 11 and abs(scene.frame_subframe - .25) < 1e-6
                  and binding_facts(a['meshes'], a['armature'])['signature'] == signature)
        checks.append({'motion_id': motion_id, 'passed': passed, 'max_root_extraction_equivalence_error_m': max(errors),
                       'max_quaternion_component_change': max(pose_changes), 'source_info': info,
                       'root_last': roots[-1], 'temporary_imports_cleaned': self_clean})

    # Named punch transfer must preserve actual nearly straight striking arm.
    for motion_id, side, peak in (('quaternius.punch_jab', 'Left', 8), ('quaternius.punch_cross', 'Right', 14)):
        info = prepare_motion(a['armature'], motion_id)
        apply(sample_motion(a['armature'], motion_id, (peak - 1) / info['key_timebase_fps']))
        arm = a['armature']
        shoulder, elbow, hand = [arm.matrix_world @ arm.pose.bones['mixamorig:' + side + part].head
                                 for part in ('Arm', 'ForeArm', 'Hand')]
        angle = math.degrees((shoulder - elbow).angle(hand - elbow))
        checks.append({'motion_id': motion_id, 'check': 'real_striking_elbow', 'elbow_deg': angle, 'passed': angle > 170})
    untouched_b = all(tuple(tuple(row) for row in pb.matrix_basis) == before_b[pb.name] for pb in b['armature'].pose.bones)
    checks.append({'check': 'independent_actor_B_pose', 'passed': untouched_b})
    a['armature'].scale *= 2
    expected_error = False
    try:
        prepare_motion(a['armature'], 'mixamo.basic.walk')
    except ValueError as exc:
        expected_error = 'incompatible' in str(exc)
    finally:
        a['armature'].scale *= .5
    checks.append({'check': 'incompatible_scale_rejected_and_temp_cleaned',
                   'passed': expected_error and before_objects == set(bpy.data.objects.keys()) and before_actions == set(bpy.data.actions.keys())})
    result = {'passed': all(item['passed'] for item in checks), 'checks': checks,
              'scope': 'Real fixed Beta skin and source-pose transfer; no contact, full-scene naturalness or combat-quality certification'}
    output = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('AGENT_MOTION ' + str(output), flush=True)
    print(json.dumps({'passed': result['passed'], 'checks': [{'check': x.get('check',x.get('motion_id')), 'passed':x['passed']} for x in checks]}), flush=True)
    temporary.cleanup()
    if not result['passed']:
        raise RuntimeError('Fixed-skin motion regression failed')


if __name__ == '__main__':
    main()
