"""One actor opens a real door, crosses its aperture and closes from the far side.

The immutable scene request owns actions and timing. This executor owns safe
approaches, a persistent gait, planted feet and non-stretching bar contact.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from duel_plan import PROJECT, atomic_json
from single_plan import validate_single
from scene_plan import load_scene, allocate_interaction_times
from scene_objects import SceneObjects, scene_quality
from duel_scene import Actor, PREFIX, point, smooth, setup_stage, setup_camera, action_fcurves
from duel_motion_sources import MotionSources
from locomotion_path import compile_timeline
from locomotion_gait import LocomotionGait
from locomotion_scene import skin_vertices, angular_distance
from door_passage_geometry import DoorPassageGeometry
from door_interaction_pose import DoorInteractionPose
from reference_camera import setup_reference_camera, verify_reference_camera

ENGINE_FILES = ('door_passage_scene.py', 'door_passage_geometry.py',
                'door_interaction_pose.py', 'scene_objects.py', 'scene_plan.py',
                'locomotion_path.py', 'locomotion_gait.py', 'duel_scene.py',
                'duel_motion_sources.py', 'single_plan.py', 'camera_plan.py', 'reference_camera.py')
SOURCE_HASHES = {name: hashlib.sha256((PROJECT/'scripts'/name).read_bytes()).hexdigest()
                 for name in ENGINE_FILES}
REACH_SECONDS = .50
HINGE_SECONDS = 1.30
RELEASE_SECONDS = .50
SETTLE_SECONDS = .40


def navigation_blocked(props, geometry, xy):
    """An exterior approach strip belongs only to this actual doorway.

    Floor projections route roots. The baked skin is checked against every
    solid afterwards, including the wall and the changing door leaf.
    """
    if props.floor_blocked(xy, padding=.30):
        return True
    room = props.plan['room']
    if room['enabled']:
        width, depth, _ = room['size']
        if abs(xy[0]) > width/2-.35 or xy[1] < -depth/2+.5:
            return True
        if xy[1] > depth/2:
            local = geometry.local(xy)
            if abs(local.x) > geometry.width/2+.35 or local.y > 2.0:
                return True
    return False


def movement(points, yaw, final_yaw, duration, blocked):
    """Compile a walk and optional terminal turn without resetting its gait."""
    points = [list(p[:2]) for p in points]
    distance = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    terminal_heading = yaw
    if distance > .015:
        delta = Vector((*points[-1], 0))-Vector((*points[-2], 0))
        terminal_heading = math.atan2(delta.x, -delta.y)
    walk_minimum = 0.
    if distance > .015:
        probe = compile_timeline({'version': 1, 'start_position': points[0], 'start_yaw_deg': math.degrees(yaw),
            'segments': [{'start': 0, 'end': 50, 'gait': 'walk', 'waypoints': points}]}, 50,
            blocked=blocked, fps=30)
        walk_minimum = probe['segments'][0]['minimum_seconds']
        terminal_heading = probe['frames'][-1]['yaw']
    angle = angular_distance(final_yaw, terminal_heading) if final_yaw is not None else 0
    turn_minimum = max(.451, 1.5*math.degrees(angle)/120+.451) if angle > math.radians(1) else 0
    if distance > .015 and not turn_minimum:
        walk_minimum += .451
    minimum = walk_minimum+turn_minimum
    if duration is None:
        return minimum
    if duration+1e-7 < minimum:
        raise ValueError(f'门链走近与转身至少需要{minimum:.2f}秒，分配仅{duration:.2f}秒')
    duration = max(1/30, duration)
    segments = []
    if distance > .015:
        segments.append({'start': 0., 'end': duration-turn_minimum, 'gait': 'walk', 'waypoints': points})
    if turn_minimum:
        segments.append({'start': duration-turn_minimum, 'end': duration, 'gait': 'turn',
                         'end_yaw_deg': math.degrees(final_yaw)})
    if not segments:
        segments = [{'start': 0., 'end': duration, 'gait': 'idle'}]
    return compile_timeline({'version': 1, 'start_position': points[0], 'start_yaw_deg': math.degrees(yaw),
                             'segments': segments}, duration, blocked=blocked, fps=30)


def hand_vertex_masks(actor):
    masks = {'LeftHand': [], 'RightHand': []}
    for mesh in actor.meshes:
        for hand in masks:
            groups = {g.index for g in mesh.vertex_groups if g.name.startswith(PREFIX+hand)}
            masks[hand].extend(any(g.group in groups and g.weight >= .3 for g in v.groups)
                               for v in mesh.data.vertices)
    return {key: np.asarray(value, dtype=bool) for key, value in masks.items()}


def solid_collisions(world, props, hand_masks, contact_hand=None):
    failures = []
    for solid in props.solids:
        inverse = np.array(solid.matrix_world.inverted(), dtype=np.float64)
        local = world@inverse[:3, :3].T+inverse[:3, 3]
        bounds = np.array(solid.bound_box, dtype=np.float64)
        margin = min(.004, float((bounds.max(axis=0)-bounds.min(axis=0)).min())*.2)
        mask = np.all((local > bounds.min(axis=0)+margin)&(local < bounds.max(axis=0)-margin), axis=1)
        # The calibrated finger target leaves room for the actual hand skin.
        # Intentional contact never exempts vertices from solid penetration.
        if np.any(mask):
            failures.append({'part': solid.name, 'vertex_count': int(np.count_nonzero(mask))})
    return failures


def hand_bar_gap(world, mask, bar, target):
    patch = world[mask]
    patch = patch[np.linalg.norm(patch-np.asarray(target), axis=1) < .10]
    if not len(patch):
        return float('inf')
    inverse = np.asarray(bar.matrix_world.inverted(), dtype=float)
    local = patch@inverse[:3, :3].T+inverse[:3, 3]
    box = np.asarray(bar.bound_box, dtype=float)
    closest = np.maximum(box.min(axis=0), np.minimum(local, box.max(axis=0)))
    return float(np.linalg.norm(local-closest, axis=1).min())


def reference_panel(props, door_id):
    """See-through reference display; the original solid still blocks bodies.

    A single fixed camera cannot see both sides of every opaque leaf position.
    Only this specialized reference shot changes the panel's material; handles,
    hinges, a thin panel outline and every collision volume remain present.
    """
    entry = props.objects[door_id]
    leaf = next(obj for obj in entry['meshes'] if obj.name == door_id+'_leaf')
    display = leaf.data.materials[0].copy(); display.name = door_id+'_ReferencePanel'
    display.diffuse_color = (*display.diffuse_color[:3], .12)
    display.node_tree.nodes.get('Principled BSDF').inputs['Alpha'].default_value = .12
    display.surface_render_method = 'DITHERED'
    leaf.data.materials[0] = display; leaf['reference_panel_alpha'] = .12
    outline = bpy.data.objects.new(door_id+'_ReferenceOutline', leaf.data.copy())
    props.collection.objects.link(outline)
    outline.parent = leaf.parent; outline.matrix_basis = leaf.matrix_basis.copy()
    outline.data.materials.clear(); outline.data.materials.append(props.frame_mat)
    outline['prop_id'] = door_id; outline['prop_solid'] = False
    wire = outline.modifiers.new('Panel_Outline', 'WIREFRAME'); wire.thickness = .008
    wire.use_replace = True
    entry['meshes'].append(outline)
    # In this side view, a wall section and the jamb can also lie exactly in
    # front of the actor. Their reference materials must transmit the body;
    # changing display alpha does not remove them from physical clearance QA.
    structure = list(props.wall_meshes)+[obj for obj in entry['meshes']
                                         if obj.name.startswith(door_id+'_frame_')]
    structure_names = []
    for obj in structure:
        if obj.hide_render:
            continue
        material = obj.data.materials[0].copy()
        material.name = obj.name+'_ReferenceStructure'
        material.diffuse_color = (*material.diffuse_color[:3], .04)
        material.node_tree.nodes.get('Principled BSDF').inputs['Alpha'].default_value = .04
        material.surface_render_method = 'DITHERED'
        obj.data.materials[0] = material
        obj['reference_structure_alpha'] = .04
        structure_names.append(obj.name)
    return {'panel_alpha': .12, 'outline_width_m': .008,
            'structure_alpha': .04, 'structure_objects': structure_names,
            'collision_solid_retained': True, 'reason': 'Fixed full-body view on both sides of the leaf.'}


def bake(actor, frame, feet, hand=None):
    for key in actor.goals:
        if key.endswith('Foot'):
            desired, rotation, influence = feet['desired'][key], feet['rotations'][key], feet['influences'][key]
            pole = actor.pole_location(key.removesuffix('Foot'), 'Leg', 'UpLeg', desired)
        elif hand and key == hand['hand_name']:
            desired, rotation, influence = hand['desired'][key], hand['rotations'][key], hand['influences'][key]
            pole = hand['poles'][key]
        else:
            desired = point(actor.arm, key)
            rotation = (actor.arm.matrix_world@actor.arm.pose.bones[PREFIX+key].matrix).to_quaternion()
            influence = 0.
            pole = actor.poles[key].location.copy()
        goal = actor.goals[key]; goal.location = desired; goal.rotation_quaternion = rotation
        goal.keyframe_insert('location', frame=frame); goal.keyframe_insert('rotation_quaternion', frame=frame)
        actor.poles[key].location = pole; actor.poles[key].keyframe_insert('location', frame=frame)
        for constraint in (actor.constraints[key], actor.rotation_constraints[key]):
            constraint.influence = influence; constraint.keyframe_insert('influence', frame=frame)
    actor.bake_pose(frame)


def build(single, folder):
    folder = Path(folder)
    environment = load_scene(folder, single['job_id'], 1, single['duration_seconds'])
    if not environment or not any(e['action'] == 'pass_door' for e in environment['interactions']):
        raise ValueError('门洞执行器需要pass_door专用链')
    if single.get('locomotion'):
        raise ValueError('门链自身管理移动，不混入独立通用路线')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0
    sources = MotionSources({'guard': {'asset': 'mixamo_fbx/basic/Idle.fbx'},
        'walk': {'asset': 'mixamo_fbx/basic/Walk.fbx', 'extract_root': True},
        'run': {'asset': 'mixamo_fbx/basic/Run.fbx', 'extract_root': True},
        'grip': {'asset': 'mixamo_fbx/combat_melee/Lead Jab.fbx'}})
    props = SceneObjects(environment)
    events = [dict(e) for e in environment['interactions']]
    door_id = events[0]['object']; geometry = DoorPassageGeometry(props, door_id)
    opened = geometry.spec['open_angle_deg']
    initial_angle = geometry.angle; angle = initial_angle
    passage = next(e for e in events if e['action'] == 'pass_door')
    route = geometry.passage_route(passage['direction'], opened if initial_angle == 0 else initial_angle)
    first = events[0]
    if first['action'] == 'pass_door':
        initial = Vector(route['entry']); initial_yaw = route['yaw']
    else:
        stance = geometry.operation_stance(first['door_side'], angle)
        initial_yaw = stance['yaw']
        forward = Vector((math.sin(initial_yaw), -math.cos(initial_yaw), 0))
        initial = Vector(stance['position'])-forward*.35
    required = []; states = []; current = initial.copy(); yaw = initial_yaw
    blocked = lambda xy: navigation_blocked(props, geometry, xy)
    for event in events:
        props.set_angle(door_id, -math.radians(angle))
        if event['action'] == 'pass_door':
            route = geometry.passage_route(event['direction'], angle)
            points = [list(current[:2])]
            entry, exit = Vector(route['entry']), Vector(route['exit'])
            if events[len(states)+1:] and event['direction'] == 'outward':
                local_exit = geometry.local(exit); local_exit.y = .90 if event['direction'] == 'outward' else -.90
                exit = geometry.world(local_exit)
            if (current-entry).xy.length > .02:
                # Stay beyond the leaf tip before crossing towards the portal.
                if event['direction'] == 'outward':
                    retreat = geometry.local(current); retreat.y = min(retreat.y, -geometry.width-.40)
                    points.append(list(geometry.world(retreat)[:2]))
                points.append(list(entry[:2]))
            points.append(list(exit[:2]))
            side = 'outside' if event['direction'] == 'outward' else 'inside'
            following = events[len(states)+1:]  # Only one passage is supported.
            final_yaw = None
            if following:
                stance = geometry.operation_stance(side, angle)
                # The entire body first clears the aperture, then approaches
                # the bar on its new side; no root is moved through the leaf.
                if event['direction'] == 'inward':
                    local_exit = geometry.local(exit); local_exit.y = -geometry.width-.40
                    points[-1] = list(geometry.world(local_exit)[:2])
                    across = Vector(stance['local_position']); across.y = local_exit.y
                    points.append(list(geometry.world(across)[:2]))
                points.append(stance['position'][:2]); final_yaw = stance['yaw']
            minimum = movement(points, yaw, final_yaw, None, blocked)
            states.append({'points': points, 'yaw': yaw, 'final_yaw': final_yaw,
                           'angle_start': angle, 'angle_end': angle, 'route': route})
            required.append(minimum)
            current = Vector((*points[-1], 0)); yaw = final_yaw if final_yaw is not None else route['yaw']
        else:
            stance = geometry.operation_stance(event['door_side'], angle)
            points = [list(current[:2]), stance['position'][:2]]
            minimum = movement(points, yaw, stance['yaw'], None, blocked)
            end_angle = opened if event['action'] == 'open_door' else 0.
            states.append({'points': points, 'yaw': yaw, 'final_yaw': stance['yaw'],
                           'angle_start': angle, 'angle_end': end_angle,
                           'stance': stance, 'movement_minimum': minimum})
            required.append(minimum+REACH_SECONDS+HINGE_SECONDS+RELEASE_SECONDS+SETTLE_SECONDS)
            current = Vector(stance['position']); yaw = stance['yaw']; angle = end_angle
    atomic_json(folder/'door_preflight.json', {'required_seconds': required, 'events': states})
    allocated = allocate_interaction_times(events, required, environment['timing_policy'])
    environment['interactions'] = allocated
    for event, state, minimum in zip(allocated, states, required):
        total = event['end']-event['start']
        travel = total if event['action'] == 'pass_door' else total-REACH_SECONDS-HINGE_SECONDS-RELEASE_SECONDS-SETTLE_SECONDS
        props.set_angle(door_id, -math.radians(state['angle_start']))
        state['timeline'] = movement(state['points'], state['yaw'], state['final_yaw'], travel, blocked)
        state.update({'travel_seconds': travel, 'minimum_seconds': minimum})
    props.set_angle(door_id, -math.radians(initial_angle))
    actor = Actor('A', sources, initial.x, initial_yaw, .82)
    actor.root.location = initial; actor.set_pose(sources.sample('guard', 1))
    bpy.context.view_layer.update(); actor.add_ik()
    gait = LocomotionGait(actor, sources, fps=30); hand_pose = DoorInteractionPose(actor, sources)
    masks = hand_vertex_masks(actor)
    for obj in sources.meshes+[sources.template]:
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene; scene.frame_start = 1; scene.frame_end = single['frame_count']; scene.render.fps = 30
    current = initial.copy(); yaw = initial_yaw; baked_yaw = initial_yaw; angle = initial_angle
    records = []; last_distance = {}; last_turn = {}; total_distance = total_turn = 0.
    for frame in range(1, single['frame_count']+1):
        scene.frame_set(frame); seconds = (frame-1)/30
        index = next((i for i, e in enumerate(allocated) if e['start'] <= seconds < e['end']), None)
        event = allocated[index] if index is not None else None
        record = {'frame': frame, 'seconds': seconds, 'speed': 0., 'gait': 'idle', 'phase': 'idle',
                  'event': event['id'] if event else None, 'action': event['action'] if event else None,
                  'stance': {}, 'swing': {}, 'toe_stance': {}, 'contact': None}
        delta_distance = delta_turn = 0.; reach = 0.; hand = None
        if event:
            state = states[index]; elapsed = seconds-event['start']; travel = state['travel_seconds']
            samples = state['timeline']['frames']
            sample = samples[min(len(samples)-1, max(0, round(elapsed*30)))]
            current = Vector(sample['position']); yaw = sample['yaw']
            delta_distance = max(0., sample['distance']-last_distance.get(index, 0.))
            delta_turn = max(0., sample.get('turn_distance', 0.)-last_turn.get(index, 0.))
            last_distance[index] = sample['distance']; last_turn[index] = sample.get('turn_distance', 0.)
            if elapsed < travel:
                record.update({k: sample[k] for k in ('speed', 'gait', 'phase')})
            else:
                current = Vector((*state['points'][-1], 0)); yaw = state['final_yaw'] if state['final_yaw'] is not None else yaw
                if event['action'] != 'pass_door':
                    operating = elapsed-travel
                    reach = smooth(operating/REACH_SECONDS)*(1-smooth((operating-REACH_SECONDS-HINGE_SECONDS)/RELEASE_SECONDS))
                    progress = smooth((operating-REACH_SECONDS)/HINGE_SECONDS)
                    angle = state['angle_start']+(state['angle_end']-state['angle_start'])*progress
                    record['phase'] = 'reach' if operating < REACH_SECONDS else (
                        'operate' if operating < REACH_SECONDS+HINGE_SECONDS else 'release')
            if event['action'] == 'pass_door':
                angle = state['angle_start']
        # Euler keys must stay on one continuous branch, even when a local
        # event reports -13 degrees after the previous route passed 360.
        yaw = baked_yaw+math.atan2(math.sin(yaw-baked_yaw), math.cos(yaw-baked_yaw))
        baked_yaw = yaw
        props.set_angle(door_id, -math.radians(angle), frame)
        for constraint in (*actor.constraints.values(), *actor.rotation_constraints.values()):
            constraint.influence = 0.
        actor.set_pose(gait.pose(record['speed'], record['gait'], delta_distance, delta_turn, dt=1/30))
        actor.root.location = current; actor.root.rotation_euler.z = yaw
        actor.forward = Vector((math.sin(yaw), -math.cos(yaw), 0))
        actor.lateral = Vector((math.cos(yaw), math.sin(yaw), 0))
        bpy.context.view_layer.update()
        local_root = geometry.local(current)
        tuck_weight = smooth((1.75-local_root.xy.length)/.55)
        hand_pose.tuck_arms(tuck_weight)
        if event and event['action'] != 'pass_door' and reach > 0:
            contact = geometry.handle(event['door_side'], angle)
            inward = -geometry.leaf_normal(event['door_side'], angle)
            hand = hand_pose.apply(event['side'], contact, inward, weight=reach, hip_drop=.10)
            record['contact'] = {'hand': hand['hand_name'], 'tip': hand['contact_tip'],
                                 'target': list(contact), 'weight': reach, 'door_side': event['door_side']}
        total_distance += delta_distance; total_turn += delta_turn
        record.update({'root': list(current), 'position': list(current), 'yaw': yaw,
                       'door_angle_deg': angle, 'distance': total_distance, 'turn_distance': total_turn})
        feet = gait.feet(record); hand_pose.smooth_feet(feet, record)
        bake(actor, frame, feet, hand); records.append(record)
    for action in bpy.data.actions:
        if action.users:
            for curve in action_fcurves(action):
                for key in curve.keyframe_points: key.interpolation = 'LINEAR'
    if not single.get('camera'):
        props.traversal_cutaway(door_id)
        presentation = reference_panel(props, door_id)
        # Preserve the existing fixed-side reference shot for legacy plans.
        for wall in props.wall_meshes:
            if 'Right' in wall.name:
                wall.hide_render = True; wall['door_camera_cutaway'] = door_id
    elif single['camera']['occlusion_policy'] == 'transparent_architecture':
        presentation = reference_panel(props, door_id)
    else:
        presentation = {'policy': 'strict', 'collision_solid_retained': True}
    setup_stage()
    camera_meshes = actor.meshes+props.focus_meshes()
    if single.get('camera'):
        camera_bounds = setup_reference_camera(camera_meshes, single['camera'], {'A': actor}, props)
        camera_quality = verify_reference_camera(camera_meshes, single['camera'], {'A': actor}, props)
    else:
        camera_bounds = setup_camera(camera_meshes, {'camera': {'azimuth_deg': 0}}, {'A': actor})
        camera_quality = None
    quality = scene_quality(props, {'A': actor.arm}, single['frame_count'])
    if camera_quality is not None:
        quality.update(camera_facts=camera_bounds, camera_quality=camera_quality)
        quality['passed'] = bool(quality['passed'] and camera_quality['passed'])
    failures = []; max_contact = max_bar_gap = max_stance = max_joint = max_speed = max_yaw_rate = 0.
    previous_root = previous_yaw = None; rotations = {}; pass_samples = []; contact_frames = 0
    for record in records:
        scene.frame_set(record['frame']); bpy.context.view_layer.update()
        world = skin_vertices(actor)
        if float(world[:, 2].min()) < -.045:
            failures.append({'frame': record['frame'], 'kind': 'ground', 'minimum_z': float(world[:, 2].min())})
        contact = record['contact']
        active = contact['hand'] if contact and contact['weight'] > .05 else None
        for collision in solid_collisions(world, props, masks, active):
            failures.append({'frame': record['frame'], 'kind': 'skin_collision', **collision})
        if contact and contact['weight'] >= .999:
            error = (point(actor.arm, contact['tip'])-Vector(contact['target'])).length
            max_contact = max(max_contact, error); contact_frames += 1
            if error > .0495: failures.append({'frame': record['frame'], 'kind': 'hand_contact', 'error_m': error})
            label = 'front' if contact['door_side'] == 'inside' else 'back'
            bar = bpy.data.objects[door_id+'_grip_bar_'+label]
            gap = hand_bar_gap(world, masks[contact['hand']], bar, contact['target'])
            max_bar_gap = max(max_bar_gap, gap)
            if gap > .02:
                failures.append({'frame': record['frame'], 'kind': 'hand_skin_bar_gap', 'gap_m': gap if math.isfinite(gap) else None})
        for key, target in record['toe_stance'].items():
            error = (point(actor.arm, key.split(':')[-1])-Vector(target)).length
            max_stance = max(max_stance, error)
            if error > .0165: failures.append({'frame': record['frame'], 'kind': 'stance', 'error_m': error})
        for joint in ('Hips', 'Spine', 'LeftUpLeg', 'RightUpLeg', 'LeftLeg', 'RightLeg', 'LeftFoot', 'RightFoot',
                      'LeftArm', 'RightArm', 'LeftForeArm', 'RightForeArm'):
            rotation = actor.arm.pose.bones[PREFIX+joint].matrix.to_quaternion()
            if joint in rotations:
                step = math.degrees(2*math.acos(min(1., abs(rotation.dot(rotations[joint])))))
                max_joint = max(max_joint, step)
                if step > 65: failures.append({'frame': record['frame'], 'kind': 'joint_jump', 'joint': joint, 'angle_deg': step})
            rotations[joint] = rotation.copy()
        root = actor.root.matrix_world.translation.copy(); actual_yaw = actor.root.rotation_euler.z
        if previous_root is not None:
            max_speed = max(max_speed, (root-previous_root).xy.length*30)
            max_yaw_rate = max(max_yaw_rate, math.degrees(angular_distance(actual_yaw, previous_yaw))*30)
        previous_root, previous_yaw = root, actual_yaw
        if record['action'] == 'pass_door':
            inverse = np.array(geometry.inverse, dtype=float)
            local = world@inverse[:3, :3].T+inverse[:3, 3]
            local_root = geometry.local(root)
            pass_samples.append({'frame': record['frame'], 'minimum_y': float(local[:, 1].min()),
                                 'maximum_y': float(local[:, 1].max()), 'root_local': list(local_root)})
    sign = 1 if passage['direction'] == 'outward' else -1
    entered = any(p['maximum_y'] < -.02 if sign == 1 else p['minimum_y'] > .02 for p in pass_samples)
    exited = any(p['minimum_y'] > .02 if sign == 1 else p['maximum_y'] < -.02 for p in pass_samples)
    crossings = []
    band = geometry.passage_band(passage['direction'], next(state['angle_start'] for event, state in zip(allocated, states)
                                                            if event['action'] == 'pass_door'))
    for before, after in zip(pass_samples, pass_samples[1:]):
        a, b = before['root_local'], after['root_local']
        if sign*a[1] <= 0 < sign*b[1]:
            fraction = -a[1]/(b[1]-a[1]); x = a[0]+(b[0]-a[0])*fraction
            crossings.append({'frame': after['frame'], 'root_x': x,
                              'inside_target_aperture': band['minimum_root_x']-.002 <= x <= band['maximum_root_x']+.002})
    before_cross = bool(crossings and any(p['frame'] < crossings[0]['frame'] and
        (p['maximum_y'] < -.02 if sign == 1 else p['minimum_y'] > .02) for p in pass_samples))
    terminal_pass = bool(pass_samples and (pass_samples[-1]['minimum_y'] > .02 if sign == 1
                                           else pass_samples[-1]['maximum_y'] < -.02))
    passage_ok = bool(entered and exited and before_cross and terminal_pass and len(crossings) == 1
                      and crossings[0]['inside_target_aperture'])
    if not passage_ok:
        failures.append({'kind': 'whole_body_passage', 'entered': entered, 'exited': exited,
                         'ordered_crossing': before_cross, 'terminal_full_body': terminal_pass, 'crossings': crossings})
    terminal = []
    for frame in range(max(1, single['frame_count']-2), single['frame_count']+1):
        scene.frame_set(frame)
        for side in ('Left', 'Right'):
            terminal.append({'frame': frame, 'side': side, 'minimum_skin_z_m': gait._skin_min(side),
                             'toe_anchor_present': 'A:'+side+'ToeBase' in records[frame-1]['toe_stance']})
    terminal_ok = all(-.025 <= t['minimum_skin_z_m'] <= .03 and t['toe_anchor_present'] for t in terminal)
    if not terminal_ok: failures.append({'kind': 'terminal_feet'})
    if max_speed > 1.70 or max_yaw_rate > 125: failures.append({'kind': 'root_speed_or_yaw'})
    quality.update({'passed': bool(quality['passed'] and not failures), 'kind': 'door_passage',
        'frame_count': single['frame_count'], 'failures': failures[:100], 'failure_count': len(failures),
        'direction': passage['direction'], 'whole_body_entered': entered, 'whole_body_exited': exited,
        'whole_body_passage_passed': passage_ok, 'aperture_crossings': crossings,
        'contact_frames': contact_frames, 'max_hand_contact_error_m': max_contact, 'max_stance_error_m': max_stance,
        'max_hand_skin_bar_gap_m': max_bar_gap if math.isfinite(max_bar_gap) else None,
        'max_joint_step_deg': max_joint, 'max_root_speed_m_s': max_speed, 'max_yaw_rate_deg_s': max_yaw_rate,
        'terminal_feet_passed': terminal_ok, 'terminal_feet': terminal, 'camera_bounds': camera_bounds,
        'reference_presentation': presentation,
        'action_counts': dict(Counter(r['action'] for r in records)), 'engine_sources_sha256': SOURCE_HASHES,
        'visual_review_required': True, 'scope': 'One flat-ground actor and one hinged door; real bar hand IK, aperture crossing, deformed skin vs solid OBBs and whole-body framing.'})
    environment['execution'] = {'engine': 'door_passage', 'initial_door_angle_deg': initial_angle,
        'events': [{k: v for k, v in state.items() if k != 'timeline'} for state in states],
        'bar_contact_anchors': ['contact_front', 'contact_back'], 'geometry': route,
        'reference_presentation': presentation}
    atomic_json(folder/'executed_scene_plan.json', environment)
    atomic_json(folder/'trajectory.json', {'frames': records}); atomic_json(folder/'quality.json', quality)
    scene['single_plan_json'] = json.dumps(single); scene['scene_plan_json'] = json.dumps(environment)
    scene['door_passage_quality_json'] = json.dumps(quality)
    scene.frame_set(1); bpy.ops.wm.save_as_mainfile(filepath=str(folder/'scene.blend'))
    print('DOOR_PASSAGE '+json.dumps({k: quality[k] for k in ('passed', 'failure_count', 'max_hand_contact_error_m',
        'whole_body_entered', 'whole_body_exited')}, ensure_ascii=False), flush=True)
    return quality


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--plan', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:]); path = Path(args.plan)
    raw = json.loads(path.read_text()); quality = build(validate_single(raw, raw['job_id']), path.parent)
    if not quality['passed']:
        raise RuntimeError('门链接触、净空、足部支撑或构图检查失败，查看quality.json')
