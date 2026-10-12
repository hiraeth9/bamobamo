"""Build reproducible two-actor scenes from arbitrary validated event sequences.

Source poses supply body motion; independent two-bone IK controls hands/feet.
All goals are world-space objects, so contact never creates a dependency cycle.
"""
import argparse
import contextlib
import io
import json
import math
import random
import hashlib
import sys
import time
from pathlib import Path

import bpy
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0, str(Path(__file__).resolve().parent))
from duel_plan import PROJECT, atomic_json, asset_manifest, catalog, validate_plan, interaction_rules
from duel_impacts import strike_reach, reaction_kind, response_amount, step_distance, recoil_step_progress
from duel_cameras import install_shots, shot_at

PREFIX = 'mixamorig:'
CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
ENGINE_SHA256 = {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                for name in ('duel_scene.py', 'duel_impacts.py', 'duel_cameras.py', 'duel_plan.py')}
REGION_BONES = {'head': 'Head', 'body': 'Spine1', 'abdomen': 'Spine', 'chest': 'Spine2'}


def smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def bell(t, peak=.46):
    return smooth(t / peak) if t <= peak else 1 - smooth((t - peak) / (1 - peak))


def point(arm, name, tail=False):
    bone = arm.pose.bones[PREFIX + name]
    return arm.matrix_world @ (bone.tail if tail else bone.head)


def target(name, location):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    obj.empty_display_type = 'SPHERE'
    obj.empty_display_size = .035
    obj.location = location
    obj.hide_render = True
    return obj


class MotionSources:
    """Cache local transforms from FBX. Providers share a bone-transform contract."""
    def __init__(self, definitions, needs_fall=False):
        self.definitions = definitions
        self.poses = {}
        self.ends = {}
        self.template = None
        self.meshes = []
        for name, definition in definitions.items():
            if definition['category'] == 'reaction' and not needs_fall:
                continue
            if not definition.get('asset'):
                continue
            before = set(bpy.data.objects)
            with contextlib.redirect_stdout(io.StringIO()):
                bpy.ops.import_scene.fbx(filepath=str(PROJECT / definition['asset']))
            added = set(bpy.data.objects) - before
            arm = next(o for o in added if o.type == 'ARMATURE')
            action = arm.animation_data.action
            start, end = [int(x) for x in action.frame_range]
            poses = []
            for frame in range(start, end + 1):
                bpy.context.scene.frame_set(frame)
                poses.append({b.name: (b.location.copy(), b.rotation_quaternion.copy(), b.scale.copy()) for b in arm.pose.bones})
            self.poses[name] = poses
            self.ends[name] = end
            if name == 'guard':
                self.template = arm
                self.meshes = [o for o in added if o.type == 'MESH']
            else:
                for obj in added:
                    bpy.data.objects.remove(obj, do_unlink=True)
        self.template.animation_data_clear()

    def sample(self, name, frame, mirror=False):
        poses = self.poses[name]
        f = min(len(poses) - 1, max(0, frame - 1))
        first = int(f)
        fraction = f - first
        a, b = poses[first], poses[min(first + 1, len(poses) - 1)]
        sampled = {key: (a[key][0].lerp(b[key][0], fraction), a[key][1].slerp(b[key][1], fraction), a[key][2].lerp(b[key][2], fraction)) for key in a}
        if not mirror:
            return sampled
        reflection = Matrix.Diagonal((-1.0, 1.0, 1.0))
        result = {}
        for key, (loc, rot, scale) in sampled.items():
            opposite = key.replace('Left', '#').replace('Right', 'Left').replace('#', 'Right')
            mirrored = (reflection @ rot.to_matrix() @ reflection).to_quaternion()
            result[opposite] = (Vector((-loc.x, loc.y, loc.z)), mirrored, scale)
        return result


class Actor:
    def __init__(self, actor_id, sources, x, yaw, grey):
        self.id = actor_id
        self.root = target(f'{actor_id}_Space', (x, 0, 0))
        self.root.rotation_euler.z = yaw
        collection = bpy.data.collections.new(f'Actor_{actor_id}')
        bpy.context.scene.collection.children.link(collection)
        self.arm = sources.template.copy()
        self.arm.data = sources.template.data.copy()
        self.arm.animation_data_clear()
        self.arm.name = f'{actor_id}_Rig'
        collection.objects.link(self.arm)
        self.arm.parent = self.root
        self.arm['actor_id'] = actor_id
        self.meshes = []
        material = bpy.data.materials.new(f'{actor_id}_Clay')
        material.diffuse_color = (grey, grey, grey, 1)
        material.use_nodes = True
        bsdf = material.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['Base Color'].default_value = (grey, grey, grey, 1)
        bsdf.inputs['Roughness'].default_value = .8
        for original in sources.meshes:
            mesh = original.copy()
            mesh.data = original.data.copy()
            mesh.name = f'{actor_id}_Body'
            collection.objects.link(mesh)
            mesh.parent = self.arm if original.parent == sources.template else self.root
            for modifier in mesh.modifiers:
                if modifier.type == 'ARMATURE':
                    modifier.object = self.arm
            mesh.data.materials.clear()
            mesh.data.materials.append(material)
            mesh['actor_id'] = actor_id
            self.meshes.append(mesh)
        self.surface_polygons = {}
        for mesh in self.meshes:
            masks = {}
            for region, names in {'head': {'Head', 'Neck'}, 'body': {'Hips', 'Spine', 'Spine1', 'Spine2'}}.items():
                groups = {g.index for g in mesh.vertex_groups if g.name.removeprefix(PREFIX) in names}
                selected = {v.index for v in mesh.data.vertices if any(g.group in groups and g.weight >= .25 for g in v.groups)}
                masks[region] = [tuple(p.vertices) for p in mesh.data.polygons if all(i in selected for i in p.vertices)]
            self.surface_polygons[mesh.name] = masks
        self.forward = Vector((math.sin(yaw), -math.cos(yaw), 0))
        self.lateral = Vector((math.cos(yaw), math.sin(yaw), 0))
        self.goals = {}
        self.poles = {}
        self.constraints = {}
        self.rotation_constraints = {}
        self.tip_names = {}
        self.anchors = {}
        self.foot_rotations = {}

    def set_pose(self, pose):
        for name, (loc, rot, scale) in pose.items():
            bone = self.arm.pose.bones[name]
            bone.rotation_mode = 'QUATERNION'
            bone.location = loc
            bone.rotation_quaternion = rot
            bone.scale = scale

    def add_ik(self):
        bpy.context.view_layer.update()
        for side in ('Left', 'Right'):
            for end, middle, start in (('Hand', 'ForeArm', 'Arm'), ('Foot', 'Leg', 'UpLeg')):
                key = side + end
                goal = target(f'{self.id}_{key}_Goal', point(self.arm, key))
                goal.rotation_mode = 'QUATERNION'
                goal.rotation_quaternion = (self.arm.matrix_world @ self.arm.pose.bones[PREFIX + key].matrix).to_quaternion()
                pole = target(f'{self.id}_{key}_Pole', self.pole_location(side, middle, start, point(self.arm, key)))
                constraint = self.arm.pose.bones[PREFIX + side + middle].constraints.new('IK')
                constraint.name = f'Duel_{key}_IK'
                constraint.target = goal
                constraint.pole_target = pole
                constraint.chain_count = 2
                constraint.use_stretch = False
                constraint.iterations = 64
                # Calibrate pole angle against the source elbow/knee; rig-dependent axes.
                expected = point(self.arm, side + middle)
                best = (float('inf'), 0)
                for step in range(16):
                    angle = -math.pi + step * math.pi / 8
                    constraint.pole_angle = angle
                    bpy.context.view_layer.update()
                    error = (point(self.arm, side + middle) - expected).length
                    if error < best[0]:
                        best = (error, angle)
                constraint.pole_angle = best[1]
                for bone in (side + middle, side + start):
                    self.arm.pose.bones[PREFIX + bone].ik_stretch = 0
                self.goals[key] = goal
                self.poles[key] = pole
                self.constraints[key] = constraint
                self.anchors[key] = goal.location.copy()
                self.tip_names[key] = side + ('HandMiddle1' if end == 'Hand' else 'ToeBase')
                rotation = self.arm.pose.bones[PREFIX + key].constraints.new('COPY_ROTATION')
                rotation.name = 'ContactOrientation'
                rotation.target = goal
                rotation.target_space = 'WORLD'
                rotation.owner_space = 'WORLD'
                self.rotation_constraints[key] = rotation
                if end == 'Foot':
                    self.foot_rotations[key] = goal.rotation_quaternion.copy()

    def pole_location(self, side, middle, start, goal):
        origin = point(self.arm, side + start)
        if middle == 'Leg':
            # Rotate the knee plane continuously with the hip-to-ankle axis.
            # A fixed forward pole becomes collinear as a kick crosses hip height.
            perpendicular = (goal - origin).cross(self.lateral)
            if perpendicular.length < .02:
                perpendicular = self.forward.copy()
            return origin.lerp(goal, .5) + perpendicular.normalized() * .7
        axis = goal - origin
        if axis.length < .02:
            axis = self.forward.copy()
        axis.normalize()
        preferred = Vector((0, 0, -.8)) + self.lateral * (.25 if side == 'Left' else -.25)
        perpendicular = preferred - axis * preferred.dot(axis)
        if perpendicular.length < .02:
            perpendicular = self.forward.copy()
        return origin.lerp(goal, .5) + perpendicular.normalized() * .6

    def hip_offset(self, offset):
        self.arm.pose.bones[PREFIX + 'Hips'].location += self.arm.matrix_world.to_3x3().inverted() @ offset

    def bake_pose(self, frame):
        for bone in self.arm.pose.bones:
            bone.keyframe_insert('rotation_quaternion', frame=frame, group=bone.name)
            if bone.name == PREFIX + 'Hips':
                bone.keyframe_insert('location', frame=frame, group=bone.name)
        self.root.keyframe_insert('location', frame=frame)
        self.root.keyframe_insert('rotation_euler', frame=frame)

    def face(self, other_position):
        delta = other_position - self.root.location
        if delta.xy.length > .01:
            yaw = math.atan2(delta.x, -delta.y)
            self.root.rotation_euler.z = yaw
            self.forward = Vector((math.sin(yaw), -math.cos(yaw), 0))
            self.lateral = Vector((math.cos(yaw), math.sin(yaw), 0))


def support_lowering(actor, desired, stance):
    lowering = 0.0
    for side in ('Left', 'Right'):
        key = side + 'Foot'
        if actor.id + ':' + key not in stance:
            continue
        upper = point(actor.arm, side + 'UpLeg')
        knee = point(actor.arm, side + 'Leg')
        ankle = point(actor.arm, key)
        length = ((upper - knee).length + (knee - ankle).length) * .985
        delta = upper - desired[key]
        horizontal_sq = delta.x ** 2 + delta.y ** 2
        if horizontal_sq < length ** 2:
            lowering = max(lowering, delta.z - math.sqrt(length ** 2 - horizontal_sq))
    return max(0, lowering)


def prepare_feet(actor, moving, active, phase, definitions, move_origins, frame_info):
    desired = {}
    for key in ('LeftFoot', 'RightFoot'):
        desired[key] = actor.anchors[key].copy()
        swing = False
        if actor.id in moving:
            delta, move_phase, transient = moving[actor.id]
            anchor_id = active['id'] + actor.id + key
            if anchor_id not in move_origins:
                move_origins[anchor_id] = actor.anchors[key].copy()
            progress = min(1, move_phase * 2) if key == 'LeftFoot' else max(0, move_phase * 2 - 1)
            desired[key] = move_origins[anchor_id] + delta * smooth(progress)
            swing = 0 < progress < 1 and delta.length > 0
            if swing:
                desired[key].z += .09 * math.sin(math.pi * progress)
            if not transient:
                actor.anchors[key] = desired[key].copy()
        if active and active['kind'] == 'strike' and active['actor'] == actor.id and definitions[active['motion']]['limb'] == 'foot' and key.lower().startswith(active['side']):
            swing = .01 < phase < .99
            lift_weight = smooth(phase / .15) * smooth((1 - phase) / .15)
            desired[key] = actor.anchors[key].lerp(point(actor.arm, key), lift_weight) if swing else actor.anchors[key].copy()
        (frame_info['swing'] if swing else frame_info['stance'])[actor.id + ':' + key] = list(desired[key])
    reachable = dict(frame_info['stance'])
    kicking = active and active['kind'] == 'strike' and active['actor'] == actor.id and definitions[active['motion']]['limb'] == 'foot'
    if actor.id in moving and not kicking:
        reachable.update(frame_info['swing'])
    lowering = min(.24 if actor.id in moving else .18, support_lowering(actor, desired, reachable))
    actor.hip_offset(Vector((0, 0, -lowering)))
    frame_info.setdefault('support_crouch_m', {})[actor.id] = lowering
    bpy.context.view_layer.update()
    return desired


def blend_pose(a, b, weight):
    return {name: (a[name][0].lerp(b[name][0], weight), a[name][1].slerp(b[name][1], weight), a[name][2].lerp(b[name][2], weight)) for name in a}


def region_center(actor, region):
    bone = actor.arm.pose.bones[PREFIX + REGION_BONES[region]]
    return actor.arm.matrix_world @ bone.head.lerp(bone.tail, .55 if region == 'head' else .4)


def surface_trees(actor, region):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for mesh in actor.meshes:
        polygons = actor.surface_polygons[mesh.name]['head' if region == 'head' else 'body']
        if polygons:
            evaluated = mesh.evaluated_get(depsgraph)
            vertices = [evaluated.matrix_world @ v.co for v in evaluated.data.vertices]
            yield BVHTree.FromPolygons(vertices, polygons)


def surface_contact(actor, region, from_position):
    center = region_center(actor, region)
    direction = from_position - center
    direction.normalize()
    origin = center + direction * 1.5
    hits = []
    for tree in surface_trees(actor, region):
        hit, normal, _, distance = tree.ray_cast(origin, -direction, 2)
        if hit is not None:
            hits.append((distance, hit, normal))
    if hits:
        _, hit, normal = min(hits, key=lambda value: value[0])
        return hit, normal, 'skinned_mesh_raycast'
    return center + actor.forward * (.11 if region == 'head' else .14), actor.forward.copy(), 'bone_surface_fallback'


def surface_distance(actor, region, position):
    values = [tree.find_nearest(position)[3] for tree in surface_trees(actor, region)]
    return min((v for v in values if v is not None), default=float('inf'))


def rotate_world(actor, bone_name, axis, angle):
    bone = actor.arm.pose.bones[PREFIX + bone_name]
    world = actor.arm.matrix_world.to_3x3() @ bone.matrix.to_3x3()
    local_axis = world.inverted() @ axis
    bone.rotation_quaternion @= Quaternion(local_axis.normalized(), angle)


def close_fists(actor, sources):
    fist = sources.sample('jab', sources.definitions['jab']['source_peak'])
    mirrored = sources.sample('jab', sources.definitions['jab']['source_peak'], True)
    for name in fist:
        if 'Hand' in name and any(finger in name for finger in ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky')):
            actor.arm.pose.bones[name].rotation_quaternion = (fist if 'Left' in name else mirrored)[name][1]


def source_frame(event, definition, time_seconds, end):
    peak = definition['source_peak']
    if time_seconds <= event['peak']:
        return 1 + (peak - 1) * smooth((time_seconds - event['start']) / (event['peak'] - event['start']))
    return peak + (end - peak) * smooth((time_seconds - event['peak']) / (event['end'] - event['peak']))


def action_fcurves(action):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                yield from bag.fcurves


def evaluated_corners(meshes):
    deps = bpy.context.evaluated_depsgraph_get()
    return [obj.matrix_world @ Vector(corner) for mesh in meshes for obj in [mesh.evaluated_get(deps)] for corner in obj.bound_box]


def setup_camera(meshes, plan, actors):
    scene = bpy.context.scene
    minima = Vector((float('inf'),) * 3)
    maxima = -minima
    separations = []
    for frame in range(1, scene.frame_end + 1):
        scene.frame_set(frame)
        for p in evaluated_corners(meshes):
            for axis in range(3):
                minima[axis] = min(minima[axis], p[axis])
                maxima[axis] = max(maxima[axis], p[axis])
        separations.append(point(actors['A'].arm, 'Spine2') - point(actors['B'].arm, 'Spine2'))
    center = (minima + maxima) / 2
    cam_data = bpy.data.cameras.new('Duel_FullCamera')
    cam_data.type = 'ORTHO'
    cam_data.clip_start = .05
    cam_data.clip_end = 100
    camera = bpy.data.objects.new('Duel_FullCamera', cam_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    requested = plan['camera']['azimuth_deg']
    candidates = [requested, -80, -75, -60, -50, -45]
    choices = []
    for azimuth in candidates:
        angle = math.radians(azimuth)
        camera.location = center + Vector((math.cos(angle) * 7, math.sin(angle) * 7, 2.3))
        camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
        bpy.context.view_layer.update()
        inverse = camera.matrix_world.inverted()
        projected = [inverse @ Vector((x, y, z)) for x in (minima.x, maxima.x) for y in (minima.y, maxima.y) for z in (minima.z, maxima.z)]
        width = max(p.x for p in projected) - min(p.x for p in projected)
        height = max(p.y for p in projected) - min(p.y for p in projected)
        scale = max(width, height * 1280 / 720) / .86
        right = camera.matrix_world.to_3x3().col[0]
        score = min(abs(right.dot(delta)) / scale for delta in separations)
        choices.append((score, azimuth, scale, camera.location.copy(), camera.rotation_euler.copy()))
        if azimuth == requested and score >= .05:
            break
    score, azimuth, scale, location, rotation = max(choices, key=lambda value: value[0])
    camera.location, camera.rotation_euler = location, rotation
    cam_data.ortho_scale = scale
    return {'min': list(minima), 'max': list(maxima), 'ortho_scale': scale, 'requested_azimuth_deg': requested, 'actual_azimuth_deg': azimuth, 'min_body_separation_in_frame': score}


def setup_stage():
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.fps = 30
    scene.render.fps_base = 1
    scene.render.image_settings.color_mode = 'RGB'
    scene.world = bpy.data.worlds.new('Duel_World')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.12, .12, .12, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = .45
    for name, position, power, size in [('Key', (0, -4, 5), 1100, 5), ('Fill', (-3, 0, 3), 600, 4), ('Rim', (1, 3, 4), 900, 3)]:
        data = bpy.data.lights.new(name, 'AREA')
        data.energy = power
        data.shape = 'DISK'
        data.size = size
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        obj.location = position
        obj.rotation_euler = (-obj.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -.015))
    floor = bpy.context.object
    floor.name = 'Stage_Floor'
    material = bpy.data.materials.new('Stage_Material')
    material.diffuse_color = (.16, .17, .19, 1)
    floor.data.materials.append(material)


def build(plan, folder):
    started = time.monotonic()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    definitions = catalog()['motions']
    sources = MotionSources(definitions, any(e.get('reaction') == 'knockdown' for e in plan['events']))
    initial_distance = .8
    for event in plan['events']:
        if event['kind'] == 'strike':
            break
        if event['kind'] == 'move' and event['direction'] == 'closer':
            initial_distance += event['distance'] * (2 if event['actor'] == 'both' else 1)
    actors = {'A': Actor('A', sources, -initial_distance / 2, math.pi / 2, .63), 'B': Actor('B', sources, initial_distance / 2, -math.pi / 2, .36)}
    idle = sources.sample('guard', 1)
    for actor in actors.values():
        actor.set_pose(idle)
    bpy.context.view_layer.update()
    for actor in actors.values():
        actor.add_ik()
    for obj in sources.meshes + [sources.template]:
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = plan['frame_count']
    scene.render.fps = plan['fps']
    frame_metadata = []
    contact_records = []
    # Freeze targets at event commitment. Not linked to the victim's moving bones.
    committed_targets = {}
    committed_normals = {}
    current_positions = {'A': Vector((-initial_distance / 2, 0, 0)), 'B': Vector((initial_distance / 2, 0, 0))}
    random_source = random.Random(plan['seed'])
    guard_offsets = {key: random_source.uniform(-.15, .15) for key in actors}
    move_origins = {}
    effective_moves = {}
    strike_origins = {}
    impact_states = {}
    previous_event = None
    for frame in range(1, scene.frame_end + 1):
        scene.frame_set(frame)
        seconds = (frame - 1) / plan['fps']
        active = next((e for e in plan['events'] if e['start'] <= seconds <= e['end']), None)
        phase = (seconds - active['start']) / (active['end'] - active['start']) if active else 0
        fall_weights = {}
        for actor in actors.values():
            for constraint in actor.constraints.values():
                constraint.influence = 0
            for constraint in actor.rotation_constraints.values():
                constraint.influence = 0
            pose = sources.sample('guard', 1 + 35 * (.5 - .5 * math.cos(seconds * 2.2 + guard_offsets[actor.id])))
            if active and active['kind'] == 'strike' and active['actor'] == actor.id:
                definition = definitions[active['motion']]
                weight = definition['source_weight'] * bell(phase, (active['peak'] - active['start']) / (active['end'] - active['start']))
                if definition.get('asset'):
                    f = source_frame(active, definition, seconds, sources.ends[active['motion']])
                    pose = blend_pose(pose, sources.sample(active['motion'], f, active['side'] != definition['side']), weight)
            impact = impact_states.get(actor.id)
            if impact and reaction_kind(impact['event']) == 'knockdown':
                dt = seconds - impact['contact_time']
                weight = smooth((dt - .08) / .22)
                fall_weights[actor.id] = weight
                source_time = min(1, max(0, (dt - .08) / 1.85))
                falling = sources.sample('fall_back', 1 + (sources.ends['fall_back'] - 1) * source_time)
                hip_key = PREFIX + 'Hips'
                first = sources.poses['fall_back'][0][hip_key][0]
                falling[hip_key] = (falling[hip_key][0] - first + idle[hip_key][0], falling[hip_key][1], falling[hip_key][2])
                pose = blend_pose(pose, falling, weight)
            actor.set_pose(pose)
            if fall_weights.get(actor.id, 0) < .5:
                close_fists(actor, sources)
            actor.root.location = current_positions[actor.id]
        bpy.context.view_layer.update()
        if previous_event and previous_event['kind'] == 'move' and active != previous_event:
            current_positions = {key: actor.root.location.copy() for key, actor in actors.items()}
        previous_event = active
        # Moves use alternating swing/support legs, independently of body-local poses.
        moving = {}
        if active and active['kind'] == 'move':
            event_id = active['id']
            if event_id not in move_origins:
                move_origins[event_id] = {k: current_positions[k].copy() for k in actors}
                spacing = (current_positions['B'] - current_positions['A']).xy.length
                divisor = 2 if active['actor'] == 'both' else 1
                effective_moves[event_id] = min(active['distance'], max(0, spacing - .8) / divisor) if active['direction'] == 'closer' else active['distance']
            for actor in actors.values():
                delta = Vector((0, 0, 0))
                if active['actor'] in (actor.id, 'both'):
                    if active['direction'] in ('apart', 'closer'):
                        delta = actor.forward * effective_moves[event_id] * (-1 if active['direction'] == 'apart' else 1)
                    else:
                        delta = Vector((0, active['distance'] * (1 if active['direction'] == 'left' else -1), 0))
                actor.root.location = move_origins[event_id][actor.id] + delta * smooth(phase)
                moving[actor.id] = (delta, phase, False)
                current_positions[actor.id] = actor.root.location.copy()
        if active and active['kind'] == 'strike':
            attacker = actors[active['actor']]
            defender = actors['B' if active['actor'] == 'A' else 'A']
            event_id = active['id']
            if event_id not in strike_origins:
                delta = current_positions[defender.id] - current_positions[attacker.id]
                distance = max(0, delta.xy.length - .8)
                direction = Vector((delta.x, delta.y, 0)).normalized()
                strike_origins[event_id] = (current_positions[attacker.id].copy(), direction * distance)
            origin, delta = strike_origins[event_id]
            approach = smooth((seconds - active['start']) / max(.08, active['peak'] - .20 - active['start']))
            if delta.length > .001:
                attacker.root.location = origin + delta * approach
                current_positions[attacker.id] = attacker.root.location.copy()
                moving[attacker.id] = (delta, approach, False)
            contact_time = round(active['peak'] * plan['fps']) / plan['fps']
            if active['outcome'] == 'hit' and seconds >= contact_time and (defender.id not in impact_states or impact_states[defender.id]['event']['id'] != event_id):
                direction = Vector((defender.root.location.x - attacker.root.location.x, defender.root.location.y - attacker.root.location.y, 0)).normalized()
                impact_states[defender.id] = {'event': active, 'contact_time': contact_time, 'origin': defender.root.location.copy(), 'direction': direction}
        for actor_id, impact in impact_states.items():
            event = impact['event']
            if impact['contact_time'] <= seconds <= event['end'] and step_distance(event) > 0:
                actor = actors[actor_id]
                progress = recoil_step_progress(event, seconds, plan['fps'])
                delta = impact['direction'] * step_distance(event)
                actor.root.location = impact['origin'] + delta * progress
                current_positions[actor_id] = actor.root.location.copy()
                moving[actor_id] = (delta, progress, False)
        for actor in actors.values():
            actor.face(current_positions['B' if actor.id == 'A' else 'A'])
        bpy.context.view_layer.update()
        if active and active['kind'] == 'strike':
            attacker = actors[active['actor']]
            defender = actors['B' if active['actor'] == 'A' else 'A']
            peak_phase = (active['peak'] - active['start']) / (active['end'] - active['start'])
            amplitude = bell(phase, peak_phase)
            definition = definitions[active['motion']]
            # Preserve residual pelvis movement, limiting source locomotion, then add lunge.
            for actor in (attacker,):
                hip = actor.arm.pose.bones[PREFIX + 'Hips']
                world_delta = actor.arm.matrix_world.to_3x3() @ (hip.location - idle[PREFIX + 'Hips'][0])
                horizontal = Vector((world_delta.x, world_delta.y, 0))
                if definition['limb'] == 'hand' and horizontal.length > .07:
                    actor.hip_offset(-horizontal + horizontal.normalized() * .07)
                elif definition['limb'] == 'foot':
                    support = 'LeftFoot' if active['side'] == 'right' else 'RightFoot'
                    drift = point(actor.arm, support) - actor.anchors[support]
                    actor.hip_offset(Vector((-drift.x, -drift.y, -.035 * amplitude)))
                    residual = actor.arm.matrix_world.to_3x3() @ (hip.location - idle[PREFIX + 'Hips'][0])
                    horizontal = Vector((residual.x, residual.y, 0))
                    if horizontal.length > .16:
                        actor.hip_offset(-horizontal + horizontal.normalized() * .16)
                actor.hip_offset(attacker.forward * (.17 if definition['limb'] == 'hand' else .04) * amplitude)
            twist = (.40 + .18 * active['intensity']) * amplitude * (1 if active['side'] == 'right' else -1)
            attacker.arm.pose.bones[PREFIX + 'Spine2'].rotation_quaternion @= Quaternion((0, 1, 0), twist)
            if active['motion'] == 'uppercut':
                attacker.hip_offset(Vector((0, 0, -.09 * math.sin(math.pi * phase))))
            if active['outcome'] == 'dodge':
                displacement = {'side': defender.lateral * .30, 'back': -defender.forward * .30, 'duck': Vector((0, 0, -.29))}[active['dodge']]
                if active['dodge'] == 'duck':
                    defender.hip_offset(displacement * amplitude)
                else:
                    defender.root.location += displacement * amplitude
                    moving[defender.id] = (displacement, amplitude, True)
                    defender.hip_offset(Vector((0, 0, -.04 * amplitude)))
            bpy.context.view_layer.update()
        for actor_id, impact in impact_states.items():
            actor = actors[actor_id]
            event = impact['event']
            response = response_amount(seconds - impact['contact_time']) * (1 - fall_weights.get(actor_id, 0))
            if response > .005:
                strength = event['intensity']
                axis = Vector((-impact['direction'].y, impact['direction'].x, 0))
                actor.hip_offset(impact['direction'] * (.045 + .11 * strength) * response + Vector((0, 0, -.055 * response)))
                bend = 1 if event['region'] == 'head' else -1
                rotate_world(actor, 'Spine', axis, bend * .14 * strength * response)
                rotate_world(actor, 'Spine1', axis, bend * .16 * strength * response)
                rotate_world(actor, 'Spine2', axis, bend * .12 * strength * response)
                if event['region'] == 'head':
                    rotate_world(actor, 'Head', axis, .24 * strength * response)
                    if event['motion'] == 'hook':
                        rotate_world(actor, 'Neck', Vector((0, 0, 1)), .20 * strength * response * (1 if event['side'] == 'right' else -1))
        bpy.context.view_layer.update()
        for actor_id, weight in fall_weights.items():
            if weight > 0:
                actor = actors[actor_id]
                ground = min(p.z for p in evaluated_corners(actor.meshes))
                if ground < -.008:
                    actor.hip_offset(Vector((0, 0, -.008 - ground)))
        bpy.context.view_layer.update()
        frame_info = {'frame': frame, 'stance': {}, 'swing': {}, 'event': active['id'] if active else None,
                      'fall_weights': fall_weights, 'roots': {k: list(a.root.location) for k, a in actors.items()},
                      'reactions': {k: {'event': i['event']['id'], 'type': reaction_kind(i['event']), 'amount': response_amount(seconds-i['contact_time'])} for k,i in impact_states.items()}}
        foot_goals = {}
        for actor in actors.values():
            if fall_weights.get(actor.id, 0) > 0:
                foot_goals[actor.id] = {key: point(actor.arm, key) for key in ('LeftFoot', 'RightFoot')}
                frame_info.setdefault('support_crouch_m', {})[actor.id] = 0
            else:
                foot_goals[actor.id] = prepare_feet(actor, moving, active, phase, definitions, move_origins, frame_info)
        if active and active['kind'] == 'strike':
            # Contact follows the prepared recipient pose until commitment/impact.
            # Dodge freezes at launch; hit/block freeze at peak. No bone-target cycles.
            if active['id'] not in committed_targets or (active['outcome'] != 'dodge' and seconds <= active['peak']):
                hit, normal, method = surface_contact(defender, active['region'], point(attacker.arm, active['side'].title() + 'Arm'))
                committed_targets[active['id']] = hit
                committed_normals[active['id']] = normal
                frame_info['surface_method'] = method
            frame_info['contact_target'] = list(committed_targets[active['id']])
        for actor in actors.values():
            natural = {key: point(actor.arm, key) for key in actor.goals}
            desired = {key: value.copy() for key, value in natural.items()}
            desired.update(foot_goals[actor.id])
            lowering = frame_info['support_crouch_m'][actor.id]
            rotations = {key: (actor.arm.matrix_world @ actor.arm.pose.bones[PREFIX + key].matrix).to_quaternion() for key in actor.goals}
            # A guard target maintains hands near the face throughout source changes.
            for key in ('LeftHand', 'RightHand'):
                head = point(actor.arm, 'Head')
                guard = head + actor.forward * .20 + actor.lateral * (.12 if key == 'LeftHand' else -.12) + Vector((0, 0, -.11))
                desired[key] = natural[key].lerp(guard, .75)
            if active and active['kind'] == 'strike':
                attacker = actors[active['actor']]
                definition = definitions[active['motion']]
                side = active['side'].title()
                limb = side + ('Hand' if definition['limb'] == 'hand' else 'Foot')
                goal = committed_targets[active['id']].copy()
                if actor == attacker:
                    peak_phase = (active['peak'] - active['start']) / (active['end'] - active['start'])
                    reach = strike_reach(active, seconds, definition['limb'])
                    tip_vector = point(actor.arm, actor.tip_names[limb]) - natural[limb]
                    contact_direction = -committed_normals[active['id']]
                    if contact_direction.dot(actor.forward) < 0:
                        contact_direction = -contact_direction
                    contact_offset = contact_direction * tip_vector.length
                    wrist_goal = goal - contact_offset
                    aimed = tip_vector.rotation_difference(contact_direction) @ rotations[limb]
                    rotations[limb] = rotations[limb].slerp(aimed, reach)
                    if definition['limb'] == 'foot':
                        if actor.id + ':' + limb in frame_info['swing']:
                            clearance = .13 * smooth(min(phase / .2, (1 - phase) / .2))
                            desired[limb].z = max(desired[limb].z, actor.anchors[limb].z + clearance)
                            desired[limb] = desired[limb].lerp(wrist_goal, reach)
                            arc_phase = phase / peak_phase if phase <= peak_phase else (phase - peak_phase) / (1 - peak_phase)
                            desired[limb] += actor.forward * (.20 * math.sin(math.pi * arc_phase))
                    else:
                        guard = desired[limb].copy()
                        trajectory = guard.lerp(wrist_goal, reach)
                        if active['motion'] == 'hook':
                            trajectory += actor.lateral * (.25 * math.sin(math.pi * min(1, phase / peak_phase))) * (1 if side == 'Right' else -1)
                        elif active['motion'] == 'uppercut':
                            trajectory.z -= .27 * math.sin(math.pi * min(1, phase / peak_phase))
                        desired[limb] = trajectory
                elif active['outcome'] == 'block':
                    # Block hand is in place before impact, then smoothly releases.
                    defense = smooth(phase / max(.1, peak_phase - .15)) if phase < peak_phase else 1 - smooth((phase - peak_phase) / (1 - peak_phase))
                    block_side = 'LeftHand' if active['side'] == 'right' else 'RightHand'
                    # Wrist contact point; hand geometry extends slightly beyond it.
                    desired[block_side] = desired[block_side].lerp(goal, defense)
                if actor == attacker and definition['limb'] == 'hand':
                    shoulder = point(actor.arm, side + 'Arm')
                    elbow = point(actor.arm, side + 'ForeArm')
                    wrist = point(actor.arm, side + 'Hand')
                    length = ((shoulder - elbow).length + (elbow - wrist).length) * .985
                    delta = desired[limb] - shoulder
                    if delta.length > length and delta.xy.length > .01:
                        correction = min(.14, delta.length - length + .012)
                        direction = Vector((delta.x, delta.y, 0)).normalized()
                        actor.hip_offset(direction * correction)
                        bpy.context.view_layer.update()
                        reachable = {**frame_info['stance'], **frame_info['swing']}
                        extra_lowering = min(.24 - lowering, support_lowering(actor, desired, reachable))
                        actor.hip_offset(Vector((0, 0, -extra_lowering)))
                        bpy.context.view_layer.update()
                        frame_info['support_crouch_m'][actor.id] += extra_lowering
                        natural = {key: point(actor.arm, key) for key in actor.goals}
            for key, goal in actor.goals.items():
                goal.location = desired[key]
                goal.keyframe_insert('location', frame=frame)
                end = 'Hand' if key.endswith('Hand') else 'Foot'
                middle, start = ('ForeArm', 'Arm') if end == 'Hand' else ('Leg', 'UpLeg')
                side = key[:-len(end)]
                pole = actor.poles[key]
                pole.location = actor.pole_location(side, middle, start, desired[key])
                pole.keyframe_insert('location', frame=frame)
                influence = 1 - fall_weights.get(actor.id, 0)
                actor.constraints[key].influence = influence
                actor.constraints[key].keyframe_insert('influence', frame=frame)
                goal.rotation_quaternion = actor.foot_rotations[key] if end == 'Foot' and actor.id + ':' + key in frame_info['stance'] else rotations[key]
                goal.keyframe_insert('rotation_quaternion', frame=frame)
                actor.rotation_constraints[key].influence = influence
                actor.rotation_constraints[key].keyframe_insert('influence', frame=frame)
            actor.bake_pose(frame)
        frame_metadata.append(frame_info)
        if frame % 60 == 0:
            print(f'DUEL_BUILD {frame}/{scene.frame_end}', flush=True)
    for action in bpy.data.actions:
        if action.name.startswith(('A_', 'B_')) or action.users:
            for curve in action_fcurves(action):
                for key in curve.keyframe_points:
                    key.interpolation = 'LINEAR'
    setup_stage()
    meshes = [mesh for actor in actors.values() for mesh in actor.meshes]
    camera_bounds = setup_camera(meshes, plan, actors)
    camera_counts = install_shots(plan, actors, scene.camera)
    # Evaluate the exact saved trajectories on every frame.
    quality = check_quality(plan, actors, frame_metadata, committed_targets)
    quality['camera_bounds'] = camera_bounds
    quality['camera_frames'] = camera_counts
    quality['effective_move_distances_m'] = effective_moves
    quality['asset_sha256'] = asset_manifest()
    quality['blender_version'] = bpy.app.version_string
    quality['scene_code_sha256'] = CODE_SHA256
    quality['engine_sources_sha256'] = dict(ENGINE_SHA256)
    quality['build_seconds'] = round(time.monotonic() - started, 2)
    atomic_json(folder / 'quality.json', quality)
    atomic_json(folder / 'trajectory.json', {'frames': frame_metadata, 'targets': {k: list(v) for k, v in committed_targets.items()}})
    scene['duel_plan_json'] = json.dumps(plan, ensure_ascii=False)
    scene['duel_job_id'] = plan['job_id']
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / 'scene.blend'))
    print('DUEL_SCENE ' + json.dumps({'ok': quality['passed'], 'quality': quality, 'scene': str(folder / 'scene.blend')}), flush=True)
    return quality


def check_quality(plan, actors, metadata, goals):
    scene = bpy.context.scene
    height = 1.65
    rules = interaction_rules()
    contact_tolerance = rules['contact_tolerance_height'] * height
    stance_tolerance = rules['stance_tolerance_height'] * height
    camera_violations, stance_violations, reach_violations, contact_records = [], [], [], []
    body_violations, occlusion_violations, flip_violations = [], [], []
    ground_violations, shot_violations = [], []
    max_stance, max_reach, max_joint_velocity = 0, 0, 0
    previous = {}
    previous_joints = {}
    margins = []
    for record in metadata:
        frame = record['frame']
        scene.frame_set(frame)
        shot = shot_at(plan, frame)
        expected_camera = 'Duel_FullCamera' if shot['kind'] == 'full' else 'POV_' + shot['actor']
        if scene.camera.name != expected_camera:
            shot_violations.append({'frame': frame, 'expected': expected_camera, 'actual': scene.camera.name})
        body_centers = []
        screen_centers = []
        for actor in actors.values():
            yaw = actor.root.rotation_euler.z
            actor.forward = Vector((math.sin(yaw), -math.cos(yaw), 0))
            body_centers.append((point(actor.arm, 'Hips') + point(actor.arm, 'Spine2')) / 2)
            screen_centers.append(world_to_camera_view(scene, scene.camera, point(actor.arm, 'Spine2')))
            for joint in ('LeftForeArm', 'RightForeArm', 'LeftLeg', 'RightLeg'):
                rotation = actor.arm.pose.bones[PREFIX + joint].matrix.to_quaternion()
                joint_id = actor.id + joint
                if joint_id in previous_joints:
                    angle = 2 * math.acos(min(1, abs(previous_joints[joint_id].dot(rotation))))
                    if angle > math.radians(65):
                        flip_violations.append({'frame': frame, 'joint': joint_id, 'angle_deg': round(math.degrees(angle), 2)})
                previous_joints[joint_id] = rotation.copy()
            corners = evaluated_corners(actor.meshes)
            if min(c.z for c in corners) < -.045:
                ground_violations.append({'frame': frame, 'actor': actor.id, 'min_z': round(min(c.z for c in corners), 4)})
            if shot['kind'] == 'full':
                for corner in corners:
                    uv = world_to_camera_view(scene, scene.camera, corner)
                    margin = min(uv.x, uv.y, 1 - uv.x, 1 - uv.y)
                    margins.append(margin)
                    if margin < rules['camera_margin'] - 1e-4 or uv.z < 0:
                        camera_violations.append({'frame': frame, 'actor': actor.id})
                        break
            elif actor.id != shot['actor']:
                visible = [world_to_camera_view(scene, scene.camera, region_center(actor, region)) for region in ('head', 'chest')]
                if not any(uv.z > 0 and 0 <= uv.x <= 1 and 0 <= uv.y <= 1 for uv in visible):
                    camera_violations.append({'frame': frame, 'actor': actor.id, 'reason': 'opponent outside POV'})
            for key, goal in actor.goals.items():
                actual = point(actor.arm, key)
                error = (actual - goal.location).length
                falling = record['fall_weights'].get(actor.id, 0) > 0
                if not falling:
                    max_reach = max(max_reach, error)
                if not falling and error > contact_tolerance:
                    reach_violations.append({'frame': frame, 'actor': actor.id, 'limb': key, 'error_m': round(error, 4)})
                name = actor.id + ':' + key
                if name in record['stance']:
                    anchor = Vector(record['stance'][name])
                    deviation = Vector((actual.x - anchor.x, actual.y - anchor.y, 0)).length
                    max_stance = max(max_stance, deviation)
                    if deviation > stance_tolerance:
                        stance_violations.append({'frame': frame, 'limb': name, 'error_m': round(deviation, 4)})
                if name in previous:
                    max_joint_velocity = max(max_joint_velocity, (actual - previous[name]).length * plan['fps'])
                previous[name] = actual.copy()
        if (body_centers[0] - body_centers[1]).length < .30:
            body_violations.append({'frame': frame, 'center_distance_m': round((body_centers[0] - body_centers[1]).length, 4)})
        if shot['kind'] == 'full' and abs(screen_centers[0].x - screen_centers[1].x) < .035:
            occlusion_violations.append({'frame': frame})
        for event in plan['events']:
            if event['kind'] != 'strike' or frame != round(event['peak'] * plan['fps']) + 1:
                continue
            attacker = actors[event['actor']]
            defender = actors['B' if event['actor'] == 'A' else 'A']
            definition = catalog()['motions'][event['motion']]
            limb = event['side'].title() + ('Hand' if definition['limb'] == 'hand' else 'Foot')
            attack = point(attacker.arm, attacker.tip_names[limb])
            error = (attack - goals[event['id']]).length
            entry = {'event': event['id'], 'frame': frame, 'outcome': event['outcome'], 'target_error_m': round(error, 4), 'passed': error <= contact_tolerance}
            if event['outcome'] == 'block':
                defense_limb = 'LeftHand' if event['side'] == 'right' else 'RightHand'
                entry['block_error_m'] = round((point(defender.arm, defense_limb) - goals[event['id']]).length, 4)
                entry['passed'] &= entry['block_error_m'] <= contact_tolerance
            if event['outcome'] == 'hit':
                entry['surface_error_m'] = round(surface_distance(defender, event['region'], attack), 4)
                entry['passed'] &= entry['surface_error_m'] <= contact_tolerance
                onset = next((r['frame'] for r in metadata if r['reactions'].get(defender.id, {}).get('event') == event['id'] and r['reactions'][defender.id]['amount'] > .05), None)
                entry['reaction_onset_frame'] = onset
                entry['reaction_sync_frames'] = abs(onset - frame) if onset is not None else 999
                entry['passed'] &= entry['reaction_sync_frames'] <= 2
                entry['reaction'] = reaction_kind(event)
                end_frame = min(len(metadata), round(event['end'] * plan['fps']) + 1)
                recoil = (Vector(metadata[end_frame-1]['roots'][defender.id]) - Vector(record['roots'][defender.id])).xy.length
                entry['recoil_step_m'] = round(recoil, 4)
                if reaction_kind(event) == 'stagger':
                    entry['passed'] &= recoil >= step_distance(event) * .9
                if reaction_kind(event) == 'knockdown':
                    scene.frame_set(scene.frame_end)
                    entry['final_head_height_m'] = round(region_center(defender, 'head').z, 4)
                    entry['final_hip_height_m'] = round(point(defender.arm, 'Hips').z, 4)
                    entry['passed'] &= entry['final_head_height_m'] < .45 and entry['final_hip_height_m'] < .35
                    scene.frame_set(frame)
            if event['outcome'] == 'dodge':
                region = point(defender.arm, REGION_BONES[event['region']])
                entry['dodge_clearance_m'] = round((attack - region).length, 4)
                entry['passed'] &= entry['dodge_clearance_m'] >= .20
            contact_records.append(entry)
    passed = not any((camera_violations, stance_violations, reach_violations, body_violations, occlusion_violations, flip_violations, ground_violations, shot_violations)) and all(r['passed'] for r in contact_records)
    return {'passed': passed, 'frame_count': len(metadata), 'actors_independent': actors['A'].arm.data != actors['B'].arm.data,
            'min_camera_margin': round(min(margins), 5) if margins else None, 'max_stance_error_m': round(max_stance, 5), 'max_ik_error_m': round(max_reach, 5),
            'max_joint_speed_m_s': round(max_joint_velocity, 3), 'camera_failures': camera_violations[:20],
            'stance_failures': stance_violations[:20], 'ik_failures': reach_violations[:20], 'contacts': contact_records,
            'body_overlap_failures': body_violations[:20], 'occlusion_failures': occlusion_violations[:20], 'joint_flip_failures': flip_violations[:20],
            'ground_failures': ground_violations[:20], 'shot_failures': shot_violations[:20],
            'visual_review_required': True, 'limitations': ['Geometry checks do not certify realism or all self-collisions; preview review required.']}


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--plan', required=True)
    options = args.parse_args(sys.argv[sys.argv.index('--') + 1:])
    path = Path(options.plan)
    raw = json.loads(path.read_text())
    result = build(validate_plan(raw, raw['job_id']), path.parent)
    if not result['passed']:
        raise RuntimeError('双人几何检查未通过，查看 quality.json')
