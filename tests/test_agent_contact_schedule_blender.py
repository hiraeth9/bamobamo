"""Real Beta alternating contacts and FK recovery; temporary headless scene."""
import math
from pathlib import Path
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
sys.path.insert(0, str(PROJECT / 'tests'))
import test_agent_contacts_blender as fixture


def main():
    import bpy
    from mathutils import Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_contacts import check_contacts
    from agent_motion_timeline import register_event, motion_commit
    if not bpy.app.background:
        raise RuntimeError('Run this test in background Blender')
    with tempfile.TemporaryDirectory(prefix='bamo-contact-schedule-') as temporary:
        job, folder = fixture.new_scene(Path(temporary) / 'jobs', 'alternating_contacts')
        actor_ensure(job, 'A')
        actor_ensure(job, 'B', position=(0, -.35, 0), yaw=math.pi)
        actor_ensure(job, 'C', position=(2, 2, 0))
        actors = {aid: resolve_actor(job, aid) for aid in ('A', 'B', 'C')}
        initial, effectors = {}, {}
        for aid in ('A', 'B'):
            effectors[aid], initial[aid], _ = fixture.pick_hand_vertex(actors[aid])
        shifts = {'A': Vector((.02, 0, 0)), 'B': Vector((-.015, 0, 0))}
        for aid, actor in actors.items():
            event = fixture.pose_event(actor, 'base_' + aid)
            if aid in shifts:
                origin = actor['root'].location.copy()
                event['root_strategy'] = 'trajectory'
                event['root_path'] = [{'frame': 1, 'root': list(origin)},
                                      {'frame': 24, 'root': list(origin + shifts[aid])}]
            register_event(job, aid, event['event_id'], event)
        motion_commit(job, 'C')
        spectator_action = actors['C']['armature'].animation_data.action
        for aid, other, start, contact_start, contact_end, end in (
                ('A', 'B', 1, 4, 8, 12), ('B', 'A', 13, 16, 20, 23)):
            register_event(job, aid, 'touch_' + aid, {
                'source': 'procedural', 'start_frame': start, 'end_frame': end,
                'mask': ['right_arm'], 'targets': {'RightHand': {
                    'binding': {'actor_id': other, 'bone': 'Head', 'local_point': [0, 3, 0]},
                    'effector': effectors[aid],
                    'contact': {'start_frame': contact_start, 'end_frame': contact_end, 'tolerance_m': .02},
                    'keyframes': [{'frame': start, 'influence': 0},
                                  {'frame': contact_start, 'influence': 1},
                                  {'frame': contact_end, 'influence': 1},
                                  {'frame': end, 'influence': 0}]}}})
        committed = motion_commit(job, ['A', 'B'], 5, 20)
        assert committed['dynamic_commit_order'], committed['commit_order']
        assert any(w['dependencies'] == {'A': ['B']} for w in committed['dependency_windows'])
        assert any(w['dependencies'] == {'B': ['A']} for w in committed['dependency_windows'])
        assert actors['C']['armature'].animation_data.action is spectator_action
        checked = check_contacts(job)
        assert checked['passed'], checked['failures']
        assert len(checked['contacts']) == 2
        bpy.context.scene.frame_set(24)
        for aid in ('A', 'B'):
            actual = fixture.vertex_world(effectors[aid]['object'], effectors[aid]['vertex_index'])
            assert (actual - initial[aid] - shifts[aid]).length < .0001, 'IK leaked into FK recovery: ' + aid
            assert committed['actors'][aid]['actual_bake_range'] == [1, 24]
        actions = {(aid, key): actors[aid][key].animation_data.action
                   for aid in ('A', 'B') for key in ('armature', 'root')}
        register_event(job, 'B', 'simultaneous_cycle', {
            'source': 'procedural', 'start_frame': 4, 'end_frame': 8, 'mask': ['right_arm'],
            'targets': {'RightHand': {'binding': {'actor_id': 'A', 'bone': 'Head'}, 'contact': False}}})
        try:
            motion_commit(job, ['A', 'B'])
        except ValueError as error:
            assert 'Frame 4' in str(error) and 'cyclic' in str(error).lower(), str(error)
        else:
            raise AssertionError('A genuine same-frame cycle was accepted')
        assert all(actors[aid][key].animation_data.action is action
                   for (aid, key), action in actions.items()), 'Cycle rejection changed existing animation'
        maximum = max(contact['max_distance_m'] for contact in checked['contacts'])
        print(f'CONTACT_SCHEDULE_PASS frames=24 contacts=2 max_distance_m={maximum:.6f}', flush=True)


if __name__ == '__main__':
    main()
