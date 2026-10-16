"""Local, open-text motion discovery; the index is not a prompt whitelist.

Only measured import facts become compatibility claims.  Filename/alias matches
are candidates for the director, and do not certify contacts or naturalness.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct

PROJECT = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT / 'knowledge/motions/catalog.json'

# Curated search terms describe the existing filenames, not new implemented
# controls.  In particular, jumping is not advertised as window vaulting.
ALIASES = {
    'Idle': ['待机', '站着', '站立', 'idle'],
    'Walk': ['走路', '步行', '行走', 'walking', 'walk'],
    'Run': ['跑步', '奔跑', '跑向', 'running', 'run'],
    'Sprint': ['冲刺', '疾跑', 'sprint'],
    'Jogging': ['慢跑', 'jogging', 'jog'],
    'Strafe': ['侧移', '横移', 'strafe'],
    'Jump': ['跳跃', '起跳', 'jump'],
    'Falling': ['下落', '坠落', 'falling'],
    'Mma Idle': ['格斗待机', '格斗站姿', '警戒', 'guard'],
    'Hook': ['勾拳', '摆拳', 'hook'],
    'Lead Jab': ['前手刺拳', '刺拳', 'lead jab'],
    'Jab Cross': ['刺拳直拳组合', '一二连拳', 'jab cross'],
    'Punching': ['出拳', '拳击', '打斗', '对打', '打一拳', '揍一拳', 'punch', 'punching'],
    'Boxing': ['拳击组合', '拳击', '打斗', '搏斗', '对打', '格斗', 'boxing', 'fight'],
    'Mma Kick': ['踢击', '踢腿', 'kick'],
    'Kicking': ['踢击', '踢腿', 'kicking'],
    'Side Kick': ['侧踢', 'side kick'],
    'Drop Kick': ['飞踢', '双脚飞踢', 'drop kick'],
    'Flip Kick': ['翻身踢', '翻转踢', 'flip kick'],
    'Knee Jab': ['膝击', 'knee jab'],
    'Headbutt': ['头槌', '头撞', 'headbutt'],
    'Throw': ['投掷', 'throw'],
    'Throw In': ['掷球', 'throw in'],
    'Head Hit': ['头部受击', '打中头', '头部反应', 'head hit'],
    'Hit To Body': ['身体受击', '躯干受击', '打中身体', 'body hit'],
    'Hit On Legs': ['腿部受击', 'hit on legs'],
    'Side Hit': ['侧面受击', 'side hit'],
    'Getting Hit': ['受击', '挨打', 'getting hit'],
    'Big Rib Hit': ['肋部重击', 'big rib hit'],
    'Rib Hit': ['肋部受击', 'rib hit'],
    'Kidney Hit': ['腰部受击', 'kidney hit'],
    'Stunned': ['眩晕', '晕眩', 'stunned'],
    'Hit By Car': ['被车撞', 'hit by car'],
    'Death': ['死亡', '倒地', 'death'],
    'Dying': ['濒死', '倒地', 'dying'],
    'Blocking': ['格挡', '防守', 'blocking'],
    'Body Block': ['躯干格挡', 'body block'],
    'Right Block': ['右侧格挡', 'right block'],
    'Left Block': ['左侧格挡', 'left block'],
    'Center Block': ['正面格挡', 'center block'],
    'Inward Block': ['向内格挡', 'inward block'],
    'Outward Block': ['向外格挡', 'outward block'],
    'Bayonet Stab': ['刺刀突刺', 'bayonet stab'],
    'Swing To Land': ['挥击落地', 'swing to land'],
    'Slash Advance': ['前进斩击', 'slash advance'],
    'Thrust Slash': ['突刺斩击', 'thrust slash'],
    'Upward Thrust': ['向上突刺', 'upward thrust'],
    'Run With Sword': ['持剑跑步', 'run with sword'],
    'Run And Swing': ['跑步挥击', 'run and swing'],
    'Swing Dancing': ['摇摆舞', 'swing dancing'],
    'Aiming Gun': ['持枪瞄准', 'aiming gun'],
    'Aiming': ['瞄准', 'aiming'],
    'Pistol Aim': ['手枪瞄准', 'pistol aim'],
    'Shooting': ['射击', 'shooting'],
    'Fire Rifle': ['步枪射击', 'fire rifle'],
    'Reload': ['换弹', '装弹', 'reload'],
    'Rifle Run': ['持步枪跑步', 'rifle run'],
    'Pistol': ['手枪', 'pistol'],
    'Spell Cast': ['施法', 'spell cast'],
    'Spell Casting': ['施法', 'spell casting'],
    'Casting Spell': ['施法', 'casting spell'],
    'Magic Heal': ['魔法治疗', 'magic heal'],
    'Fishing Cast': ['抛鱼竿', '钓鱼抛投', 'fishing cast'],
}

# These terms improve discovery, not execution eligibility or motion quality.
# In particular a get-up filename does not imply compatibility with Beta.
STAGING_TERMS = (
    (('walk',), ['走路', '行走', '步行', 'walk', 'walking'], 'locomotion'),
    (('run',), ['跑步', '奔跑', '跑向', 'run', 'running'], 'locomotion'),
    (('sprint',), ['冲刺', '疾跑', 'sprint'], 'locomotion'),
    (('idle',), ['待机', '站立', 'idle'], 'idle'),
    (('boxing',), ['拳击', '打斗', '空击', '出拳', '揍一拳', 'boxing'], 'unarmed'),
    (('fightingidle',), ['格斗待机', '防守站姿', '格斗站姿', 'fighting idle'], 'unarmed'),
    (('frontkick', 'roundhousekick'), ['踢腿', '踢击', 'kick'], 'unarmed'),
    (('knockout',), ['击倒', '倒地', '受击倒地', 'knockout'], 'reaction'),
    (('getup',), ['起身', '起立', '撑地起身', '倒地起身', 'get up'], 'recovery'),
    (('backward',), ['后退', '倒退', '向后跑', 'backward'], 'backward'),
    (('reload',), ['换弹', '装弹', 'reload'], 'weapon'),
    (('look',), ['观察', '看向四周', 'look'], 'gesture'),
    (('chat', 'conversation'), ['交谈', '聊天', '说话'], 'gesture'),
    (('point',), ['指向', '指着', 'pointing'], 'gesture'),
    (('listeningtomusic',), ['听音乐'], 'gesture'),
    (('arguing',), ['争吵'], 'gesture'),
    (('leaningonwall',), ['靠墙', '倚墙'], 'gesture'),
    (('gesture', 'guesture'), ['手势', '讲解手势'], 'gesture'),
    (('fingerwave',), ['招手', '挥手'], 'gesture'),
    (('jump',), ['跳跃', '起跳'], 'jump'),
    (('injured',), ['受伤行走', '受伤跑步'], 'injured'),
)
WEAPON_WORDS = ('sword', 'knife', 'rifle', 'riffle', 'pistol', 'shotgun', 'gun', 'grenade', 'rpg', 'bowarrow', 'execution')
WEAPON_QUERY_WORDS = ('剑', '刀', '枪', '弓', '手雷', '武器', *WEAPON_WORDS)
CORE_GLB = 'assets/incoming/combat/quaternius/ual1_standard/extracted/Universal Animation Library[Standard]/Unreal-Godot/UAL1_Standard_RM.glb'
CORE_CLIPS = {
    'Idle_Loop': ['待机', '站立', 'idle'],
    'Walk_Loop': ['走路', '普通走路', '步行', '行走', 'walk'],
    'Sprint_Loop': ['跑步', '奔跑', '冲刺', '疾跑', 'sprint'],
}


def _core_entries(previous):
    path = PROJECT / CORE_GLB
    actions = {}
    if path.is_file():
        with path.open('rb') as stream:
            stream.seek(12)
            length, _ = struct.unpack('<II', stream.read(8))
            document = json.loads(stream.read(length))
        for animation in document.get('animations', []):
            inputs = [document['accessors'][sampler['input']] for sampler in animation['samplers']]
            actions[animation['name']] = (min(item['min'][0] for item in inputs), max(item['max'][0] for item in inputs))
    result = []
    for clip, aliases in CORE_CLIPS.items():
        identifier = 'quaternius.'+_slug(clip)
        if not path.is_file():
            if identifier in previous:
                result.append(previous[identifier])
            continue
        if clip not in actions:
            raise ValueError('Named core animation is missing from actual GLB: '+clip)
        low, high = actions[clip]
        result.append({'motion_id': identifier, 'name': clip, 'clip': clip, 'asset': CORE_GLB,
            'asset_sha256': _sha(path), 'provider': 'quaternius_glb', 'category': 'core_locomotion',
            'collection': 'main', 'entry_kind': 'motion', 'description': clip+'（已枚举的CC0动画；风格和过渡仍需实际检查）',
            'aliases': aliases+[clip], 'registered_ids': [], 'source_fps': 30.,
            'source_fps_basis': 'gltf_animation_seconds_sampled_at_30fps', 'source_frame_range': [low*30, high*30],
            'duration_s': high-low, 'scan_status': 'actual_glb_animation_name_enumerated',
            'compatibility': {'method': 'quaternius_to_beta_rest_space', 'direct_action': False},
            'semantic_status': 'actual_action_name_candidate', 'quality_status': 'not_visually_verified',
            'loop_allowed': True, 'loop_validation': 'source_loop_name_not_phase_validation',
            'side': None, 'phases': None, 'root_metadata': None,
            'license': {'id': 'CC0-1.0', 'status': 'bundled_original_license',
                        'license_file': 'bundled_assets/animations/quaternius/UAL1_License.txt'},
            'source_provenance': {'source_url': 'https://quaternius.itch.io/universal-animation-library',
                                  'basis': 'actual_glb_animations_and_original_pack_license',
                                  'capture_method': 'not_claimed_mocap'}})
    return result


def _previous_entries():
    if not CATALOG_PATH.is_file():
        return {}
    return {entry['motion_id']: entry for entry in json.loads(CATALOG_PATH.read_text(encoding='utf-8'))['entries']}


def _staging_hints(path):
    text = path.stem.casefold()
    aliases, hints = [path.stem, path.name], []
    for tokens, terms, hint in STAGING_TERMS:
        if any(token in text for token in tokens):
            aliases.extend(terms); hints.append(hint)
    if 'shadowboxing' in text:
        aliases.extend(['影子拳击', '假想对手', '空击训练', 'shadow boxing'])
    if 'slowwalk' in text or text in ('02-walkforward', '10-walkcycle_01_mixamo_769'):
        aliases.extend(['普通走路', '自然行走', '慢走'])
    if any(word in text for word in WEAPON_WORDS):
        hints.append('weapon')
    if any(word in text for word in ('taunt', 'hero', 'execution', 'gunfu')):
        hints.append('performed_or_stylized')
    if 'wind' in text:
        aliases.extend(['迎风走路', '顶风行走']); hints.append('performed_or_stylized')
    return list(dict.fromkeys(aliases)), list(dict.fromkeys(hints))


def _staging_entries(previous):
    staging = PROJECT / 'rokoko_staging'
    manifest = staging / 'manifest.json'
    records = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else []
    archives = {}
    for record in records:
        archives.setdefault(record['file'], set()).add(record['zip'])
    entries = []
    for path in sorted(staging.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.fbx', '.glb'):
            continue
        category = path.parent.name
        identifier = ('rokoko.' if category == 'mixamo' else 'staging.')+category+'.'+_slug(path.stem)
        aliases, hints = _staging_hints(path)
        kind = 'motion_file_candidate'
        provider = 'rokoko_mixamo_fbx' if category == 'mixamo' else 'unregistered_local_source'
        reason = 'Named Mixamo mapping candidate; import, timebase, neutral frames and Beta transfer need actual validation.'
        if category == 'ual':
            kind, provider = 'collection_candidate', 'unregistered_local_collection'
            aliases.extend(['动作合集', '动画合集', 'Universal Animation Library', path.stem.split('_')[0]])
            reason = 'Animation collection file; enumerate named Actions and register each usable clip before sampling.'
        elif 'mannequin' in path.stem.casefold():
            kind, provider = 'character_asset', 'character_asset'
            aliases.extend(['人物资产', '女性模型', '人体模型'])
            reason = 'Character/model asset, not a registered motion clip; animation content has not been established.'
        elif category != 'mixamo':
            reason = 'Source skeleton and Actions have not been mapped to the canonical Beta; needs import inspection and an adapter.'
        if kind in ('collection_candidate', 'character_asset'):
            identifier += '.'+path.suffix.lower().lstrip('.')
        relative = str(path.relative_to(PROJECT))
        entry = {'motion_id': identifier, 'name': path.stem, 'asset': relative,
                 'asset_sha256': _sha(path), 'provider': provider, 'category': category,
                 'collection': 'rokoko_staging', 'entry_kind': kind,
                 'description': path.stem+'（本地文件名候选；实际表演、动作数量及适用范围待核查）',
                 'aliases': list(dict.fromkeys(aliases)), 'registered_ids': [],
                 'semantic_status': 'filename_inference_only', 'style_hints': hints,
                 'style_hint_basis': 'filename_and_archive_names_not_visual_review',
                 'source_provenance': {'manifest': 'rokoko_staging/manifest.json',
                                       'archives': sorted(archives.get(path.name, [])),
                                       'basis': 'local_extraction_manifest', 'source_url': None,
                                       'capture_method': 'not_documented_in_local_manifest'},
                 'source_fps': None, 'source_frame_range': None, 'duration_s': None,
                 'skeleton': None, 'compatibility': None, 'source_sampling_status': None,
                 'quality_status': 'not_visually_verified', 'visual_status': 'not_reviewed',
                 'side': None, 'phases': None, 'root_metadata': None, 'support_metadata': None,
                 'recovery_metadata': None, 'loop_allowed': False, 'loop_validation': 'not_measured',
                 'use_status': 'candidate_needs_validation',
                 'execution': {'available': False, 'function': 'agent_motion.prepare_motion / agent_motion.sample_motion'
                               if provider == 'rokoko_mixamo_fbx' else None,
                               'motion_id': identifier, 'reason': reason},
                 'license': {'id': 'local_source_terms_unconfirmed',
                             'status': 'consult_original_pack_terms_before_external_asset_redistribution'}}
        old = previous.get(identifier, {})
        if old.get('asset_sha256') == entry['asset_sha256']:
            # Keep measured annotations and validated adapters on a normal index
            # refresh. Discovery must not erase work already done on a source.
            for key in ('entry_kind', 'source_fps', 'source_frame_range', 'duration_s', 'key_timebase_fps',
                        'source_action', 'source_slot', 'skeleton', 'compatibility',
                        'source_sampling_status', 'semantic_status', 'semantic_annotations',
                        'visual_status', 'quality_status', 'side', 'phases', 'root_metadata',
                        'support_metadata', 'recovery_metadata', 'use_status', 'execution',
                        'suggested_source_window_seconds', 'valid_motion_window_seconds',
                        'validated_playback_windows', 'requires_source_window',
                        'calibration_metadata', 'validation_scope'):
                if key in old:
                    entry[key] = old[key]
        entries.append(entry)
    return entries


def _describe_entry(entry):
    entry.setdefault('entry_kind', 'motion')
    entry.setdefault('collection', 'main')
    entry.setdefault('semantic_status', 'filename_and_legacy_annotations')
    entry.setdefault('visual_status', 'not_reviewed')
    entry.setdefault('style_hints', ['weapon'] if any(word in entry['name'].casefold() for word in WEAPON_WORDS) else [])
    if entry['collection'] == 'main':
        available = bool((entry.get('compatibility') or {}).get('direct_action') or
                         entry['provider'] == 'quaternius_glb')
        entry.setdefault('use_status', 'compatible_source' if available else 'candidate_needs_validation')
        entry.setdefault('execution', {'available': available,
                         'function': 'agent_motion.prepare_motion / agent_motion.sample_motion',
                         'motion_id': entry['motion_id'],
                         'reason': 'Rig/source sampling compatibility; not a naturalness or contact certification.'
                         if available else 'Source compatibility has not been measured.'})
        entry.setdefault('source_provenance', {'basis': 'existing_local_motion_registration',
                         'asset': entry['asset'], 'capture_method': 'not_uniformly_documented'})
    fields = ('source_fps', 'duration_s', 'side', 'phases', 'root_metadata', 'support_metadata', 'recovery_metadata')
    entry['metadata_gaps'] = [field for field in fields if entry.get(field) is None]
    entry['naturalness_verified'] = False
    return entry


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.casefold()).strip('_')


def asset_path(relative):
    """Resolve an indexed source without reading outside this project."""
    path = (PROJECT / relative).resolve()
    if not path.is_relative_to(PROJECT.resolve()) or not path.is_file():
        raise ValueError('Motion source is missing or outside the project: ' + str(relative))
    return path


def build_catalog(scan_report=None, validation_report=None):
    """Build all local entries, conservatively merging measured scan facts."""
    previous = _previous_entries()
    report = scan_report or {}
    if isinstance(report, (str, Path)):
        scan_path = Path(report)
        report = json.loads(scan_path.read_text(encoding='utf-8'))
        report['report_path'] = str(scan_path)
    scanned = {item.get('asset', item.get('path')): item for item in report.get('assets', [])}
    duel = json.loads((PROJECT / 'knowledge/motions/duel.json').read_text(encoding='utf-8'))['motions']
    entries = []
    for path in sorted((PROJECT / 'mixamo_fbx').rglob('*.fbx')):
        relative = str(path.relative_to(PROJECT))
        fact = scanned.get(relative, {})
        asset_hash = _sha(path)
        stale_scan = bool(fact and fact.get('path_sha256') != asset_hash)
        if stale_scan:
            fact = {'status': 'scan_stale_source_changed'}
        registered = [(key, value) for key, value in duel.items() if value.get('asset') == relative]
        annotations = registered[0][1] if registered else {}
        source_fps = fact.get('source_fps')
        actions = fact.get('actions', [])
        frame_range = actions[0].get('frame_range') if len(actions) == 1 else None
        duration = ((frame_range[1] - frame_range[0]) / source_fps
                    if frame_range and isinstance(source_fps, (float, int)) and source_fps > 0 else None)
        entries.append({
            'motion_id': 'mixamo.' + _slug(path.parent.name) + '.' + _slug(path.stem),
            'name': path.stem, 'asset': relative, 'asset_sha256': asset_hash,
            'provider': 'fbx', 'category': path.parent.name,
            'description': annotations.get('description', path.stem + '（素材名称；动作细节仍需预览核对）'),
            'aliases': list(dict.fromkeys(ALIASES.get(path.stem, []) + [path.stem])),
            'registered_ids': [key for key, _ in registered],
            'registration_scope': 'Existing duel annotations describe that executor; source sampler transfers poses, while contact IK and smoothing must be requested separately' if registered else None,
            'source_fps': source_fps, 'source_frame_range': frame_range, 'duration_s': duration,
            'actions': actions, 'skeleton': {key: fact['skeleton'][key] for key in ('signature', 'bone_count')}
                                          if fact.get('skeleton') else None,
            'object_transform': fact.get('object_transform'), 'compatibility': fact.get('compatibility'),
            'scan_status': fact.get('status', 'not_scanned'),
            'scan_warning': 'Source hash differs from measured scan; rescan before claiming compatibility' if stale_scan else None,
            'quality_status': 'registered_bounded_source' if registered else 'not_visually_verified',
            # A duel's single retreat-step policy is specific to that event;
            # it must not turn the underlying full Walk asset into a one-shot.
            'loop_allowed': path.stem in ('Idle', 'Walk', 'Run', 'Sprint', 'Jogging', 'Mma Idle') or annotations.get('loop_allowed', False),
            'loop_validation': 'annotation_only',
            'source_window_frames': [annotations['source_start'], annotations['source_end']]
                                    if 'source_start' in annotations and 'source_end' in annotations else None,
            'phases': annotations.get('phases'), 'source_peak': annotations.get('source_peak'),
            'side': annotations.get('side'), 'root_metadata': fact.get('root_metadata'),
            'license': {'id': 'Mixamo-source-terms', 'status': 'existing_asset_source_terms_not_CC0',
                        'redistribution_review': 'required_before_external_redistribution'},
        })
        old = previous.get(entries[-1]['motion_id'], {})
        if old.get('asset_sha256') == asset_hash:
            if not fact:
                for field in ('source_fps', 'source_frame_range', 'duration_s', 'actions', 'skeleton',
                              'object_transform', 'compatibility', 'scan_status', 'root_metadata'):
                    if field in old:
                        entries[-1][field] = old[field]
            for field in ('semantic_status', 'semantic_annotations', 'visual_status', 'quality_status',
                          'support_metadata', 'recovery_metadata'):
                if field in old:
                    entries[-1][field] = old[field]
            if validation_report is None and entries[-1]['scan_status'] != 'scan_stale_source_changed':
                for field in ('source_sampling_status', 'source_sampling_report', 'key_timebase_fps',
                              'source_action', 'source_slot'):
                    if field in old:
                        entries[-1][field] = old[field]
    for key, definition in duel.items():
        if not definition.get('clip') or not definition.get('asset'):
            continue
        path = PROJECT / definition['asset']
        if not path.is_file():
            identifier = 'quaternius.'+_slug(definition['clip'])
            if identifier in previous:
                entries.append(previous[identifier])
            continue
        aliases = {'jab': ['刺拳', '左手直拳', '前手拳', 'jab', 'left punch', 'punch'],
                   'cross': ['右手直拳', '后手直拳', '重直拳', '重拳', '直拳', 'cross', 'right punch', 'punch'],
                   'fall_back': ['仰面倒地', '向后倒地', '被击倒', 'fall back']}[key]
        entries.append({
            'motion_id': 'quaternius.' + _slug(definition['clip']),
            'name': definition['clip'], 'asset': definition['asset'], 'asset_sha256': _sha(path),
            'provider': 'quaternius_glb', 'clip': definition['clip'], 'category': definition['category'],
            'description': definition['description'], 'aliases': aliases + [definition['clip']],
            'registered_ids': [key], 'source_fps': 30.0,
            'source_fps_basis': 'gltf_animation_seconds_sampled_at_30fps',
            'source_frame_range': None, 'duration_s': None,
            'scan_status': 'registered_specific_retarget', 'quality_status': 'registered_bounded_source',
            'compatibility': {'method': 'quaternius_to_beta_rest_space', 'direct_action': False},
            'loop_allowed': False, 'loop_validation': 'one_shot_annotation',
            'phases': definition.get('phases'), 'source_peak': definition.get('source_peak'),
            'side': definition.get('side'), 'license': {'id': definition['license'], 'status': 'existing_registration'},
        })
        old = previous.get(entries[-1]['motion_id'], {})
        if old.get('asset_sha256') == entries[-1]['asset_sha256'] and validation_report is None:
            for field in ('source_fps', 'source_frame_range', 'duration_s', 'key_timebase_fps',
                          'source_action', 'source_slot', 'compatibility', 'source_sampling_status',
                          'source_sampling_report', 'root_metadata', 'semantic_status',
                          'semantic_annotations', 'visual_status', 'support_metadata', 'recovery_metadata'):
                if field in old:
                    entries[-1][field] = old[field]
    if validation_report is not None:
        validation_path = Path(validation_report)
        validation = json.loads(validation_path.read_text(encoding='utf-8'))
        verified = {check['motion_id']: check for check in validation.get('checks', [])
                    if check.get('passed') and 'source_info' in check}
        for entry in entries:
            check = verified.get(entry['motion_id'])
            if not check:
                continue
            measured = check['source_info']
            # Only measurements bound to the current source are reused.
            if measured.get('source_sha256') != entry['asset_sha256']:
                continue
            entry.update({'source_fps': measured['source_fps'],
                          'source_frame_range': measured['source_frame_range'],
                          'duration_s': measured['duration_s'],
                          'key_timebase_fps': measured['key_timebase_fps'],
                          'source_action': measured['source_action'], 'source_slot': measured['source_slot'],
                          'compatibility': {**(entry.get('compatibility') or {}), **measured['compatibility']},
                          'source_sampling_status': 'verified_bounded_scope',
                          'source_sampling_report': str(validation_path),
                          'root_metadata': {'space': measured['root_space'], 'reference': measured['root_reference'],
                                            'extraction': 'planar', 'end_displacement_m': check['root_last'],
                                            'extraction_equivalence_max_error_m': check['max_root_extraction_equivalence_error_m']},
                          })
    entries.extend(_core_entries(previous))
    # Installed private packs are optional. Reindexing a public-only checkout
    # keeps the known IDs discoverable, with live availability supplied below.
    indexed = {entry['motion_id'] for entry in entries}
    entries.extend(entry for identifier, entry in previous.items()
                   if identifier not in indexed and entry.get('collection', 'main') == 'main')
    main_count = len(entries)
    entries.extend(_staging_entries(previous))
    indexed = {entry['motion_id'] for entry in entries}
    entries.extend(entry for identifier, entry in previous.items() if identifier not in indexed)
    entries = [_describe_entry(entry) for entry in entries]
    kinds = {kind: sum(entry['entry_kind'] == kind for entry in entries)
             for kind in sorted({entry['entry_kind'] for entry in entries})}
    staging_entries = [entry for entry in entries if entry['collection'] == 'rokoko_staging']
    return {'version': 2, 'purpose': 'Natural-language candidates, never a prompt/action whitelist',
            'procedural_allowed': True, 'fbx_count': sum(e['provider'] == 'fbx' for e in entries),
            'main_motion_count': main_count, 'entry_counts': kinds,
            'legacy_main_motion_count': 66, 'bundled_core_motion_ids': [
                'quaternius.'+_slug(clip) for clip in CORE_CLIPS],
            'unique_asset_file_count': len({entry['asset'] for entry in entries}),
            'staging_file_count': len(staging_entries),
            'staging_fbx_count': sum(entry['asset'].lower().endswith('.fbx') for entry in staging_entries),
            'staging_glb_count': sum(entry['asset'].lower().endswith('.glb') for entry in staging_entries),
            'entries': entries, 'scan_report': report.get('report_path'),
            'quality_scope': 'Main registered motion entries plus local file/collection/model candidates; file count is not usable-motion count. No contact or naturalness certification.'}


def write_catalog(scan_report=None, path=CATALOG_PATH, validation_report=None):
    result = build_catalog(scan_report, validation_report)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(output)
    return result


def load_catalog(path=CATALOG_PATH):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else build_catalog()


def motion_info(motion_id, catalog=None):
    """Exact asset IDs, source paths, or already registered IDs select a source."""
    entries = (catalog or load_catalog())['entries']
    matches = [entry for entry in entries if motion_id in
               [entry['motion_id'], entry['asset'], entry['name'], *entry.get('registered_ids', [])]
               or isinstance(motion_id, str) and motion_id.casefold() == entry['name'].casefold()]
    if len(matches) != 1:
        raise ValueError('Motion identifier missing or ambiguous; query candidates first: ' + str(motion_id))
    result = dict(matches[0])
    path = asset_path(result['asset'])
    current = _sha(path)
    result['available'] = True
    result['available_scope'] = 'source_file_exists_only'
    execution = result.get('execution', {})
    result['sampling_available'] = bool(execution.get('available') and
                                         (execution.get('function') or '').startswith('agent_motion.'))
    result['source_hash_verified'] = current == result['asset_sha256']
    if not result['source_hash_verified']:
        raise ValueError('Motion source changed since catalog scan: ' + result['asset'])
    return result


def _matched(query, term):
    if not term:
        return False
    if re.fullmatch(r'[a-z0-9 _-]+', term):
        return bool(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', query))
    return term in query


def search_motions(query, limit=8, catalog=None, *, usable_only=False, entry_kind=None):
    if not isinstance(query, str):
        raise ValueError('query must be natural-language text')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError('limit must be an integer between 1 and 100')
    normalized = query.strip().casefold()
    weapon_requested = any(term in normalized for term in WEAPON_QUERY_WORDS)
    candidates = []
    if normalized:
        for entry in (catalog or load_catalog())['entries']:
            present = (PROJECT / entry['asset']).is_file()
            executable = present and bool(entry.get('execution', {}).get('available'))
            if usable_only and not executable:
                continue
            if entry_kind is not None and entry.get('entry_kind', 'motion') != entry_kind:
                continue
            terms = [term for term in entry.get('aliases', []) if _matched(normalized, term.casefold())]
            if not terms:
                continue
            # Longer exact phrases rank higher, allowing the director to see
            # specific right-hand punches ahead of general "punch" clips.
            score = max(len(term) for term in terms) + min(len(terms), 3) * .1
            if executable:
                score += .5
            if not present:
                score -= 3
            if 'weapon' in entry.get('style_hints', []) and not weapon_requested:
                score -= 2
            candidates.append({**entry, 'score': round(score, 3),
                               'available': present, 'available_scope': 'source_file_exists_only',
                               'sampling_available': executable and
                                   (entry.get('execution', {}).get('function') or '').startswith('agent_motion.'),
                               'availability_note': 'Installed source' if present else 'Optional local asset is not installed; its ID remains discoverable.',
                               'match_reasons': ['Matched candidate term: ' + term for term in terms],
                               'requires_semantic_review': True})
    candidates.sort(key=lambda item: (-item['score'], item['motion_id']))
    return {'query': query, 'candidates': candidates[:limit], 'candidate_count': len(candidates),
            'filters': {'usable_only': usable_only, 'entry_kind': entry_kind},
            'procedural_allowed': True, 'unknown_action_rejected': False,
            'scope': 'Candidates only; Hermes must preserve every original requested event and choose/create suitable motion.'}
