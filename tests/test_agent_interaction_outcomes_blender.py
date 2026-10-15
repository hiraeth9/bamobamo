"""Small real-Beta block/miss checks; prints results and renders two frames.

./blender -b -t 2 --python-exit-code 1 --python tests/test_agent_interaction_outcomes_blender.py
All temporary jobs and images are written outside the project. No user scene
is opened; this checks pass-point geometry, not whole-body swept collisions.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))


def skin_target(actor, groups, desired):
    import bpy
    candidates = []
    for obj in actor['meshes']:
        indices = {group.index for group in obj.vertex_groups if group.name.split(':')[-1] in groups}
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            for vertex in obj.data.vertices:
                if sum(group.weight for group in vertex.groups if group.group in indices) < .4:
                    continue
                position = evaluated.matrix_world @ mesh.vertices[vertex.index].co
                candidates.append(((position-desired).length_squared, obj.name, vertex.index))
        finally:
            evaluated.to_mesh_clear()
    _, name, index = min(candidates)
    return {'object': name, 'vertex_index': index}


def register(job, plan):
    from agent_interactions import register_interaction
    return register_interaction(job, plan)


def run_case(outcome, output):
    import bpy
    from mathutils import Quaternion, Vector
    import duel_plan
    from agent_character import actor_ensure, resolve_actor
    from agent_contacts import resolve_effector
    from agent_interactions import guard_events, hand_strike_events, recoil_events, verify_interaction
    from agent_motion_timeline import motion_commit
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end, scene.render.fps = 1, 18, 30
    scene.world = bpy.data.worlds.new('OutcomeWorld')
    scene.world.color = (.3, .3, .3)
    duel_plan.JOBS = output / 'jobs'
    job = 'left_hand_' + outcome
    folder = duel_plan.JOBS / job
    folder.mkdir(parents=True)
    (folder / 'request.json').write_text(json.dumps({'prompt': '斜向左手' + outcome,
                     'character_policy': 'library_beta'}, ensure_ascii=False), encoding='utf-8')
    attacker, defender = 'southpaw.alpha', 'guard-beta.2'
    yaw = .57
    rotation = Quaternion((0, 0, 1), yaw)
    forward, lateral = rotation @ Vector((0, -1, 0)), rotation @ Vector((1, 0, 0))
    actor_ensure(job, attacker, position=(0, 0, 0), yaw=yaw, color=(.6, .7, .8, 1))
    actor_ensure(job, defender, position=list(forward*.66), yaw=yaw+math.pi, color=(.8, .7, .6, 1))
    actors = {aid: resolve_actor(job, aid) for aid in (attacker, defender)}
    guards = []
    for aid in actors:
        guard = guard_events(job, aid, 'guard_' + aid, 1, 1)
        assert len(guard['events'][0]['keyframes']) == 1
        assert all(len(target['keyframes']) == 1 for target in guard['events'][0]['targets'].values())
        register(job, guard)
        motion_commit(job, aid, 1, 1)
        guards.append(guard)
    for guard in guards:
        guard['events'][0]['end_frame'] = scene.frame_end
        register(job, guard)
    scene.frame_set(1); bpy.context.view_layer.update()
    arm = actors[attacker]['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    hand = arm.matrix_world @ arm.pose.bones['mixamorig:LeftHand'].head
    groups = ('LeftForeArm', 'RightForeArm') if outcome == 'block' else ('Head',)
    binding = skin_target(actors[defender], groups, hand)
    before_root = actors[defender]['root'].matrix_world.translation.copy()
    other_arm = actors[defender]['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    before_head = other_arm.matrix_world @ other_arm.pose.bones['mixamorig:Head'].head
    plan = hand_strike_events(job, attacker, 'left_' + outcome, binding,
             start_frame=3, impact_frame=8, end_frame=14, hand='LeftHand',
             intensity=.7, outcome=outcome,
             aim_offset_world=list(lateral*.20) if outcome == 'miss' else None)
    assert plan['ok'], plan
    reaction = recoil_events(job, defender, 'no_recoil_' + outcome, impact_frame=8,
                             end_frame=14, direction_world=list(forward), outcome=outcome)
    assert reaction['events'] == []
    # A normal edit from an earlier hit plan to a miss/block must clear the
    # previously registered recoil subevent as well as its old expectation.
    previous_reaction = recoil_events(job, defender, 'no_recoil_' + outcome, impact_frame=8,
                                      end_frame=14, direction_world=list(forward), contact_id='old-hit')
    register(job, previous_reaction)
    register(job, reaction)
    register(job, plan)
    motion_commit(job, [attacker, defender], 1, 18)
    result = verify_interaction(job, detail=True)
    assert result['passed'] and not result['hit_confirmed'], result
    assert result[outcome + '_confirmed'], result
    scene.frame_set(8); bpy.context.view_layer.update()
    measured = resolve_effector(job, actors[attacker], 'LeftHand', plan['expectations']['effector'])
    other_arm = actors[defender]['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    after_head = other_arm.matrix_world @ other_arm.pose.bones['mixamorig:Head'].head
    assert (actors[defender]['root'].matrix_world.translation-before_root).length < 1e-6
    assert (after_head-before_head).length < 1e-5, 'block/miss unexpectedly moved defender head'
    if outcome == 'block':
        contact = result['contact_report']['contacts'][0]
        assert contact['kind'] == 'block' and contact['max_distance_m'] <= .02
        metrics = {'contact_error_m': contact['max_distance_m'], 'target_scope': contact['target_scope']}
    else:
        assert result['contact_report']['contacts'] == []
        miss = result['miss_reports'][0]
        assert miss['min_clearance_m'] >= .08 and miss['max_aim_error_m'] <= .02
        metrics = {'clearance_m': miss['min_clearance_m'], 'aim_error_m': miss['max_aim_error_m']}
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_cavity = True
    scene.render.resolution_x, scene.render.resolution_y = 720, 540
    scene.render.resolution_percentage = 100
    camera_data = bpy.data.cameras.new('OutcomeFullBody')
    camera = bpy.data.objects.new('OutcomeFullBody', camera_data)
    scene.collection.objects.link(camera)
    center = forward*.33
    camera.location = center-forward-lateral*5+Vector((0, 0, 2.4))
    camera.rotation_euler = (center+Vector((0, 0, .95))-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera_data.type, camera_data.ortho_scale = 'ORTHO', 3.2
    scene.camera = camera
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = str(output / (outcome + '.png'))
    bpy.ops.render.render(write_still=True)
    saved_scene = output / (outcome + '.blend')
    bpy.ops.wm.save_as_mainfile(filepath=str(saved_scene), check_existing=False)
    subprocess.run([str(PROJECT / 'blender'), '-b', str(saved_scene), '-t', '2', '--python-exit-code', '1',
                    '--python', str(Path(__file__).resolve()), '--', '--output', str(output),
                    '--verify-saved', job], check=True)
    print('OUTCOME_PASS', json.dumps({'outcome': outcome, 'actor_id': attacker,
          'hand': 'LeftHand', 'yaw': yaw, 'hit_confirmed': result['hit_confirmed'],
          'recoil_events': 0, 'actual_effector': measured['scope'], **metrics}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--verify-saved')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    output = Path(args.output).resolve() if args.output else Path(tempfile.mkdtemp(prefix='bamo_outcomes_'))
    output.mkdir(parents=True, exist_ok=True)
    if args.verify_saved:
        import bpy
        import duel_plan
        from agent_interactions import guard_events, verify_interaction
        duel_plan.JOBS = output / 'jobs'
        outcome = args.verify_saved.removeprefix('left_hand_')
        # Long guards compile sparse keys rather than repeating 450 poses.
        end = bpy.context.scene.frame_end
        bpy.context.scene.frame_end = 450
        for rise, count in ((0, 1), (12, 2)):
            guard = guard_events(args.verify_saved, 'southpaw.alpha', 'unregistered_long_guard', 1, 450, raise_frames=rise)
            assert len(guard['events'][0]['keyframes']) == count
            assert all(len(target['keyframes']) == count for target in guard['events'][0]['targets'].values())
        bpy.context.scene.frame_end = end
        result = verify_interaction(args.verify_saved)
        assert result['passed'] and result[outcome+'_confirmed'] and not result['hit_confirmed'], result
        print('OUTCOME_RELOAD_PASS', outcome, flush=True)
        return
    print('OUTCOME_OUTPUT', output, flush=True)
    for outcome in ('block', 'miss'):
        run_case(outcome, output)
    print('OUTCOMES_PASS', output, flush=True)


if __name__ == '__main__':
    main()
