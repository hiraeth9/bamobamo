"""Persist an agent-authored Blender scene without a choreography schema.

Analysis is descriptive JSON.  The manifest describes the saved Blender file;
it never certifies the requested action, contact, or physical quality.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time
import uuid

import duel_plan


ANALYSIS_NAME = 'agent_analysis.json'
MANIFEST_NAME = 'agent_scene.json'
SCENE_NAME = 'scene.blend'


def contact_requirements_manifest(job_id):
    """Keep a concise description; the probe checks the scene's actual contacts."""
    from agent_contacts import contact_requirements
    registered = contact_requirements(job_id)
    fields = ('id', 'actor_id', 'event_id', 'chain', 'kind', 'frame_start', 'frame_end', 'tolerance_m')
    contacts = [{key: contact[key] for key in fields}
                for contact in registered['requirements']['contacts']]
    return {'contact_contract_version': registered['version'],
            'contact_requirements_status': registered['status'],
            'contact_requirements': {'version': registered['version'], 'contacts': contacts}}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_job(job_id):
    """A submission can write only to an existing, local request directory."""
    folder = duel_plan.job_directory(job_id)
    if folder.is_symlink() or folder.resolve().parent != duel_plan.JOBS.resolve():
        raise ValueError('任务目录不能指向其它位置')
    if not folder.is_dir() or not (folder / 'request.json').is_file() or (folder / 'request.json').is_symlink():
        raise ValueError('任务不存在；请使用当前任务的 job_id')
    return folder


def _request(folder):
    path = folder / 'request.json'
    raw = path.read_bytes()
    request = json.loads(raw)
    if not isinstance(request, dict) or not isinstance(request.get('prompt'), str):
        raise ValueError('任务缺少原始 prompt')
    return request, hashlib.sha256(raw).hexdigest()


@contextmanager
def _submission_lock(folder):
    with (folder / '.agent_scene.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _json_copy(value):
    if not isinstance(value, dict):
        raise ValueError('analysis 必须是 JSON 对象，内部字段不限动作、人数或类别')
    try:
        return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise ValueError('analysis 必须能保存为有限数值的 JSON') from error


def _analysis_document(job_id, request, request_hash, analysis):
    return {'version': 1, 'job_id': job_id, 'created_at': time.time(),
            'original_prompt': request['prompt'], 'request_sha256': request_hash,
            'analysis': _json_copy(analysis)}


def _write_temporary_json(folder, value, suffix):
    path = folder / ('.agent_' + uuid.uuid4().hex + suffix)
    try:
        with path.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _archive_current(folder):
    """Keep prior submissions and their output evidence before replacing them."""
    names = [ANALYSIS_NAME, MANIFEST_NAME, SCENE_NAME, 'agent_scene_probe.json',
             'preview.mp4', 'final.mp4', 'result.json', 'quality.json',
             'preview.log', 'rendering.log']
    existing = [folder / name for name in names if (folder / name).is_file() and not (folder / name).is_symlink()]
    if not existing:
        return None
    archive = folder / 'attempts' / 'agent_scene' / (str(time.time_ns()) + '_' + uuid.uuid4().hex[:8])
    archive.mkdir(parents=True)
    for source in existing:
        shutil.copy2(source, archive / source.name)
    return str(archive.relative_to(folder))


def analyze_job(job_id, analysis):
    folder = validate_job(job_id)
    with _submission_lock(folder):
        request, request_hash = _request(folder)
        document = _analysis_document(job_id, request, request_hash, analysis)
        temporary = _write_temporary_json(folder, document, '.analysis.json')
        try:
            archive = _archive_current(folder)
            os.replace(temporary, folder / ANALYSIS_NAME)
        finally:
            temporary.unlink(missing_ok=True)
    return {'ok': True, 'job_id': job_id, 'analysis_path': str(folder / ANALYSIS_NAME),
            'analysis_sha256': sha256_file(folder / ANALYSIS_NAME),
            'archive': archive, 'message': '原提示词与增强分析已保存；内部分析字段不限'}


def load_analysis(folder, request, request_hash):
    path = folder / ANALYSIS_NAME
    if not path.is_file():
        raise ValueError('先调用 wm_agent_analyze，或在 wm_agent_scene 中传入 analysis')
    document = json.loads(path.read_text(encoding='utf-8'))
    if (document.get('job_id') != folder.name or document.get('request_sha256') != request_hash
            or document.get('original_prompt') != request['prompt']):
        raise ValueError('增强分析与当前任务原提示词不一致；请重新分析并提交')
    _json_copy(document.get('analysis'))
    return document


def _render_enabled_objects(scene):
    """Collection/render flags only; this does not certify pixel visibility."""
    layers = getattr(scene, 'view_layers', None)
    if layers is None:
        return {id(obj) for obj in scene.objects if not getattr(obj, 'hide_render', False)}
    enabled = set()

    def visit(layer_collection, parent_enabled=True):
        collection = layer_collection.collection
        allowed = (parent_enabled and not layer_collection.exclude
                   and not getattr(collection, 'hide_render', False))
        if allowed:
            for obj in collection.objects:
                if not obj.hide_render:
                    enabled.add(obj.as_pointer() if hasattr(obj, 'as_pointer') else id(obj))
        for child in layer_collection.children:
            visit(child, allowed)

    for layer in layers:
        if getattr(layer, 'use', True):
            visit(layer.layer_collection)
    return enabled


def actor_geometry_facts(scene):
    enabled = _render_enabled_objects(scene)
    result = []
    for rig in scene.objects:
        if rig.type != 'ARMATURE':
            continue
        visible, other = [], []
        for mesh in scene.objects:
            if mesh.type != 'MESH':
                continue
            parent, seen = mesh.parent, set()
            parent_related = False
            while parent is not None and id(parent) not in seen:
                seen.add(id(parent))
                if parent == rig:
                    parent_related = True
                    break
                parent = parent.parent
            modifier_related = any(modifier.type == 'ARMATURE'
                                   and getattr(modifier, 'object', None) == rig
                                   and getattr(modifier, 'show_render', True)
                                   for modifier in getattr(mesh, 'modifiers', []))
            if not parent_related and not modifier_related:
                continue
            pointer = mesh.as_pointer() if hasattr(mesh, 'as_pointer') else id(mesh)
            renderable = pointer in enabled and bool(getattr(mesh.data, 'vertices', []))
            (visible if renderable else other).append(mesh.name)
        result.append({'rig_name': rig.name, 'related_visible_meshes': sorted(visible),
                       'related_hidden_or_empty_meshes': sorted(other)})
    return result


def appearance_warnings(facts):
    warnings = []
    for actor in facts.get('actor_geometry', []):
        if not actor['related_visible_meshes']:
            warnings.append('骨架 ' + actor['rig_name']
                            + ' 未找到通过父级链或可渲染 ARMATURE modifier 关联的可渲染网格；'
                            '骨架本身不能直接成为成片人物，如作为角色需 visible mesh。'
                            '其它约束关联未判定，此项仅提示。')
    if facts.get('render_engine') in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT', 'CYCLES'):
        if facts.get('render_enabled_light_count', facts.get('light_count', 0)) == 0:
            warnings.append('EEVEE/Cycles 场景没有可渲染 LIGHT；请确认 World 环境或 emission 发光提供照明。'
                            '环境/发光照明可以合法出片，此项仅提示。')
    return warnings


LIT_ENGINES = ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT', 'CYCLES')
_LIGHT_EPSILON = 1e-4


def _world_background(world):
    """Return (strength, [r, g, b]) for the world background, or (None, None)."""
    if world is None:
        return None, None
    if world.use_nodes:
        tree = world.node_tree
        background = tree.nodes.get('Background') if tree is not None else None
        if background is None:
            # A node-based world with no Background node emits nothing.
            return 0.0, [0.0, 0.0, 0.0]
        node_color = background.inputs[0].default_value
        color = [float(node_color[0]), float(node_color[1]), float(node_color[2])]
        try:
            strength = float(background.inputs[1].default_value)
        except (IndexError, TypeError, ValueError):
            strength = 1.0
        return strength, color
    return 1.0, [float(value) for value in world.color]


def _emissive_material_count():
    """Count materials with a real emission shader (EEVEE/Cycles light source)."""
    import bpy
    count = 0
    for material in getattr(bpy.data, 'materials', []):
        if not getattr(material, 'use_nodes', False) or material.node_tree is None:
            continue
        emitting = False
        for node in material.node_tree.nodes:
            if node.type == 'EMISSION':
                color = node.inputs[0].default_value if len(node.inputs) > 0 else (0, 0, 0, 1)
                strength = node.inputs[1].default_value if len(node.inputs) > 1 else 1.0
                if strength > _LIGHT_EPSILON and max(color[0], color[1], color[2]) > _LIGHT_EPSILON:
                    emitting = True
                    break
            elif node.type == 'BSDF_PRINCIPLED':
                try:
                    strength = float(node.inputs['Emission Strength'].default_value)
                    color = node.inputs['Emission Color'].default_value
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                if strength > _LIGHT_EPSILON and max(color[0], color[1], color[2]) > _LIGHT_EPSILON:
                    emitting = True
                    break
        if emitting:
            count += 1
    return count


def illumination_facts(scene):
    """Collection/render flags and shader inputs only; not pixel brightness."""
    enabled = _render_enabled_objects(scene)
    render_enabled = sum(
        obj.type == 'LIGHT' and (obj.as_pointer() if hasattr(obj, 'as_pointer') else id(obj)) in enabled
        for obj in scene.objects)
    strength, color = _world_background(scene.world)
    world_lit = bool(scene.world is not None and strength is not None
                     and strength > _LIGHT_EPSILON
                     and max(color or [0.0, 0.0, 0.0]) > _LIGHT_EPSILON)
    emissive = _emissive_material_count()
    needs_light = scene.render.engine in LIT_ENGINES
    illuminated = (not needs_light) or render_enabled > 0 or world_lit or emissive > 0
    return {'render_enabled_light_count': int(render_enabled),
            'world_present': scene.world is not None,
            'world_background_strength': strength,
            'world_background_color': color,
            'emissive_material_count': int(emissive),
            'illuminated': bool(illuminated),
            'illumination_scope': 'collection/render flags and shader inputs; not pixel brightness'}


def ensure_scene_lighting(scene, force=False):
    """Last-resort default World + three-point light for an EEVEE/Cycles scene.

    Runs only when the scene has no usable illumination at all, so a fully
    unlit scene can never render as a black video.  Everything it creates is
    reported back to the caller instead of being a silent success.
    """
    if scene.render.engine not in LIT_ENGINES:
        return {'applied': False, 'reason': 'engine_uses_display_lighting', 'created': []}
    facts = illumination_facts(scene)
    if facts['illuminated'] and not force:
        return {'applied': False, 'reason': 'already_lit', 'created': []}
    import bpy
    from mathutils import Vector

    created = []
    world = scene.world
    background = (world.node_tree.nodes.get('Background')
                  if world is not None and world.use_nodes and world.node_tree is not None else None)
    if background is None:
        world = bpy.data.worlds.new('World')
        world.use_nodes = True
        scene.world = world
        background = world.node_tree.nodes.get('Background')
        created.append('World')
    if background is not None:
        background.inputs[0].default_value = (0.05, 0.05, 0.055, 1.0)
        background.inputs[1].default_value = 1.0

    xs, ys, zs = [], [], []
    for obj in scene.objects:
        if obj.type == 'MESH':
            for corner in obj.bound_box:
                point = obj.matrix_world @ Vector(corner)
                xs.append(point.x)
                ys.append(point.y)
                zs.append(point.z)
    if xs:
        cx, cy, zmin, height = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs), max(zs) - min(zs)
    else:
        cx, cy, zmin, height = 0.0, 0.0, 0.0, 1.0
    scale = max(height, 0.5)

    def add_light(name, location, energy, size):
        data = bpy.data.lights.new(name, 'AREA')
        data.energy = energy
        data.size = size
        obj = bpy.data.objects.new(name, data)
        obj.location = location
        scene.collection.objects.link(obj)
        created.append(name)

    add_light('AutoKey', (cx + scale * 2, cy - scale * 2, zmin + scale * 2.5), scale * scale * 80, scale * 2)
    add_light('AutoFill', (cx - scale * 2, cy - scale * 2, zmin + scale * 1.2), scale * scale * 40, scale * 2.5)
    add_light('AutoRim', (cx, cy + scale * 2, zmin + scale * 3), scale * scale * 60, scale * 1.5)
    return {'applied': True, 'reason': 'scene_had_no_illumination', 'created': created}


def scene_facts(scene):
    """Facts are read from Blender, never copied from the agent's claims."""
    fps_nominal = int(scene.render.fps)
    fps_base = float(scene.render.fps_base)
    fps = fps_nominal / fps_base if fps_base else float('nan')
    start, end = int(scene.frame_start), int(scene.frame_end)
    if start > end or fps_nominal <= 0 or not math.isfinite(fps) or fps <= 0:
        raise ValueError('场景帧区间或帧率无效')
    camera = scene.camera
    camera_facts = None
    if camera is not None:
        camera_facts = {'name': camera.name, 'type': camera.type,
                        'matrix_world': [float(v) for row in camera.matrix_world for v in row]}
        if camera.type == 'CAMERA':
            camera_facts.update({'projection': camera.data.type, 'lens': float(camera.data.lens),
                                 'ortho_scale': float(camera.data.ortho_scale),
                                 'clip_start': float(camera.data.clip_start),
                                 'clip_end': float(camera.data.clip_end)})
    percentage = int(scene.render.resolution_percentage)
    illumination = illumination_facts(scene)
    return {'scene_name': scene.name, 'frame_start': start, 'frame_end': end,
            'frame_step': int(scene.frame_step), 'frame_count': end - start + 1,
            'fps_nominal': fps_nominal, 'fps_base': fps_base, 'fps': fps,
            'duration_seconds': (end - start + 1) / fps,
            'resolution': [int(scene.render.resolution_x * percentage / 100),
                           int(scene.render.resolution_y * percentage / 100)],
            'resolution_x': int(scene.render.resolution_x),
            'resolution_y': int(scene.render.resolution_y),
            'resolution_percentage': percentage,
            'render_engine': scene.render.engine, 'camera': camera_facts,
            'camera_markers': [{'frame': int(m.frame), 'camera': m.camera.name}
                               for m in scene.timeline_markers if m.camera is not None],
            'object_count': len(scene.objects),
            'armature_count': sum(o.type == 'ARMATURE' for o in scene.objects),
            'actor_geometry': actor_geometry_facts(scene),
            'actor_geometry_scope': 'parent/modifier association and render flags/nonempty mesh; not pixel visibility',
            'light_count': sum(o.type == 'LIGHT' for o in scene.objects),
            'render_enabled_light_count': illumination['render_enabled_light_count'],
            'world_present': illumination['world_present'],
            'world_background_strength': illumination['world_background_strength'],
            'world_background_color': illumination['world_background_color'],
            'emissive_material_count': illumination['emissive_material_count'],
            'illuminated': illumination['illuminated'],
            'illumination_scope': illumination['illumination_scope']}


def resource_facts():
    """Check Blender datablock dependencies; no inference from action labels."""
    import bpy
    resources = []
    for collection_name in ('images', 'fonts', 'sounds', 'libraries', 'movieclips', 'cache_files', 'volumes'):
        for item in getattr(bpy.data, collection_name, []):
            path = getattr(item, 'filepath', '')
            if not path or path == '<builtin>':
                continue
            if collection_name == 'images' and getattr(item, 'source', '') in ('GENERATED', 'VIEWER'):
                continue
            packed = bool(getattr(item, 'packed_file', None) or getattr(item, 'packed_files', None))
            absolute = bpy.path.abspath(path, library=getattr(item, 'library', None))
            candidates = [absolute]
            if '<UDIM>' in absolute and hasattr(item, 'tiles'):
                candidates = [absolute.replace('<UDIM>', str(tile.number)) for tile in item.tiles]
            missing = [] if packed else [value for value in candidates if not Path(value).exists()]
            resources.append({'kind': collection_name, 'name': item.name,
                              'path': absolute, 'packed': packed, 'missing': missing,
                              'source': getattr(item, 'source', None)})
    return resources


def evaluation_sample(scene, frame):
    """Small actual-pose/skin samples detect unsaved Python callbacks too."""
    import bpy
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    graph.update()
    objects = {}
    for obj in scene.objects:
        evaluated = obj.evaluated_get(graph)
        entry = {'type': evaluated.type,
                 'matrix_world': [float(v) for row in evaluated.matrix_world for v in row]}
        if evaluated.type == 'ARMATURE':
            entry['pose'] = {bone.name: [float(v) for row in bone.matrix for v in row]
                             for bone in evaluated.pose.bones}
        if evaluated.type == 'MESH':
            vertices = evaluated.data.vertices
            count = len(vertices)
            entry['vertex_count'] = count
            indices = sorted({round(i * (count - 1) / 16) for i in range(17)}) if count else []
            entry['mesh_samples'] = [[index, *[float(v) for v in
                                               (evaluated.matrix_world @ vertices[index].co)]]
                                      for index in indices]
        objects[obj.name] = entry
    return {'frame': int(frame), 'objects': objects}


def compare_evaluation_sample(expected, actual, tolerance=1e-4):
    """Compare captured numeric values, with a small reload rounding allowance."""
    differences = []

    def compare(left, right, path):
        if len(differences) >= 8:
            return
        if isinstance(left, dict) and isinstance(right, dict):
            if left.keys() != right.keys():
                differences.append({'path': path, 'message': 'fields/objects differ'})
            for key in left.keys() & right.keys():
                compare(left[key], right[key], path + '.' + str(key))
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                differences.append({'path': path, 'message': 'length differs'})
            for index, (a, b) in enumerate(zip(left, right)):
                compare(a, b, path + '[' + str(index) + ']')
        elif type(left) is int and type(right) is int:
            if left != right:
                differences.append({'path': path, 'expected': left, 'actual': right})
        elif (isinstance(left, (float, int)) and not isinstance(left, bool)
              and isinstance(right, (float, int)) and not isinstance(right, bool)):
            if not math.isfinite(left) or not math.isfinite(right) or abs(left - right) > tolerance:
                differences.append({'path': path, 'expected': left, 'actual': right})
        elif left != right:
            differences.append({'path': path, 'expected': left, 'actual': right})

    compare(expected, actual, 'sample')
    return differences


def robot_execution_facts(job_id, request, analysis=None):
    """Check the new asset policy at execution, without changing old scenes."""
    if request.get('character_policy') != 'library_beta':
        return {}
    from agent_character import scene_actor_facts
    from agent_motion_quality import scene_motion_facts
    if analysis is None:
        folder = validate_job(job_id)
        analysis = json.loads((folder / ANALYSIS_NAME).read_text(encoding='utf-8')).get('analysis', {})
    count = analysis.get('actor_count')
    described = analysis.get('actors')
    requires_actors = (isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0)
    requires_actors = requires_actors or (isinstance(described, (list, dict)) and bool(described))
    characters = scene_actor_facts(job_id, allow_empty=not requires_actors)
    if characters.get('errors'):
        raise ValueError('动作库机器人身份或绑定检查失败：' +
                         json.dumps(characters['errors'], ensure_ascii=False))
    motion = scene_motion_facts(job_id)
    if motion.get('errors'):
        raise ValueError('实际动作与片段记录不一致：' + json.dumps(motion['errors'], ensure_ascii=False))
    return {'character_policy': 'library_beta',
            'character_execution': characters,
            'motion_execution': motion}


def execution_sample_frames(scene, execution):
    """Capture event boundaries as well as global first/middle/last frames."""
    first, last = int(scene.frame_start), int(scene.frame_end)
    frames = {first, last, (first + last) // 2}

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('start_frame', 'end_frame', 'peak_frame', 'contact_frame'):
                    if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
                        frame = round(item)
                        frames.update(i for i in (frame - 1, frame, frame + 1) if first <= i <= last)
                elif key in ('event_sample_frames', 'events_sample_frames') and isinstance(item, list):
                    for sample in item:
                        if isinstance(sample, int) and not isinstance(sample, bool) and first <= sample <= last:
                            frames.add(sample)
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(execution.get('motion_execution', {}))
    return sorted(frames)


def camera_visibility_manifest(job_id):
    """Persist camera intent; background reload checks actual visibility."""
    from agent_camera_quality import camera_requirements
    try:
        registered = camera_requirements(job_id)
        return {'camera_visibility_version': 1,
                'camera_visibility_requirements': registered['requirements'],
                'camera_visibility_status': registered['status']}
    except (ValueError, TypeError, KeyError) as error:
        return {'camera_visibility_version': 1, 'camera_visibility_requirements': None,
                'camera_visibility_status': 'invalid',
                'camera_visibility_error': str(error)}


def submit_scene(job_id, analysis=None):
    """Save a copy quickly; worker performs the expensive independent reload."""
    import bpy
    folder = validate_job(job_id)
    temporary_scene = folder / ('.agent_' + uuid.uuid4().hex + '.blend')
    temporary_analysis = None
    temporary_manifest = None
    with _submission_lock(folder):
        request, request_hash = _request(folder)
        document = (_analysis_document(job_id, request, request_hash, analysis)
                    if analysis is not None else load_analysis(folder, request, request_hash))
        warnings = []
        if not document['analysis']:
            warnings.append('分析为空；此记录不能证明提示词已被完整理解')
        try:
            # Paths must remain usable when a GUI file is copied to a new job directory.
            bpy.ops.file.make_paths_absolute()
            for operation in ('pack_all', 'pack_libraries'):
                try:
                    result = getattr(bpy.ops.file, operation)()
                    if 'FINISHED' not in result:
                        warnings.append(operation + ' 未完成；重载检查会核对依赖')
                except (RuntimeError, AttributeError) as error:
                    warnings.append(operation + ': ' + str(error))
            scene = bpy.context.scene
            scene.frame_set(scene.frame_start)
            bpy.context.view_layer.update()
            # Finalize scene changes before recording the state checked after reload.
            lighting = ensure_scene_lighting(scene)
            if lighting.get('applied'):
                warnings.append('场景完全没有可渲染照明（无可见灯光、World 为空或全黑、无自发光）；'
                                '为保证成片不黑屏，已自动补默认 World 与三点光：'
                                + ', '.join(lighting.get('created', []))
                                + '。建议在场景中主动打灯以控制画面效果。')
            bpy.context.view_layer.update()
            execution = robot_execution_facts(job_id, request, document['analysis'])
            sample_frames = execution_sample_frames(scene, execution)
            samples = [evaluation_sample(scene, frame) for frame in sample_frames]
            scene.frame_set(scene.frame_start)
            bpy.context.view_layer.update()
            facts = scene_facts(scene)
            contact_contract = contact_requirements_manifest(job_id)
            camera_visibility = camera_visibility_manifest(job_id)
            if camera_visibility['camera_visibility_status'] not in ('registered', 'ready'):
                warnings.append('相机可见性契约缺失或无效；后台会检查并反馈，不以存在camera证明主体可见。')
            warnings.extend(appearance_warnings(facts))
            dependencies = resource_facts()
            for resource in dependencies:
                if resource['missing']:
                    warnings.append('外部资源缺失: ' + resource['name'])
                if resource.get('source') == 'SEQUENCE':
                    warnings.append('图片序列需要保留外部序列文件: ' + resource['name'])
            if scene.camera is None:
                warnings.append('场景没有默认相机；重载检查会验证实际帧的相机')
            editor = scene.sequence_editor
            strips = (getattr(editor, 'strips_all', getattr(editor, 'sequences_all', []))
                      if editor is not None else [])
            if (any(strip.type == 'SOUND' for strip in strips)
                    or any(obj.type == 'SPEAKER' and obj.data.sound is not None for obj in scene.objects)):
                warnings.append('场景含声音；当前 PNG→MP4 出片只编码视频，不包含音轨')
            temporary_analysis = _write_temporary_json(folder, document, '.analysis.json')
            result = bpy.ops.wm.save_as_mainfile(filepath=str(temporary_scene), copy=True,
                                                relative_remap=False, compress=True, check_existing=False)
            if 'FINISHED' not in result or not temporary_scene.is_file():
                raise RuntimeError('Blender 未保存真实场景')
            if _request(folder)[1] != request_hash:
                raise ValueError('保存时原提示词已变化，请重新分析提交')
            declared_actors = document['analysis'].get('actor_count')
            if declared_actors is None and isinstance(document['analysis'].get('actors'), (list, dict)):
                declared_actors = len(document['analysis']['actors'])
            if execution:
                actual_actors = len(execution['character_execution'].get('actors', []))
                if (isinstance(declared_actors, (int, float)) and not isinstance(declared_actors, bool)
                        and declared_actors != actual_actors):
                    warnings.append(f'分析声明 {declared_actors} 个人物，实际标准机器人登记 {actual_actors} 个；请核对遗漏。登记数量不证明每帧像素可见人数。')
            manifest = {'version': 1, 'kind': 'agent_scene', 'job_id': job_id,
                        'submission_id': uuid.uuid4().hex, 'created_at': time.time(),
                        'scene_path': str(folder / SCENE_NAME), 'analysis_path': str(folder / ANALYSIS_NAME),
                        **facts, 'actor_count': declared_actors,
                        **camera_visibility,
                        **contact_contract,
                        'contact_coverage': 'registered_geometry_only; natural_language_coverage_unverified',
                        **execution,
                        'actor_count_source': 'agent_analysis_metadata',
                        'scene_sha256': sha256_file(temporary_scene),
                        'analysis_sha256': sha256_file(temporary_analysis),
                        'request_sha256': request_hash, 'resources': dependencies, 'warnings': warnings,
                        'evaluation_samples': samples, 'evaluation_sample_tolerance': 1e-4,
                        'validation_scope': 'saved_scene_facts_only; physical and semantic quality are not certified'}
            temporary_manifest = _write_temporary_json(folder, manifest, '.manifest.json')
            archive = _archive_current(folder)
            # Manifest is the commit marker; a partial replacement cannot pass its hashes.
            os.replace(temporary_scene, folder / SCENE_NAME)
            os.replace(temporary_analysis, folder / ANALYSIS_NAME)
            os.replace(temporary_manifest, folder / MANIFEST_NAME)
            return {'ok': True, 'job_id': job_id, 'manifest_path': str(folder / MANIFEST_NAME),
                    'scene_path': str(folder / SCENE_NAME), 'scene_sha256': manifest['scene_sha256'],
                    'frame_count': manifest['frame_count'], 'fps': manifest['fps'], 'archive': archive,
                    'contact_contract_version': 1,
                    'contact_requirements_status': contact_contract['contact_requirements_status'],
                    'warnings': warnings, 'message': '真实 Blender 场景已保存；后台将重载检查并出片'}
        finally:
            temporary_scene.unlink(missing_ok=True)
            if temporary_analysis is not None:
                temporary_analysis.unlink(missing_ok=True)
            if temporary_manifest is not None:
                temporary_manifest.unlink(missing_ok=True)


def validate_manifest_files(manifest_path):
    """Pure checks bind the submitted scene to this request and analysis."""
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('version') != 1 or manifest.get('kind') != 'agent_scene':
        raise ValueError('不是有效的 agent_scene manifest')
    folder = validate_job(manifest.get('job_id'))
    if path.resolve() != (folder / MANIFEST_NAME).resolve():
        raise ValueError('manifest 必须位于本任务目录')
    for field, name in (('scene_path', SCENE_NAME), ('analysis_path', ANALYSIS_NAME)):
        if Path(manifest.get(field, '')).resolve() != (folder / name).resolve():
            raise ValueError(field + ' 必须为本任务文件')
        if (folder / name).is_symlink() or not (folder / name).is_file() or sha256_file(folder / name) != manifest.get(field.replace('_path', '_sha256')):
            raise ValueError(name + ' 内容或哈希已变化')
    request, request_hash = _request(folder)
    if manifest.get('request_sha256') != request_hash:
        raise ValueError('原始 request 已变化')
    if request.get('character_policy') == 'library_beta' and manifest.get('character_policy') != 'library_beta':
        raise ValueError('本任务要求动作库机器人；请用当前工具重新提交真实场景')
    load_analysis(folder, request, request_hash)
    return manifest, folder
