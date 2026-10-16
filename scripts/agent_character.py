"""Task-owned real character profiles; no proxy geometry or inferred identity.

World/root coordinates are metres. Each profile retains its imported armature
scale and rest units: Beta uses centimetres; the bundled CC0 profile uses metres.
The module imports outside Blender so catalog/validation code can be tested.
"""
import hashlib
import json
import math
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CATALOG = PROJECT / 'knowledge/characters/catalog.json'
REGISTRY_KEY = 'wm_actor_registry'
JOB_KEY = 'wm_actor_job_id'
ACTOR_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
PREFIX = 'mixamorig:'
DEFAULT_ACTOR_COLOR = (.82, .82, .84, 1.)


def _bpy():
    import bpy
    return bpy


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def matrix_values(matrix, digits=5):
    return [[round(float(value), digits) for value in row] for row in matrix]


def rig_facts(armature):
    if armature.type != 'ARMATURE':
        raise ValueError('人物对象必须是实际 Armature')
    bones = [{'name': bone.name, 'parent': bone.parent.name if bone.parent else None,
              'matrix_local': matrix_values(bone.matrix_local),
              'head': [round(float(v), 5) for v in bone.head_local],
              'tail': [round(float(v), 5) for v in bone.tail_local],
              'use_deform': bool(bone.use_deform),
              'inherit_scale': bone.inherit_scale,
              'use_inherit_rotation': bool(bone.use_inherit_rotation)}
             for bone in sorted(armature.data.bones, key=lambda b: b.name)]
    return {'signature': _digest(bones), 'bone_count': len(bones), 'bones': bones}


def rig_signature(armature):
    return rig_facts(armature)['signature']


fingerprint_rig = rig_signature


def mesh_facts(mesh, armature):
    """Fingerprint undeformed data and weights, never an animated world pose."""
    groups = {group.index: group.name for group in mesh.vertex_groups}
    vertices = [[round(float(v), 5) for v in vertex.co] for vertex in mesh.data.vertices]
    polygons = [list(poly.vertices) for poly in mesh.data.polygons]
    weights = [[(groups.get(g.group, '<missing>'), round(float(g.weight), 6))
                for g in sorted(vertex.groups, key=lambda g: groups.get(g.group, ''))]
               for vertex in mesh.data.vertices]
    modifiers = [modifier for modifier in mesh.modifiers if modifier.type == 'ARMATURE']
    binding_ok = bool(modifiers) and all(modifier.object == armature and modifier.use_vertex_groups
                                        and modifier.show_render for modifier in modifiers)
    # Object-relative bind transform catches cancelled/doubled parent inverses.
    # Both objects share actor world motion, which cancels from this matrix.
    bind_transform = matrix_values(armature.matrix_world.inverted() @ mesh.matrix_world)
    # Hash static asset data.  Compare live matrix values with a measured
    # tolerance separately: actor translations can introduce float noise.
    identity = {'vertices': vertices, 'polygons': polygons, 'weights': weights}
    return {'name': mesh.name, 'signature': _digest(identity),
            'vertex_count': len(vertices), 'polygon_count': len(polygons),
            'armature_bind_transform': bind_transform,
            'binding_ok': binding_ok,
            'armature_modifiers': [{'name': m.name, 'target': m.object.name if m.object else None,
                                   'use_vertex_groups': bool(m.use_vertex_groups),
                                   'use_bone_envelopes': bool(m.use_bone_envelopes)} for m in modifiers]}


def binding_facts(meshes, armature):
    facts = [mesh_facts(mesh, armature) for mesh in meshes]
    return {'signature': _digest(sorted(item['signature'] for item in facts)),
            'meshes': facts, 'binding_ok': bool(facts) and all(f['binding_ok'] for f in facts)}


def compatible_rig(actual, expected, tolerance=0.001):
    """Tolerance is expressed in the character profile's rest-bone units."""
    current = {b['name']: b for b in actual['bones']}
    canonical = {b['name']: b for b in expected['bones']}
    if current.keys() != canonical.keys():
        return False
    for name, bone in current.items():
        other = canonical[name]
        if any(bone[key] != other[key] for key in ('parent', 'use_deform', 'inherit_scale', 'use_inherit_rotation')):
            return False
        if max(abs(a-b) for row, reference in zip(bone['matrix_local'], other['matrix_local'])
               for a, b in zip(row, reference)) > tolerance:
            return False
    return True


def _catalog_character(character_id='beta', verify_cache=True):
    document = json.loads(CATALOG.read_text(encoding='utf-8'))
    item = document['characters'].get(character_id)
    if not item:
        raise ValueError(f'人物资产不存在：{character_id}')
    source = PROJECT / item['source_asset']
    cache = PROJECT / item['cache_asset']
    if not source.is_file() or file_sha256(source) != item['source_sha256']:
        raise ValueError('人物源资产缺失或已改变：' + item['source_asset'] + '；请恢复该人物资产或重新构建缓存')
    if verify_cache and (not cache.is_file() or file_sha256(cache) != item['cache_sha256']):
        raise ValueError('人物缓存缺失或已改变：' + item['cache_asset'] + '；请重新构建人物缓存')
    return item


def default_character_id():
    """Prefer the installed local Beta; clean clones use the bundled CC0 rig."""
    for character_id in ('beta', 'quaternius_mannequin_f'):
        try:
            _catalog_character(character_id)
            return character_id
        except ValueError:
            continue
    raise ValueError('没有可用人物资产；请安装 bundled_assets 中的 CC0 人物或恢复本机 Beta 缓存')


def character_info(character_id=None, detail=False):
    character_id = default_character_id() if character_id is None else character_id
    item = _catalog_character(character_id)
    if detail:
        return {'ok': True, **item}
    return {'ok': True, **{key: item[key] for key in
            ('character_id', 'version', 'source_asset', 'source_sha256', 'cache_asset', 'cache_sha256',
             'height_m', 'world_unit', 'rest_bone_unit', 'up_axis', 'forward_axis', 'validation_state',
             'report', 'license', 'default_pose_source', 'default_pose_source_frame')},
            'catalog_path': str(CATALOG.relative_to(PROJECT)),
            'skeleton_signature': item['skeleton']['signature'],
            'bone_count': item['skeleton']['bone_count'],
            'binding_signature': item['binding']['signature'],
            'mesh_vertex_counts': [mesh['vertex_count'] for mesh in item['binding']['meshes']],
            'bone_roles': _bone_role_names()}


def _validate_actor(actor_id):
    if not isinstance(actor_id, str) or not ACTOR_PATTERN.fullmatch(actor_id):
        raise ValueError('actor_id 必须是稳定的字母数字角色 ID，不得包含路径')


def _job_scene(job_id, allow_claim=False):
    from agent_scene import validate_job
    validate_job(job_id)
    scene = _bpy().context.scene
    existing = scene.get(JOB_KEY)
    if existing and existing != job_id:
        raise ValueError(f'当前人物场景属于任务 {existing}，不能用于任务 {job_id}')
    if not existing:
        if not allow_claim:
            raise ValueError('当前场景尚未登记本任务人物')
        scene[JOB_KEY] = job_id
    return scene


def _registry(scene):
    value = json.loads(scene.get(REGISTRY_KEY, '{}'))
    if not isinstance(value, dict):
        raise ValueError('当前场景人物登记损坏')
    return value


def _write_registry(scene, value):
    scene[REGISTRY_KEY] = json.dumps(value, ensure_ascii=False, allow_nan=False)


def _bone_role_names():
    roles = {'hips': 'Hips', 'head': 'Head', 'neck': 'Neck', 'spine': 'Spine',
             'spine1': 'Spine1', 'spine2': 'Spine2'}
    for side in ('left', 'right'):
        for key, suffix in {'shoulder': 'Shoulder', 'arm': 'Arm', 'forearm': 'ForeArm',
                            'hand': 'Hand', 'up_leg': 'UpLeg', 'leg': 'Leg',
                            'foot': 'Foot', 'toe': 'ToeBase'}.items():
            roles[f'{side}_{key}'] = side.title() + suffix
    return {key: PREFIX + value for key, value in roles.items()}


def _bone_roles(armature):
    return {key: name for key, name in _bone_role_names().items() if name in armature.data.bones}


def _is_declared_prop(obj):
    """An explicitly labelled hand-held prop is separate from robot skin."""
    ancestor = obj
    while ancestor is not None:
        if ancestor.get('prop_id') or ancestor.get('wm_object_role') == 'prop':
            return True
        ancestor = ancestor.parent
    return False


def _identity_check(armature, meshes, character):
    skeleton = rig_facts(armature)
    binding = binding_facts(meshes, armature)
    if not compatible_rig(skeleton, character['skeleton']):
        raise ValueError('人物骨架或休止姿态与登记人物不兼容：' + character['character_id'])
    if not binding['binding_ok'] or binding['signature'] not in character['accepted_binding_signatures']:
        raise ValueError('人物原始网格、蒙皮或 Armature modifier 不符合登记人物：' + character['character_id'])
    reference_meshes = {mesh['signature']: mesh for mesh in character['binding']['meshes']}
    for mesh in binding['meshes']:
        reference = reference_meshes.get(mesh['signature'])
        if not reference or max(abs(a-b) for row, other in zip(mesh['armature_bind_transform'],
                                  reference['armature_bind_transform']) for a,b in zip(row,other)) > .002:
            raise ValueError('人物蒙皮对象的绑定变换或 parent inverse 与登记人物不一致：' + character['character_id'])
    expected_scale = character['object_transform']['scale']
    if any(abs(float(a)-float(b)) > 1e-6 for a, b in zip(armature.scale, expected_scale)):
        raise ValueError('人物骨架导入缩放与登记人物不一致：' + character['character_id'])
    return skeleton, binding


def actor_get(job_id, actor_id):
    _validate_actor(actor_id)
    scene = _job_scene(job_id)
    registry = _registry(scene)
    if actor_id not in registry:
        raise ValueError(f'当前任务未登记人物：{actor_id}')
    item = registry[actor_id]
    if item.get('job_id') != job_id or item.get('actor_id') != actor_id:
        raise ValueError('人物登记的任务或角色归属不一致')
    bpy = _bpy()
    objects = {name: bpy.data.objects.get(name) for name in
               [item['armature_object'], item['root_object'], *item['mesh_objects']]}
    if any(obj is None or obj.name not in scene.objects for obj in objects.values()):
        raise ValueError('已登记人物对象缺失或不属于当前场景')
    armature = objects[item['armature_object']]
    root = objects[item['root_object']]
    meshes = [objects[name] for name in item['mesh_objects']]
    for obj in [root, armature, *meshes]:
        if obj.get('wm_actor_id') != actor_id or obj.get('wm_job_id') != job_id:
            raise ValueError('人物对象的角色归属不一致')
    if armature.parent != root:
        raise ValueError('人物骨架不再挂接该人物空间根节点')
    character = _catalog_character(item['character_id'])
    skeleton, binding = _identity_check(armature, meshes, character)
    # A copied label cannot certify identity; these values came from actual data.
    result = dict(item)
    result.update(ok=True, skeleton_signature=skeleton['signature'],
                  binding_signature=binding['signature'], binding_verified=True,
                  bone_roles=_bone_roles(armature),
                  armature_scale=list(armature.scale))
    return result


actor_info = actor_get


def _record_names(entry):
    if not isinstance(entry, dict):
        return []
    names = [entry.get('root_object'), entry.get('armature_object')]
    names.extend(entry.get('mesh_objects', []) if isinstance(entry.get('mesh_objects', []), list) else [])
    return list(dict.fromkeys(name for name in names if isinstance(name, str) and name))


def actor_list(job_id):
    """List actual registry/object state even when a previous edit broke a rig."""
    from agent_scene import validate_job, _render_enabled_objects
    validate_job(job_id)
    bpy = _bpy()
    scene = bpy.context.scene
    if scene.get(JOB_KEY) and scene.get(JOB_KEY) != job_id:
        raise ValueError('当前人物场景不属于该任务')
    enabled = _render_enabled_objects(scene)
    actors = []
    for actor_id, entry in sorted(_registry(scene).items()):
        names = _record_names(entry)
        facts = []
        for name in names:
            obj = bpy.data.objects.get(name)
            present = obj is not None and obj.name in scene.objects
            owned = bool(present and obj.get('wm_job_id') == job_id and obj.get('wm_actor_id') == actor_id)
            facts.append({'name': name, 'exists': obj is not None, 'in_current_scene': present,
                          'owned': owned, 'type': obj.type if obj else None,
                          'render_enabled': bool(present and obj.as_pointer() in enabled and not obj.hide_render)})
        valid_record = isinstance(entry, dict) and entry.get('job_id') == job_id and entry.get('actor_id') == actor_id
        meshes = entry.get('mesh_objects', []) if isinstance(entry, dict) and isinstance(entry.get('mesh_objects', []), list) else []
        rendered = any(item['render_enabled'] and item['name'] in meshes for item in facts)
        actors.append({'actor_id': actor_id, 'character_id': entry.get('character_id') if isinstance(entry, dict) else None,
                       'root_object': entry.get('root_object') if isinstance(entry, dict) else None,
                       'armature_object': entry.get('armature_object') if isinstance(entry, dict) else None,
                       'mesh_objects': meshes, 'collection': entry.get('collection') if isinstance(entry, dict) else None,
                       'registry_job_matches': valid_record, 'rendered': rendered,
                       'missing_objects': [item['name'] for item in facts if not item['in_current_scene']],
                       'ownership_mismatches': [item['name'] for item in facts if item['in_current_scene'] and not item['owned']],
                       'objects': facts,
                       'status': 'present_unverified' if valid_record and names and all(item['owned'] for item in facts) else 'stale_or_mismatched'})
    return {'ok': True, 'job_id': job_id, 'actors': actors, 'registered_actor_count': len(actors),
            'scope': 'Actual presence/ownership/visibility for recovery; not a skin or binding identity certificate.'}


def actor_remove(job_id, actor_id):
    """Remove one verified-owned actor, including a partially deleted registry.

    Shared data blocks are never purged. Foreign objects and declared props are
    retained; immediate world transforms survive removal of their old parent.
    """
    _validate_actor(actor_id)
    scene = _job_scene(job_id)
    bpy = _bpy()
    registry = _registry(scene)
    entry = registry.get(actor_id)
    if entry is None:
        return {'ok': True, 'job_id': job_id, 'actor_id': actor_id, 'registry_removed': False,
                'removed_objects': [], 'status': 'already_absent'}
    if not isinstance(entry, dict) or entry.get('job_id') != job_id or entry.get('actor_id') != actor_id:
        raise ValueError('人物登记归属不一致；不能删除其它任务或角色')
    names = _record_names(entry)
    other_names = {name for key, value in registry.items() if key != actor_id for name in _record_names(value)}
    collection = bpy.data.collections.get(entry.get('collection', ''))
    root = bpy.data.objects.get(entry.get('root_object', ''))
    def owned(obj):
        return obj.get('wm_job_id') == job_id and obj.get('wm_actor_id') == actor_id
    core = {obj for name in names if (obj := bpy.data.objects.get(name)) is not None and owned(obj)}
    controls = set()
    events_count = 0
    warnings = []
    if root in core:
        try:
            store = json.loads(root.get('wm_actor_controls_v1', '{"chains":{}}'))
            for chain in store.get('chains', {}).values():
                controls.update(chain.get(key) for key in ('target_object', 'pole_object') if chain.get(key))
            events_count = len(json.loads(root.get('wm_motion_timeline_v1', '{"events":[]}')).get('events', []))
        except (ValueError, TypeError, AttributeError):
            warnings.append('Owned actor control/timeline metadata was malformed; object ownership is checked independently.')
    # Older real controls have no owner tags. Their actual rig references,
    # persistent names and dedicated actor collection jointly prove ownership.
    for rig in core:
        if rig.type == 'ARMATURE':
            for bone in rig.pose.bones:
                for constraint in bone.constraints:
                    for attr in ('target', 'pole_target'):
                        target = getattr(constraint, attr, None)
                        if target is not None:
                            controls.add(target.name)
    prefix = f'WM_{job_id}_{actor_id}_'
    candidates = set(core)
    for obj in scene.objects:
        if owned(obj):
            candidates.add(obj)
        elif (obj.name in controls and obj.name.startswith(prefix) and obj.type == 'EMPTY'
              and obj.hide_render and collection is not None and obj.name in collection.objects
              and not obj.get('wm_job_id') and not obj.get('wm_actor_id')):
            candidates.add(obj)
    protected = [{'object': name, 'reason': 'ownership_mismatch'} for name in names
                 if (obj := bpy.data.objects.get(name)) is not None and not owned(obj)]
    removal = set()
    for obj in candidates:
        reason = None
        if obj.name not in scene.objects:
            reason = 'outside_current_scene'
        elif obj.name in other_names:
            reason = 'another_actor_registry_reference'
        elif obj.type in ('CAMERA', 'LIGHT'):
            reason = 'camera_or_light_preserved'
        elif _is_declared_prop(obj):
            reason = 'declared_prop_preserved'
        elif any(obj.name in other.objects for other in bpy.data.scenes if other != scene):
            reason = 'shared_with_another_scene'
        if reason:
            protected.append({'object': obj.name, 'reason': reason})
        else:
            removal.add(obj)
    # A control or rig borrowed by a surviving actor must remain live.
    queue = [obj for obj in scene.objects if obj.name in other_names]
    visited = set()
    while queue:
        obj = queue.pop()
        if obj in visited:
            continue
        visited.add(obj)
        references = {obj.parent} if obj.parent else set()
        constraints = list(obj.constraints)
        if obj.type == 'ARMATURE':
            constraints.extend(c for bone in obj.pose.bones for c in bone.constraints)
        for constraint in constraints:
            references.update(target for attr in ('target', 'pole_target')
                              if (target := getattr(constraint, attr, None)) is not None)
        for modifier in obj.modifiers:
            target = getattr(modifier, 'object', None)
            if target is not None:
                references.add(target)
        if obj.animation_data:
            for curve in obj.animation_data.drivers:
                references.update(target.id for variable in curve.driver.variables for target in variable.targets
                                  if isinstance(target.id, bpy.types.Object))
        for target in references & removal:
            removal.remove(target)
            protected.append({'object': target.name, 'reason': 'borrowed_by_surviving_actor', 'borrower': obj.name})
        queue.extend(target for target in references if target not in visited)
    bpy.context.view_layer.update()
    detached = []
    from mathutils import Matrix
    for obj in list(bpy.data.objects):
        if obj not in removal and obj.parent in removal:
            world = obj.matrix_world.copy()
            old_parent = obj.parent.name
            obj.parent = None
            obj.parent_type = 'OBJECT'
            obj.parent_bone = ''
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_world = world
            detached.append({'object': obj.name, 'old_parent': old_parent,
                             'world_matrix': matrix_values(world, digits=8),
                             'frame': scene.frame_current})
            if obj.animation_data and (obj.animation_data.action or obj.animation_data.drivers):
                warnings.append(obj.name + ': own animation retained; attachment world preservation applies to current frame, not the removed actor future trajectory.')
    removed_names = sorted(obj.name for obj in removal)
    root_removed = root in removal
    retained_owned_root = root is not None and not root_removed and owned(root) and root.name not in other_names
    for obj in removal:
        bpy.data.objects.remove(obj, do_unlink=True)
    registry.pop(actor_id)
    _write_registry(scene, registry)
    timeline_removed = root_removed or root is None
    if retained_owned_root:
        for key in ('wm_motion_timeline_v1', 'wm_actor_controls_v1'):
            if key in root:
                del root[key]
        timeline_removed = True
    removed_collections = []
    if collection is not None and collection != scene.collection and not collection.objects and not collection.children:
        if collection.name == f'WM_{job_id}_{actor_id}' and collection.users <= 1:
            removed_collections.append(collection.name)
            bpy.data.collections.remove(collection)
    bpy.context.view_layer.update()
    blocking = [item for item in protected if item['object'] in names and item['reason'] not in ('declared_prop_preserved', 'camera_or_light_preserved')]
    return {'ok': not blocking, 'job_id': job_id, 'actor_id': actor_id, 'registry_removed': True,
            'removed_objects': removed_names, 'removed_collections': removed_collections,
            'missing_recorded_objects': [name for name in names if name not in removed_names and bpy.data.objects.get(name) is None],
            'retained_objects': protected, 'detached_objects': detached,
            'timeline_bank_removed': timeline_removed, 'removed_timeline_events': events_count,
            'remaining_registered_actor_ids': sorted(registry), 'warnings': warnings,
            'shared_datablocks_purged': False, 'status': 'removed' if not blocking else 'registry_removed_some_shared_objects_retained'}


def scene_actor_facts(job_id, allow_empty=False):
    """Actual render-enabled bodies; unregistered rendered rigs are errors.

    Hidden import sources are excluded.  This is identity/binding evidence,
    never an assertion about requested semantics, actor counts or naturalness.
    """
    from agent_scene import validate_job, _render_enabled_objects
    validate_job(job_id)
    bpy = _bpy()
    scene = bpy.context.scene
    enabled = _render_enabled_objects(scene)
    visible_meshes = [obj for obj in scene.objects if obj.type == 'MESH' and
                      obj.as_pointer() in enabled and not obj.hide_render]
    visible_mesh_names = {mesh.name for mesh in visible_meshes}
    rendered_rigs = {}
    attachment_meshes = []
    for mesh in visible_meshes:
        if _is_declared_prop(mesh):
            attachment_meshes.append(mesh.name)
            continue
        rigs = {modifier.object for modifier in mesh.modifiers
                if modifier.type == 'ARMATURE' and modifier.show_render and modifier.object is not None}
        ancestor = mesh.parent
        while ancestor is not None:
            if ancestor.type == 'ARMATURE':
                rigs.add(ancestor)
            ancestor = ancestor.parent
        for rig in rigs:
            rendered_rigs.setdefault(rig.name, []).append(mesh.name)
    actors, errors = [], []
    try:
        registry = _registry(scene)
    except Exception as exc:
        registry = {}
        errors.append(f'人物登记损坏：{exc}')
    if allow_empty and not registry and not rendered_rigs and not errors:
        return {'version': 1, 'job_id': job_id, 'actors': [], 'errors': [],
                'rendered_actor_count': 0, 'actual_rendered_rigs': [], 'identity_verified': True,
                'status': 'not_applicable', 'scope': 'No registered or rendered actor rig in this object-only scene.'}
    if scene.get(JOB_KEY) != job_id:
        errors.append('当前场景未登记为本任务的标准人物场景')
    registered_rigs = {}
    for actor_id, entry in registry.items():
        try:
            item = actor_get(job_id, actor_id)
            actual = sorted(set(item['mesh_objects']) & visible_mesh_names)
            item.update(rendered_mesh_objects=actual, rendered=bool(actual), identity_verified=True)
            registered_rigs[item['armature_object']] = actor_id
            unexpected = set(rendered_rigs.get(item['armature_object'], [])) - set(item['mesh_objects'])
            if unexpected:
                errors.append(f'{actor_id} 骨架驱动未登记身体：{sorted(unexpected)}')
            actors.append(item)
        except Exception as exc:
            errors.append(f'{actor_id} 人物身份或绑定失败：{exc}')
            actors.append({'actor_id': actor_id, 'identity_verified': False,
                           'armature_object': entry.get('armature_object'), 'error': str(exc)})
    for name, meshes in rendered_rigs.items():
        if name not in registered_rigs:
            errors.append(f'可渲染人物骨架未通过人物身份登记：{name}；身体 {sorted(set(meshes))}')
    rendered_count = sum(bool(actor.get('rendered')) for actor in actors)
    if not actors:
        errors.append('当前场景没有登记通过身份验证的标准机器人')
    return {'version': 1, 'job_id': job_id, 'actors': actors, 'errors': errors,
            'rendered_actor_count': rendered_count,
            'visibility_review_required': any(not actor.get('rendered') for actor in actors),
            'actual_rendered_rigs': [{'armature_object': name, 'mesh_objects': sorted(set(meshes))}
                                     for name, meshes in sorted(rendered_rigs.items())],
            'declared_prop_meshes': sorted(attachment_meshes),
            'identity_verified': not errors,
            'scope': 'Actual undeformed registered-character topology/weights/rest rig and live modifier binding only.'}


def resolve_actor(job_id, actor_id):
    item = actor_get(job_id, actor_id)
    bpy = _bpy()
    return {'info': item, 'root': bpy.data.objects[item['root_object']],
            'armature': bpy.data.objects[item['armature_object']],
            'meshes': [bpy.data.objects[name] for name in item['mesh_objects']],
            'collection': bpy.data.collections.get(item['collection'])}


def _register(job_id, actor_id, character_id, armature, meshes, root, collection, adopted=False):
    scene = _job_scene(job_id, allow_claim=True)
    registry = _registry(scene)
    skeleton, binding = _identity_check(armature, meshes, _catalog_character(character_id))
    for other_id, other in registry.items():
        if other_id != actor_id and (other['armature_object'] == armature.name or
                                    set(other['mesh_objects']) & {m.name for m in meshes} or
                                    other['root_object'] == root.name):
            raise ValueError('人物对象已属于另一个角色；不能共享可变骨架或身体')
    for obj in [root, armature, *meshes]:
        obj['wm_job_id'], obj['wm_actor_id'], obj['wm_character_id'] = job_id, actor_id, character_id
    registry[actor_id] = {'job_id': job_id, 'actor_id': actor_id, 'character_id': character_id,
                          'root_object': root.name, 'armature_object': armature.name,
                          'mesh_objects': [m.name for m in meshes], 'collection': collection.name,
                          'skeleton_signature': skeleton['signature'], 'binding_signature': binding['signature'],
                          'adopted': adopted, 'identity_version': 1}
    _write_registry(scene, registry)
    return actor_get(job_id, actor_id)


def actor_adopt(job_id, actor_id, armature_name, root_name=None, character_id=None):
    """Register a real catalog character without replacing its animation/rig."""
    _validate_actor(actor_id)
    scene = _job_scene(job_id, allow_claim=True)
    if actor_id in _registry(scene):
        existing = actor_get(job_id, actor_id)
        if existing['armature_object'] != armature_name:
            raise ValueError('该 actor_id 已登记另一骨架')
        if character_id is not None and existing['character_id'] != character_id:
            raise ValueError('该角色已使用另一人物资产')
        return existing
    character_id = default_character_id() if character_id is None else character_id
    bpy = _bpy()
    armature = bpy.data.objects.get(armature_name)
    if armature is None or armature.name not in scene.objects or armature.type != 'ARMATURE':
        raise ValueError('待登记骨架不存在于当前场景')
    meshes = [obj for obj in scene.objects if obj.type == 'MESH' and not _is_declared_prop(obj) and
              any(m.type == 'ARMATURE' and m.object == armature for m in obj.modifiers)]
    _identity_check(armature, meshes, _catalog_character(character_id))
    root = bpy.data.objects.get(root_name) if root_name else armature.parent
    if root is not None and (root.name not in scene.objects or armature.parent != root):
        raise ValueError('待登记 root 必须是当前骨架的直接父对象')
    if root is None:
        from mathutils import Matrix
        root = bpy.data.objects.new(f'WM_{job_id}_{actor_id}_Root', None)
        scene.collection.objects.link(root)
        armature.parent = root
        armature.matrix_parent_inverse = Matrix.Identity(4)
    collection = next(iter(armature.users_collection), scene.collection)
    return _register(job_id, actor_id, character_id, armature, meshes, root, collection, adopted=True)


def _finite_sequence(value, length, name):
    if not isinstance(value, (list, tuple)) or len(value) != length or any(
            isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in value):
        raise ValueError(f'{name} 必须是 {length} 个有限数字')
    return tuple(float(v) for v in value)


def actor_ensure(job_id, actor_id, character_id=None, position=(0, 0, 0), yaw=0,
                 color=DEFAULT_ACTOR_COLOR):
    """Create a matte white actor; explicit None retains source materials.

    Repeat calls preserve an existing actor's appearance, position and motion.
    """
    _validate_actor(actor_id)
    position = _finite_sequence(position, 3, 'position')
    if isinstance(yaw, bool) or not isinstance(yaw, (float, int)) or not math.isfinite(yaw):
        raise ValueError('yaw 必须是弧度有限数值')
    if color is not None:
        color = _finite_sequence(color, 4, 'color RGBA')
        if any(v < 0 or v > 1 for v in color):
            raise ValueError('color RGBA 分量必须在 0 到 1')
    scene = _job_scene(job_id, allow_claim=True)
    registry = _registry(scene)
    if actor_id in registry:
        result = actor_get(job_id, actor_id)
        if character_id is not None and result['character_id'] != character_id:
            raise ValueError('该角色已使用另一人物资产')
        return result
    character_id = default_character_id() if character_id is None else character_id
    bpy = _bpy()
    character = _catalog_character(character_id)
    from mathutils import Matrix
    prefix = f'WM_{job_id}_{actor_id}'
    collection = bpy.data.collections.new(prefix)
    scene.collection.children.link(collection)
    root = bpy.data.objects.new(prefix + '_Root', None)
    collection.objects.link(root)
    root.location = position
    root.rotation_euler.z = float(yaw)
    before_objects = set(bpy.data.objects)
    fps, fps_base, frame = scene.render.fps, scene.render.fps_base, scene.frame_current
    try:
        with bpy.data.libraries.load(str(PROJECT / character['cache_asset']), link=False) as (source, target):
            target.objects = source.objects
        loaded = [obj for obj in target.objects if obj is not None]
        for obj in loaded:
            collection.objects.link(obj)
        rigs = [obj for obj in loaded if obj.type == 'ARMATURE']
        if len(rigs) != 1:
            raise ValueError('人物缓存必须恰有一个实际骨架')
        armature = rigs[0]
        meshes = [obj for obj in loaded if obj.type == 'MESH']
        armature.name = prefix + '_Rig'
        armature.data = armature.data.copy()
        armature.animation_data_clear()
        armature.parent = root
        armature.matrix_parent_inverse = Matrix.Identity(4)
        # The cache retains an actual Idle standing pose without an Action.
        # Clearing pose channels would expose asset rest T-pose for free FK.
        material_copies = {}
        for index, mesh in enumerate(meshes):
            mesh.name = prefix + f'_Body_{index}'
            mesh.data = mesh.data.copy()
            if color is None:
                for slot in mesh.material_slots:
                    if slot.material is not None:
                        original = slot.material
                        key = original.as_pointer()
                        if key not in material_copies:
                            material_copies[key] = original.copy()
                        slot.material = material_copies[key]
            mesh.animation_data_clear()
            for modifier in mesh.modifiers:
                if modifier.type == 'ARMATURE':
                    modifier.object = armature
            # Preserve cached mesh parent-relative inverse/basis: imported
            # FBX parenting already compensates the .01 armature scale.
            if mesh.parent is not armature:
                world = mesh.matrix_world.copy()
                mesh.parent = root
                mesh.matrix_parent_inverse = Matrix.Identity(4)
                mesh.matrix_basis = world
            mesh.hide_render = False
            mesh.hide_viewport = False
        if color is not None:
            material = bpy.data.materials.new(prefix + '_Material')
            material.use_nodes = True
            material.diffuse_color = color
            bsdf = material.node_tree.nodes.get('Principled BSDF')
            if bsdf:
                bsdf.inputs['Base Color'].default_value = color
                bsdf.inputs['Roughness'].default_value = .8
            for mesh in meshes:
                mesh.data.materials.clear()
                mesh.data.materials.append(material)
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        return _register(job_id, actor_id, character_id, armature, meshes, root, collection)
    except Exception:
        for obj in set(bpy.data.objects) - before_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.objects.remove(root, do_unlink=True)
        bpy.data.collections.remove(collection)
        raise
    finally:
        scene.render.fps, scene.render.fps_base = fps, fps_base
        scene.frame_set(frame)
