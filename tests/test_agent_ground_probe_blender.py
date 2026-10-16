"""Default submit/fresh-probe ground checks on a copy of the real Beta sample.

Run in background Blender. Uses a new temporary jobs directory, never edits
production jobs or the original accepted sample. No persistent report files.
"""
import json
from pathlib import Path
import shutil
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'scripts'))
sys.path.insert(0, str(PROJECT/'tests'))
from test_agent_contact_probe_blender import fresh_probe


def main():
    import bpy
    import duel_plan
    from agent_camera_quality import camera_requirements
    from agent_character import resolve_actor
    from agent_motion_quality import _active_channelbags
    from agent_motion_timeline import motion_commit
    from agent_scene import submit_scene

    source = Path('/tmp/bamo_p4_ground_final_20261016_02')
    job = 'ground_beta'
    with tempfile.TemporaryDirectory(prefix='bamo-ground-probe-') as temporary:
        jobs = Path(temporary)/'jobs'
        folder = jobs/job
        folder.mkdir(parents=True)
        shutil.copy2(source/'scene.blend', folder/'scene.blend')
        shutil.copy2(source/'jobs'/job/'request.json', folder/'request.json')
        duel_plan.JOBS = jobs
        bpy.ops.wm.open_mainfile(filepath=str(folder/'scene.blend'), load_ui=False, use_scripts=False)
        scene = bpy.context.scene
        camera_requirements(job, {'version': 1, 'shots': [{
            'camera': scene.camera.name, 'frame_start': scene.frame_start,
            'frame_end': scene.frame_end, 'actor_ids': ['Person'],
            'framing': 'full_body', 'fixed_transform': True,
            'expected_projection': 'orthographic', 'margin': .02}]})
        analysis = {'actor_count': 1, 'actors': ['Person'],
                    'intent': '原Beta单人倒地保持、手撑地起身及最终双脚站稳'}
        submit_scene(job, analysis)
        accepted = fresh_probe(folder, jobs)
        assert accepted['passed'], accepted['errors']
        assert accepted['tested_frames'] == 104, accepted['tested_frames']

        # Edit actual persisted animation, then explicitly capture it through
        # the normal timeline API before submission. This preserves valid
        # source/animation bookkeeping while the geometry is genuinely wrong.
        root = resolve_actor(job, 'Person')['root']
        curves = [curve for bag in _active_channelbags(root.animation_data)
                  for curve in bag.fcurves if curve.data_path == 'location' and curve.array_index == 2]
        assert curves, 'Fixture must contain real root Z animation'
        for curve in curves:
            for point in curve.keyframe_points:
                point.co.y -= .2
                point.handle_left.y -= .2
                point.handle_right.y -= .2
        root.update_tag()
        scene.frame_set(scene.frame_start)
        motion_commit(job, 'Person', frame_start=scene.frame_start, frame_end=scene.frame_end,
                      capture=True, event_id='actual_root_lowered_negative')
        submit_scene(job, {**analysis, 'revision': 'actual root lowered 20cm after successful ground scene'})
        broken = fresh_probe(folder, jobs)
        assert not broken['passed'], broken
        codes = {error['code'] for error in broken['errors']}
        assert 'ground_motion' in codes, broken['errors']
        assert not ({'robot_execution_mismatch', 'evaluation_changed_after_reload', 'probe_error',
                     'camera_mismatch'} & codes), broken['errors']
        ground_errors = [error for error in broken['errors'] if error['code'] == 'ground_motion']
        failures = [failure for error in ground_errors for actor in error['failures']
                    for failure in actor.get('failures', [])]
        penetration = max((failure.get('penetration_m', 0) for failure in failures), default=0)
        assert any(failure.get('check') == 'actual_skin_floor' and failure.get('penetration_m', 0) > .15
                   for failure in failures), ground_errors
        result = {'passed': True, 'fresh_probe_frames': accepted['tested_frames'],
                  'normal_submit_passed': accepted['passed'],
                  'actual_20cm_root_lowering_detected': True,
                  'maximum_negative_penetration_m': penetration, 'negative_error_codes': sorted(codes)}
    print('GROUND_DEFAULT_PROBE_ACCEPTANCE '+json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
