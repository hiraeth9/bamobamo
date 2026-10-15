"""Real Beta skin/contact acceptance in isolated headless Blender processes.

Run with --report /absolute/new/report.json.  The test owns new job directories
under that report, never the shared GUI or output/jobs.  Expected distances are
measured directly from evaluated Blender meshes/bones, independently of the
contact resolver.  No rendered appearance, fight dynamics or naturalness claim.
"""
import argparse
from array import array
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
FRAMES = 24


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def configure_jobs(folder):
    import duel_plan
    duel_plan.JOBS = Path(folder).resolve()
    duel_plan.JOBS.mkdir(parents=True, exist_ok=True)


def new_scene(jobs, label, frame_end=FRAMES):
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)
    configure_jobs(jobs)
    job = 'contacts_' + label + '_' + uuid.uuid4().hex[:10]
    folder = Path(jobs) / job
    folder.mkdir()
    write_json(folder / 'request.json', {'prompt': '真实 Beta 蒙皮接触隔离验收：' + label,
               'origin': 'isolated_contact_acceptance', 'character_policy': 'library_beta'})
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, frame_end
    scene.render.fps, scene.render.fps_base = 30, 1
    return job, folder


def vertex_world(object_name, index):
    import bpy
    obj = bpy.data.objects[object_name].evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = obj.to_mesh()
    try:
        return obj.matrix_world @ mesh.vertices[index].co
    finally:
        obj.to_mesh_clear()


def target_world(job, binding):
    """Independent oracle: deliberately do not call agent_contacts helpers."""
    import bpy
    from mathutils import Vector
    from agent_character import resolve_actor
    if 'object' in binding:
        obj = bpy.data.objects[binding['object']].evaluated_get(bpy.context.evaluated_depsgraph_get())
        if 'vertex_index' in binding:
            return vertex_world(binding['object'], binding['vertex_index'])
        return obj.matrix_world @ Vector(binding.get('local_point', (0, 0, 0)))
    arm = resolve_actor(job, binding['actor_id'])['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    name = binding['bone']
    if name not in arm.pose.bones:
        name = next(b.name for b in arm.pose.bones if b.name.split(':')[-1] == name)
    return arm.matrix_world @ arm.pose.bones[name].matrix @ Vector(binding.get('local_point', (0, 0, 0)))


def measure_contact(job, spec):
    import bpy
    scene = bpy.context.scene
    old = scene.frame_current
    rows = []
    try:
        for frame in range(spec['start_frame'], spec['end_frame'] + 1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            point = vertex_world(spec['effector']['object'], spec['effector']['vertex_index'])
            goal = target_world(job, spec['binding'])
            rows.append({'frame': frame, 'distance_m': (point - goal).length,
                         'effector_world_m': list(point), 'target_world_m': list(goal)})
    finally:
        scene.frame_set(old)
    maximum = max(row['distance_m'] for row in rows)
    return {'passed': maximum <= spec['tolerance_m'], 'max_distance_m': maximum,
            'tolerance_m': spec['tolerance_m'], 'frames_checked': len(rows), 'measurements': rows,
            'oracle': 'evaluated skin vertex to independently evaluated mesh vertex or bone-local point'}


def pick_hand_vertex(actor):
    """Use a genuine palm vertex with a visible offset from the wrist origin."""
    import bpy
    arm = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    bone = arm.pose.bones['mixamorig:RightHand']
    wrist = arm.matrix_world @ bone.head
    choices = []
    for obj in actor['meshes']:
        group = obj.vertex_groups.get('mixamorig:RightHand')
        if group is None:
            continue
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            for vertex in obj.data.vertices:
                weight = next((g.weight for g in vertex.groups if g.group == group.index), 0)
                if weight < .95:
                    continue
                point = evaluated.matrix_world @ mesh.vertices[vertex.index].co
                offset = (point - wrist).length
                if .04 <= offset <= .10:
                    choices.append((abs(offset - .06), obj.name, vertex.index, point.copy(), offset, weight))
        finally:
            evaluated.to_mesh_clear()
    if not choices:
        raise AssertionError('Canonical Beta has no expected strongly weighted palm test vertex')
    _, name, index, point, offset, weight = min(choices, key=lambda item: item[:3])
    return {'object': name, 'vertex_index': index}, point, {'wrist_offset_m': offset, 'hand_weight': weight}


def linear_keys(obj):
    action = obj.animation_data.action
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    for key in curve.keyframe_points:
                        key.interpolation = 'LINEAR'


def pose_event(actor, event_id, end_bone='mixamorig:LeftForeArm', frame_end=FRAMES):
    from mathutils import Quaternion
    from agent_motion_controls import read_pose
    first = read_pose(actor['armature'])
    last = copy.deepcopy(first)
    last[end_bone]['rotation_quaternion'] = list(Quaternion(first[end_bone]['rotation_quaternion']) @ Quaternion((1, 0, 0), .25))
    return {'event_id': event_id, 'source': 'procedural', 'start_frame': 1, 'end_frame': frame_end,
            'root_strategy': 'hold', 'intent': '未收录的自然语言动作由既有人物 FK 与绑定 IK 组合完成',
            'keyframes': [{'frame': 1, 'pose': first}, {'frame': frame_end, 'pose': last}]}


def assert_contact_report(report):
    assert report['passed'] is True, report
    assert report.get('status') == 'passed', report
    assert report.get('failure_count', 0) == 0, report
    assert report.get('contacts'), 'A successful report must contain an actual contact measurement'


def actor_frame_hashes(actor, start=1, end=FRAMES):
    """All skin vertices + every pose matrix, not just controller metadata."""
    import bpy
    scene = bpy.context.scene
    old = scene.frame_current
    result = {}
    try:
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            graph = bpy.context.evaluated_depsgraph_get()
            root = actor['root'].evaluated_get(graph)
            arm = actor['armature'].evaluated_get(graph)
            values = [float(v) for row in root.matrix_world for v in row]
            values.extend(float(v) for b in arm.pose.bones for row in b.matrix for v in row)
            for obj in sorted(actor['meshes'], key=lambda item: item.name):
                evaluated = obj.evaluated_get(graph)
                mesh = evaluated.to_mesh()
                try:
                    values.extend(float(v) for vertex in mesh.vertices for v in evaluated.matrix_world @ vertex.co)
                finally:
                    evaluated.to_mesh_clear()
            result[str(frame)] = hashlib.sha256(array('f', values).tobytes()).hexdigest()
    finally:
        scene.frame_set(old)
    return result


def fresh_reload(job, folder, jobs, specs):
    import bpy
    blend = folder / 'contact_scene.blend'
    write_json(folder / 'independent-contact-specs.json', specs)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    output = folder / 'fresh-process-contact-report.json'
    env = dict(os.environ)
    for key in ('DISPLAY', 'XDG_RUNTIME_DIR', 'GHOST_BACKEND', 'WAYLAND_DISPLAY'):
        env.pop(key, None)
    cmd = [str(PROJECT / 'blender'), '-b', '-t', '2', '--python-exit-code', '1',
           '--python', str(Path(__file__).resolve()), '--', '--reload-scene', str(blend),
           '--job-id', job, '--jobs-root', str(jobs), '--report', str(output)]
    with (folder / 'fresh-process.log').open('w', encoding='utf-8') as log:
        run = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    if run.returncode or not output.is_file():
        raise AssertionError('Fresh-process reload failed; see ' + str(folder / 'fresh-process.log'))
    result = json.loads(output.read_text())
    assert result['passed'], result
    return {'passed': True, 'scene': str(blend),
            'report': str(output), 'independent_max_distance_m': max(x['max_distance_m'] for x in result['independent_contacts'])}


def mesh_contact_case(jobs):
    import bpy
    from mathutils import Quaternion, Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_contacts import check_contacts, contact_requirements
    from agent_motion_catalog import search_motions
    from agent_motion_controls import read_pose
    from agent_motion_timeline import register_event, motion_commit, timeline_info
    from agent_motion_quality import motion_check
    job, folder = new_scene(jobs, 'moving_rotating_mesh')
    actor_ensure(job, 'A')
    actor = resolve_actor(job, 'A')
    effector, initial, skin = pick_hand_vertex(actor)
    bpy.ops.mesh.primitive_cube_add(size=.08)
    prop = bpy.context.object
    prop.name = 'ContactFixture_' + job
    prop.rotation_mode = 'QUATERNION'
    arm = actor['armature']
    start_rotation = (arm.matrix_world @ arm.pose.bones['mixamorig:RightHand'].matrix).to_quaternion()
    prop.rotation_quaternion = start_rotation
    prop.location = initial - start_rotation @ prop.data.vertices[0].co
    origin = prop.location.copy()
    for frame, amount in ((1, 0), (12, .5), (FRAMES, 1)):
        prop.location = origin + Vector((.035 * amount, .015 * amount, .025 * amount))
        prop.rotation_quaternion = start_rotation @ Quaternion((0, 0, 1), .65 * amount)
        prop.keyframe_insert('location', frame=frame)
        prop.keyframe_insert('rotation_quaternion', frame=frame)
    linear_keys(prop)
    bpy.context.scene.frame_set(1)
    event = pose_event(actor, 'custom_semantics_without_library')
    event['targets'] = {'RightHand': {'binding': {'object': prop.name, 'vertex_index': 0}, 'effector': effector,
        'contact': {'start_frame': 5, 'end_frame': 20, 'tolerance_m': .02, 'kind': 'grip'},
        'keyframes': [{'frame': 1, 'influence': 0}, {'frame': 5, 'influence': 1},
                      {'frame': 20, 'influence': 1}, {'frame': FRAMES, 'influence': 0}]}}
    query = 'unlisted_contact_semantics_' + uuid.uuid4().hex
    search = search_motions(query)
    assert search['candidate_count'] == 0 and search['procedural_allowed']
    register_event(job, 'A', event['event_id'], event)
    committed = motion_commit(job, 'A', 1, FRAMES)
    spec = {'effector': effector, 'binding': event['targets']['RightHand']['binding'],
            'start_frame': 5, 'end_frame': 20, 'tolerance_m': .02}
    measured = measure_contact(job, spec)
    assert measured['passed'], measured
    automatic = check_contacts(job)
    assert_contact_report(automatic)
    requirements = contact_requirements(job)
    assert requirements['requirements']['contacts'], requirements
    quality = motion_check(job)
    assert quality['passed'], quality.get('failures')
    bpy.context.scene.frame_set(1)
    first = read_pose(actor['armature'], evaluated=True)
    bpy.context.scene.frame_set(FRAMES)
    last = read_pose(actor['armature'], evaluated=True)
    fk_change = Quaternion(first['mixamorig:LeftForeArm']['rotation_quaternion']).rotation_difference(
        Quaternion(last['mixamorig:LeftForeArm']['rotation_quaternion'])).angle
    assert fk_change > .15, 'Procedural FK content was dropped during contact baking'
    reloaded = fresh_reload(job, folder, jobs, [spec])
    # A post-bake edit is deliberately outside the motion timeline: the normal
    # checker must catch stale baked contact, without an explicit contacts list.
    bpy.context.scene.frame_set(12)
    prop.location.x += .25
    prop.keyframe_insert('location', frame=12)
    bpy.context.view_layer.update()
    broken = check_contacts(job)
    assert broken['passed'] is False and broken.get('failure_count', len(broken.get('failures', []))) > 0, broken
    broken_quality = motion_check(job)
    assert broken_quality['passed'] is False, 'Default motion_check silently ignored its persisted contact obligation'
    return {'passed': True, 'job_id': job, 'folder': str(folder), 'skin_effector': {**effector, **skin},
            'independent_contact': measured, 'automatic_contact': automatic, 'saved_reload': reloaded,
            'unmatched_library': {'query': query, 'candidate_count': search['candidate_count'],
                                  'event_sources': [e['source'] for e in timeline_info(job, 'A')['events']],
                                  'non_contact_FK_change_deg': math.degrees(fk_change)},
            'post_bake_target_corruption_detected': {'check_contacts': broken, 'motion_check_failures': broken_quality['failures']},
            'commit': {'actor_id': committed.get('actor_id'), 'frame_count': committed.get('frame_count')}}


def paired_contact_case(jobs):
    import bpy
    from mathutils import Quaternion, Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_contacts import check_contacts
    from agent_motion_timeline import register_event, motion_commit
    from agent_motion_quality import animation_fingerprint
    job, folder = new_scene(jobs, 'paired_strike_and_release')
    for aid, position in (('A', (0, 0, 0)), ('B', (0, -.4, 0)), ('C', (3, 2, 0))):
        actor_ensure(job, aid, position=position)
    actors = {aid: resolve_actor(job, aid) for aid in ('A', 'B', 'C')}
    a, b, c = (actors[aid] for aid in ('A', 'B', 'C'))
    shoulder = a['armature'].matrix_world @ a['armature'].pose.bones['mixamorig:RightArm'].head
    b_head = b['armature'].matrix_world @ b['armature'].pose.bones['mixamorig:Head'].head
    b['root'].location.x += shoulder.x - b_head.x
    b['root'].location.y += shoulder.y - .25 - b_head.y
    bpy.context.view_layer.update()
    effector, _, skin = pick_hand_vertex(a)
    spectator_event = pose_event(c, 'independent_third_actor')
    spectator_event['root_strategy'] = 'trajectory'
    spectator_event['root_path'] = [{'frame': 1, 'root': [3, 2, 0]}, {'frame': FRAMES, 'root': [3.05, 2, 0]}]
    register_event(job, 'C', spectator_event['event_id'], spectator_event)
    motion_commit(job, 'C', 1, FRAMES)
    c_before = actor_frame_hashes(c)
    c_channels = [animation_fingerprint(c[k]) for k in ('armature', 'root')]
    a_event = pose_event(a, 'A_freeform_strike', 'mixamorig:RightForeArm')
    a_event['targets'] = {'RightHand': {'binding': {'actor_id': 'B', 'bone': 'Head', 'local_point': [0, 3, 0]},
        'effector': effector, 'contact': {'start_frame': 9, 'end_frame': 12, 'tolerance_m': .02, 'kind': 'strike'},
        'keyframes': [{'frame': 1, 'influence': 0}, {'frame': 8, 'influence': 1},
                      {'frame': 12, 'influence': 1}, {'frame': 16, 'influence': 0}, {'frame': FRAMES, 'influence': 0}]}}
    b_origin = b['root'].location.copy()
    b_event = pose_event(b, 'B_reaction', 'mixamorig:Head')
    b_event['root_strategy'] = 'trajectory'
    b_event['root_path'] = [{'frame': 1, 'root': list(b_origin)},
                            {'frame': 12, 'root': list(b_origin + Vector((.015, -.015, 0)))},
                            {'frame': 16, 'root': list(b_origin + Vector((.02, -.16, 0)))},
                            {'frame': FRAMES, 'root': list(b_origin + Vector((.04, -.24, 0)))}]
    register_event(job, 'A', a_event['event_id'], a_event)
    register_event(job, 'B', b_event['event_id'], b_event)
    first_commit = motion_commit(job, ['A', 'B'], 1, FRAMES)
    assert first_commit.get('commit_order') == ['B', 'A'], first_commit
    spec = {'effector': effector, 'binding': a_event['targets']['RightHand']['binding'],
            'start_frame': 9, 'end_frame': 12, 'tolerance_m': .02}
    first_contact = measure_contact(job, spec)
    assert first_contact['passed'], first_contact
    released_before = {}
    for frame in range(17, FRAMES + 1):
        bpy.context.scene.frame_set(frame)
        released_before[frame] = vertex_world(effector['object'], effector['vertex_index'])
    old_contact_targets = [Vector(row['target_world_m']) for row in first_contact['measurements']]
    # Changing the registered target event is valid; the group must regenerate
    # B first and solve A from B's new same-frame pose, not its previous bake.
    edited = copy.deepcopy(b_event)
    for key in edited['root_path']:
        key['root'][0] += .035
        if key['frame'] >= 16:
            key['root'][1] -= .12
    register_event(job, 'B', edited['event_id'], edited)
    second_commit = motion_commit(job, ['A', 'B'], 1, FRAMES)
    assert second_commit.get('commit_order') == ['B', 'A'], second_commit
    second_contact = measure_contact(job, spec)
    assert second_contact['passed'], second_contact
    target_change = min((Vector(row['target_world_m']) - old).length
                        for row, old in zip(second_contact['measurements'], old_contact_targets))
    assert target_change > .025, 'The target reaction edit did not reach the evaluated target pose'
    release_error = 0
    released_separation = []
    for frame, previous in released_before.items():
        bpy.context.scene.frame_set(frame)
        current = vertex_world(effector['object'], effector['vertex_index'])
        release_error = max(release_error, (current - previous).length)
        released_separation.append((current - target_world(job, spec['binding'])).length)
    assert release_error < 2e-5, 'Zero-influence recovery followed the edited opponent after impact'
    assert max(released_separation) > .1, 'The strike never released its target'
    automatic = check_contacts(job)
    assert_contact_report(automatic)
    c_after = actor_frame_hashes(c)
    assert c_before == c_after, 'Coordinated A/B bake changed a third actor evaluated skin/pose'
    assert c_channels == [animation_fingerprint(c[k]) for k in ('armature', 'root')]
    reloaded = fresh_reload(job, folder, jobs, [spec])
    # Unsupported same-frame dependency cycles must reject before clearing any
    # already correct animation.  Only this disposable scene's plan is changed.
    cycle = {'source': 'procedural', 'start_frame': 1, 'end_frame': FRAMES, 'mask': ['right_arm'],
             'targets': {'RightHand': {'binding': {'actor_id': 'A', 'bone': 'Head'},
                         'contact': {'start_frame': 9, 'end_frame': 12, 'tolerance_m': .02, 'kind': 'strike'},
                         'keyframes': [{'frame': 1, 'influence': 0}, {'frame': 9, 'influence': 1},
                                       {'frame': 12, 'influence': 1}, {'frame': FRAMES, 'influence': 0}]}}}
    register_event(job, 'B', 'unsupported_cycle', cycle)
    before_cycle = {aid: [animation_fingerprint(actors[aid][k]) for k in ('armature', 'root')] for aid in actors}
    refused = None
    try:
        motion_commit(job, ['A', 'B'], 1, FRAMES)
    except ValueError as error:
        refused = str(error)
    assert refused and ('cycl' in refused.lower() or '循环' in refused), refused
    after_cycle = {aid: [animation_fingerprint(actors[aid][k]) for k in ('armature', 'root')] for aid in actors}
    assert before_cycle == after_cycle, 'Cycle rejection changed already baked actor animation'
    return {'passed': True, 'job_id': job, 'folder': str(folder), 'skin_effector': {**effector, **skin},
            'initial_contact': first_contact, 'after_target_event_edit_contact': second_contact,
            'same_frame_target_changed_min_m': target_change, 'commit_order': second_commit['commit_order'],
            'zero_influence_recovery': {'max_change_after_opponent_edit_m': release_error,
                                        'max_distance_from_opponent_after_release_m': max(released_separation),
                                        'frames_checked': len(released_before)},
            'third_actor_unchanged': {'frames_checked': len(c_before), 'all_skin_vertices_and_pose_matrices': True,
                                     'before_hashes': c_before, 'after_hashes': c_after},
            'automatic_contact': automatic, 'saved_reload': reloaded,
            'cyclic_dependency_rejected_without_animation_change': {'diagnostic': refused, 'passed': True}}


def constrained_prop_dependency_case(jobs):
    """An independent prop must carry the target actor's completed IK pose."""
    import bpy
    from mathutils import Vector
    from agent_character import actor_ensure, resolve_actor
    from agent_motion_timeline import register_event, motion_commit
    from agent_contacts import check_contacts
    results = []
    end = 8
    for constraint_type in ('COPY_LOCATION', 'CHILD_OF'):
        job, folder = new_scene(jobs, 'prop_' + constraint_type.lower(), frame_end=end)
        actor_ensure(job, 'A')
        actor_ensure(job, 'B')
        a, b = (resolve_actor(job, aid) for aid in ('A', 'B'))
        effector, a_palm, skin = pick_hand_vertex(a)
        b_wrist = b['armature'].matrix_world @ b['armature'].pose.bones['mixamorig:RightHand'].head
        b['root'].location += a_palm - b_wrist
        bpy.context.view_layer.update()
        b_wrist = b['armature'].matrix_world @ b['armature'].pose.bones['mixamorig:RightHand'].head
        bpy.ops.mesh.primitive_cube_add(size=.02, location=tuple(a_palm))
        prop = bpy.context.object
        prop.name = 'Independent_' + constraint_type + '_' + job
        constraint = prop.constraints.new(constraint_type)
        constraint.target = b['armature']
        constraint.subtarget = 'mixamorig:RightHand'
        if constraint_type == 'COPY_LOCATION':
            constraint.target_space = 'WORLD'
            constraint.owner_space = 'WORLD'
            # The planning frame has zero influence, but the prop follows B
            # later. Dependencies must account for animated constraints too.
            for frame, influence in ((1, 0), (3, 0), (4, 1), (end, 1)):
                constraint.influence = influence
                constraint.keyframe_insert('influence', frame=frame)
            linear_keys(prop)
            bpy.context.scene.frame_set(1)
        else:
            # Let Blender calibrate its actual bone-target transform, including
            # Beta's retained .01 armature scale and CHILD_OF bone semantics.
            # An identity inverse can put the disposable prop near the floor;
            # that would be an unreachable fixture, not a dependency failure.
            bpy.context.view_layer.update()
            inverse_result = bpy.ops.constraint.childof_set_inverse(constraint=constraint.name, owner='OBJECT')
            assert inverse_result == {'FINISHED'}, inverse_result
        bpy.context.view_layer.update()
        assert prop.parent is None and not prop.get('wm_actor_id')
        baseline_target = vertex_world(prop.name, 0)
        initial_fixture_error = (baseline_target - a_palm).length
        assert initial_fixture_error < .04, {'constraint': constraint_type, 'fixture_error_m': initial_fixture_error,
                                            'target': list(baseline_target), 'palm': list(a_palm)}
        a_event = pose_event(a, 'dependent_A', frame_end=end)
        a_event['targets'] = {'RightHand': {'binding': {'object': prop.name, 'vertex_index': 0},
            'effector': effector, 'influence': 1,
            'contact': {'start_frame': 1, 'end_frame': end, 'tolerance_m': .02, 'kind': 'grip'}}}
        b_event = pose_event(b, 'final_IK_target_B', frame_end=end)
        b_event['targets'] = {'RightHand': {'keyframes': [
            {'frame': 1, 'position': list(b_wrist + Vector((.03, .01, .03))), 'influence': 1},
            {'frame': end, 'position': list(b_wrist + Vector((.05, .01, .04))), 'influence': 1}],
            'contact': {'start_frame': 1, 'end_frame': end, 'tolerance_m': .02, 'kind': 'grip'}}}
        register_event(job, 'A', a_event['event_id'], a_event)
        register_event(job, 'B', b_event['event_id'], b_event)
        bpy.context.scene.frame_set(2, subframe=.25)
        planning_influence = float(constraint.influence)
        if constraint_type == 'COPY_LOCATION':
            assert planning_influence == 0
        committed = motion_commit(job, ['A'], 1, end)
        assert committed['commit_order'] == ['B', 'A'], committed
        assert set(committed['actor_ids']) == {'A', 'B'}, committed
        assert bpy.context.scene.frame_current == 2 and abs(bpy.context.scene.frame_subframe - .25) < 1e-7
        spec = {'effector': effector, 'binding': a_event['targets']['RightHand']['binding'],
                'start_frame': 1, 'end_frame': end, 'tolerance_m': .02}
        measured = measure_contact(job, spec)
        assert measured['passed'], {'constraint': constraint_type, **measured}
        target_deltas = [(Vector(row['target_world_m']) - baseline_target).length for row in measured['measurements']
                         if constraint_type != 'COPY_LOCATION' or row['frame'] >= 4]
        assert min(target_deltas) > .03, 'Target prop did not follow B beyond its pre-IK base pose'
        automatic = check_contacts(job)
        assert_contact_report(automatic)
        results.append({'passed': True, 'job_id': job, 'folder': str(folder),
                        'constraint': constraint_type, 'requested_actor_ids': ['A'],
                        'inferred_commit_order': committed['commit_order'], 'skin_effector': {**effector, **skin},
                        'initial_fixture_target_to_palm_m': initial_fixture_error,
                        'constraint_influence_at_planning_frame': planning_influence,
                        'minimum_prop_displacement_due_to_B_IK_m': min(target_deltas),
                        'independent_contact': measured, 'automatic_contact': automatic,
                        'success_preserved_frame_and_subframe': [2, .25]})
        write_json(folder / 'constrained-prop-result.json', results[-1])
    return {'passed': True, 'variants': results}


def transaction_state(actors):
    """Read observable Blender data, independently of coordinator backups."""
    import bpy
    from agent_motion_controls import CONTROL_STORE
    from agent_motion_timeline import STORE
    from agent_motion_quality import animation_fingerprint
    def matrix(value):
        return [float(v) for row in value for v in row]
    result = {'frame': bpy.context.scene.frame_current, 'subframe': float(bpy.context.scene.frame_subframe),
              'objects': {obj.name: obj.as_pointer() for obj in bpy.data.objects}, 'actors': {}, 'controls': {}}
    for aid, actor in actors.items():
        record = {'timeline': actor['root'].get(STORE), 'control_store': actor['root'].get(CONTROL_STORE),
                  'objects': {}, 'pose': {}, 'constraints': {}}
        for key in ('armature', 'root'):
            obj = actor[key]
            ad = obj.animation_data
            action = ad.action if ad else None
            slot = getattr(ad, 'action_slot', None) if ad else None
            record['objects'][key] = {'action_pointer': action.as_pointer() if action else None,
                'action_name': action.name if action else None,
                'slot_handle': slot.handle if slot else None, 'slot_identifier': slot.identifier if slot else None,
                'animation_fingerprint': animation_fingerprint(obj), 'matrix_basis': matrix(obj.matrix_basis),
                'matrix_world': matrix(obj.matrix_world)}
        evaluated = actor['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        for bone in actor['armature'].pose.bones:
            record['pose'][bone.name] = {'basis': matrix(bone.matrix_basis),
                                        'evaluated_matrix': matrix(evaluated.pose.bones[bone.name].matrix)}
            for constraint in bone.constraints:
                label = bone.name + ':' + constraint.name
                record['constraints'][label] = {'pointer': constraint.as_pointer(), 'type': constraint.type,
                    'mute': constraint.mute, 'influence': float(constraint.influence),
                    'pole_angle': float(constraint.pole_angle) if hasattr(constraint, 'pole_angle') else None}
                if constraint.name.startswith('WM_'):
                    for field in ('target', 'pole_target'):
                        control = getattr(constraint, field, None)
                        if control:
                            result['controls'][control.name] = {'matrix_basis': matrix(control.matrix_basis),
                                                                'matrix_world': matrix(control.matrix_world)}
        result['actors'][aid] = record
    return result


def compare_transaction_state(before, after):
    maximum = 0.
    differences = []
    def compare(first, second, path):
        nonlocal maximum
        if type(first) is float and type(second) is float:
            maximum = max(maximum, abs(first - second))
            if abs(first - second) > 2e-5:
                differences.append({'path': path, 'before': first, 'after': second})
        elif isinstance(first, dict) and isinstance(second, dict):
            if set(first) != set(second):
                differences.append({'path': path, 'missing': sorted(set(first) - set(second)),
                                    'added': sorted(set(second) - set(first))})
            for key in set(first) & set(second):
                compare(first[key], second[key], path + '.' + str(key))
        elif isinstance(first, list) and isinstance(second, list):
            if len(first) != len(second):
                differences.append({'path': path, 'before_length': len(first), 'after_length': len(second)})
            for i, (a, b) in enumerate(zip(first, second)):
                compare(a, b, path + '[' + str(i) + ']')
        elif first != second:
            differences.append({'path': path, 'before': first, 'after': second})
    compare(before, after, 'state')
    return {'passed': not differences, 'max_numeric_error': maximum, 'differences': differences}


def coordinated_failure_rollback_case(jobs):
    """Fail after the real final bake, so every rollback path is exercised."""
    import bpy
    from mathutils import Quaternion, Vector
    from unittest.mock import patch
    from agent_character import actor_ensure, resolve_actor
    from agent_motion_controls import controls_ensure, CONTROL_STORE
    import agent_motion_timeline as timeline
    end = 8
    job, folder = new_scene(jobs, 'injected_final_commit_failure', frame_end=end)
    actor_ensure(job, 'A')
    actor_ensure(job, 'B')
    actors = {aid: resolve_actor(job, aid) for aid in ('A', 'B')}
    a, b = actors['A'], actors['B']
    _, a_palm, _ = pick_hand_vertex(a)
    wrist = b['armature'].matrix_world @ b['armature'].pose.bones['mixamorig:RightHand'].head
    b['root'].location += a_palm - wrist
    bpy.context.view_layer.update()
    wrist = b['armature'].matrix_world @ b['armature'].pose.bones['mixamorig:RightHand'].head
    for aid, actor in actors.items():
        base = pose_event(actor, 'existing_registered_' + aid, frame_end=end)
        timeline.register_event(job, aid, base['event_id'], base)
        timeline.motion_commit(job, aid, 1, end)
    info = controls_ensure(job, 'A')
    chain = info['chains']['RightHand']
    goal, pole = (bpy.data.objects[chain[key]] for key in ('target_object', 'pole_object'))
    goal.location += Vector((.04, .02, .03))
    goal.rotation_quaternion = goal.rotation_quaternion @ Quaternion((0, 0, 1), .3)
    pole.location += Vector((.11, -.03, .02))
    pole.rotation_quaternion = Quaternion((0, 1, 0), .2)
    ik = a['armature'].pose.bones[chain['middle_bone']].constraints[chain['ik_constraint']]
    ik.pole_angle += .137
    assert b['root'].get(CONTROL_STORE) is None
    effector, _, _ = pick_hand_vertex(a)
    timeline.register_event(job, 'A', 'new_target_A', {'source': 'procedural', 'mask': ['right_arm'],
        'start_frame': 1, 'end_frame': end,
        'targets': {'RightHand': {'binding': {'actor_id': 'B', 'bone': 'RightHand'},
                                'effector': effector, 'influence': 1}}})
    timeline.register_event(job, 'B', 'new_target_B', {'source': 'procedural', 'mask': ['right_arm'],
        'start_frame': 1, 'end_frame': end,
        'targets': {'RightHand': {'position': list(wrist + Vector((.03, 0, .025))), 'influence': 1}}})
    bpy.context.scene.frame_set(2, subframe=.25)
    bpy.context.view_layer.update()
    before = transaction_state(actors)
    old_actions = set(bpy.data.actions.keys())
    calls = []
    actual_single = timeline._motion_commit_single
    def fail_after_final(*args, **kwargs):
        result = actual_single(*args, **kwargs)
        calls.append({'actor_id': args[1], 'base_only': kwargs.get('_base_only', False)})
        if args[1] == 'A' and not kwargs.get('_base_only', False):
            assert b['root'].get(CONTROL_STORE), 'B final pass did not create its new controls before injected failure'
            raise RuntimeError('Injected failure after A final commit finished')
        return result
    diagnostic = None
    with patch.object(timeline, '_motion_commit_single', side_effect=fail_after_final):
        try:
            timeline.motion_commit(job, ['A', 'B'], 1, end)
        except RuntimeError as error:
            diagnostic = str(error)
    assert diagnostic == 'Injected failure after A final commit finished', diagnostic
    assert calls == [{'actor_id': 'A', 'base_only': True}, {'actor_id': 'B', 'base_only': True},
                     {'actor_id': 'B', 'base_only': False}, {'actor_id': 'A', 'base_only': False}], calls
    after = transaction_state(actors)
    comparison = compare_transaction_state(before, after)
    assert comparison['passed'], comparison
    assert b['root'].get(CONTROL_STORE) is None
    assert bpy.context.scene.frame_current == 2 and abs(bpy.context.scene.frame_subframe - .25) < 1e-7
    new_actions = sorted(set(bpy.data.actions.keys()) - old_actions)
    assert not new_actions, {'failure_left_new_orphan_actions': new_actions}
    return {'passed': True, 'job_id': job, 'folder': str(folder), 'injected_diagnostic': diagnostic,
            'actual_commits_completed_before_failure': calls, 'restoration': comparison,
            'original_goal_pole_objects_checked': len(before['controls']),
            'new_B_control_store_and_controller_objects_removed': True,
            'failure_preserved_frame_and_subframe': [2, .25],
            'additional_orphan_actions_retained': new_actions,
            'scope': 'Original Action identity, slot, curves, current/evaluated pose, root transforms, '
                     'Goal/Pole matrices, constraint influence/mute/pole_angle, both stores, and object set restored'}


def reload_check(args):
    import bpy
    from agent_contacts import check_contacts, contact_requirements
    from agent_motion_quality import motion_check
    configure_jobs(args.jobs_root)
    bpy.ops.wm.open_mainfile(filepath=str(Path(args.reload_scene).resolve()), use_scripts=False)
    specs = json.loads(Path(args.reload_scene).with_name('independent-contact-specs.json').read_text())
    automatic = check_contacts(args.job_id)
    independent = [measure_contact(args.job_id, spec) for spec in specs]
    quality = motion_check(args.job_id)
    assert_contact_report(automatic)
    assert all(item['passed'] for item in independent), independent
    assert quality['passed'], quality.get('failures')
    result = {'passed': True, 'fresh_process': True, 'job_id': args.job_id,
              'requirements': contact_requirements(args.job_id), 'automatic_contact': automatic,
              'independent_contacts': independent, 'default_motion_check_passed': quality['passed'],
              'naturalness_verified': False, 'impact_feel_verified': False}
    write_json(args.report, result)


def main():
    import bpy
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', required=True)
    parser.add_argument('--reload-scene')
    parser.add_argument('--jobs-root')
    parser.add_argument('--job-id')
    parser.add_argument('--case', action='append', choices=('mesh_contact_case', 'paired_contact_case',
                        'constrained_prop_dependency_case', 'coordinated_failure_rollback_case'))
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    if not bpy.app.background:
        raise RuntimeError('Contacts acceptance must run in isolated background Blender')
    if args.reload_scene:
        return reload_check(args)
    report = Path(args.report).resolve()
    if report.exists():
        raise ValueError('Use a new report path; historical test evidence is never overwritten')
    work = report.parent / ('contact_blender_' + uuid.uuid4().hex[:12])
    work.mkdir(parents=True)
    jobs = work / 'jobs'
    checks = []
    cases = (mesh_contact_case, paired_contact_case, constrained_prop_dependency_case, coordinated_failure_rollback_case)
    selected_cases = [case for case in cases if not args.case or case.__name__ in args.case]
    for case in selected_cases:
        started = time.monotonic()
        try:
            result = case(jobs)
        except Exception as error:
            result = {'passed': False, 'error': str(error), 'traceback': traceback.format_exc()}
        result['case'] = case.__name__
        result['elapsed_s'] = round(time.monotonic() - started, 3)
        checks.append(result)
        print('CONTACT_ACCEPTANCE', json.dumps({'case': case.__name__, 'passed': result['passed'],
              'elapsed_s': result['elapsed_s'], 'error': result.get('error')}, ensure_ascii=False), flush=True)
    result = {'passed': all(check['passed'] for check in checks), 'blender_version': bpy.app.version_string,
              'work_directory': str(work), 'jobs_root': str(jobs), 'checks': checks,
              'selected_cases': [case.__name__ for case in selected_cases],
              'scope': 'Real Beta evaluated skin contact, same-frame target dependency/rebake, bounded strike '
                       'release, stale target detection, procedural execution, third actor preservation, fresh-process reload, '
                       'constraint-owned prop dependencies and transactional failed-bake restoration with subframes',
              'naturalness_verified': False, 'impact_feel_verified': False, 'text_model_interpretation_verified': False,
              'production_jobs_changed': False, 'gui_scene_changed': False, 'rendered_video': False}
    write_json(report, result)
    print('CONTACT_ACCEPTANCE_REPORT', report, flush=True)
    if not result['passed']:
        raise RuntimeError('Real Blender contacts acceptance failed; see ' + str(report))


if __name__ == '__main__':
    main()
