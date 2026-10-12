"""Saved Blender camera cuts with short attacker POV and full-body aftermath."""
import json
import math
import bpy
import bmesh
from mathutils import Vector


def shot_at(plan, frame):
    seconds = (frame - 1) / plan['fps']
    if plan['camera']['policy'] != 'cinematic':
        return {'kind': 'full'}
    return next((s for s in plan['camera']['shots'] if s['start'] <= seconds < s['end']), plan['camera']['shots'][-1])


def bone_point(actor, bone, fraction=0):
    b = actor.arm.pose.bones['mixamorig:' + bone]
    return actor.arm.matrix_world @ b.head.lerp(b.tail, fraction)


def pov_render_parts(actor):
    """Preserve collision meshes; split render copies so the owner's head cannot obscure POV."""
    heads = []
    for mesh in actor.meshes:
        groups = {g.index for g in mesh.vertex_groups if g.name in ('mixamorig:Head', 'mixamorig:Neck')}
        weights = {v.index: sum(g.weight for g in v.groups if g.group in groups) for v in mesh.data.vertices}
        head_faces = {p.index for p in mesh.data.polygons if sum(weights[i] for i in p.vertices) / len(p.vertices) >= .35}
        if not head_faces:
            continue
        for is_head in (False, True):
            part = mesh.copy()
            part.data = mesh.data.copy()
            part.name = actor.id + ('_POV_Head' if is_head else '_POV_Body')
            for collection in mesh.users_collection:
                collection.objects.link(part)
            bm = bmesh.new()
            bm.from_mesh(part.data)
            bm.faces.ensure_lookup_table()
            remove = [f for f in bm.faces if (f.index in head_faces) != is_head]
            bmesh.ops.delete(bm, geom=remove, context='FACES')
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
            bm.to_mesh(part.data)
            bm.free()
            if is_head:
                heads.append(part)
        mesh.hide_render = True
    return heads


def install_shots(plan, actors, full_camera):
    scene = bpy.context.scene
    if plan['camera']['policy'] != 'cinematic':
        return {'full': plan['frame_count'], 'pov': 0}
    heads = {key: pov_render_parts(actor) for key, actor in actors.items()}
    cameras = {}
    for shot in plan['camera']['shots']:
        if shot['kind'] == 'full':
            camera = full_camera
        else:
            key = shot['actor']
            if key not in cameras:
                data = bpy.data.cameras.new('POV_' + key)
                data.type = 'PERSP'
                data.clip_start = .065
                data.clip_end = 100
                data.sensor_width = 36
                camera = bpy.data.objects.new('POV_' + key, data)
                scene.collection.objects.link(camera)
                cameras[key] = camera
            camera = cameras[key]
        marker = scene.timeline_markers.new('Full body' if shot['kind'] == 'full' else 'POV ' + shot['actor'], frame=math.ceil(shot['start'] * plan['fps'] - 1e-8) + 1)
        marker.camera = camera
    counts = {'full': 0, 'pov': 0}
    for frame in range(1, plan['frame_count'] + 1):
        scene.frame_set(frame)
        shot = shot_at(plan, frame)
        counts[shot['kind']] += 1
        for key, parts in heads.items():
            for part in parts:
                part.hide_render = shot['kind'] == 'pov' and shot['actor'] == key
                part.keyframe_insert('hide_render', frame=frame)
        if shot['kind'] != 'pov':
            continue
        actor = actors[shot['actor']]
        other = actors['B' if actor.id == 'A' else 'A']
        yaw = actor.root.rotation_euler.z
        forward = Vector((math.sin(yaw), -math.cos(yaw), 0))
        camera = cameras[actor.id]
        camera.location = bone_point(actor, 'Head', .48) + forward * .06 + Vector((0, 0, .02))
        gaze = bone_point(other, 'Head', .55).lerp(bone_point(other, 'Spine2', .4), .20)
        camera.rotation_euler = (gaze - camera.location).to_track_quat('-Z', 'Y').to_euler()
        camera.data.lens = 36 / (2 * math.tan(math.radians(shot['fov']) / 2))
        camera.keyframe_insert('location', frame=frame)
        camera.keyframe_insert('rotation_euler', frame=frame)
        camera.data.keyframe_insert('lens', frame=frame)
    scene['duel_shots_json'] = json.dumps(plan['camera']['shots'])
    scene.frame_set(1)
    scene.camera = full_camera if shot_at(plan, 1)['kind'] == 'full' else cameras[shot_at(plan, 1)['actor']]
    return counts
