"""Local, open-text motion discovery; the index is not a prompt whitelist.

Only measured import facts become compatibility claims.  Filename/alias matches
are candidates for the director, and do not certify contacts or naturalness.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

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
    'Punching': ['出拳', '拳击', '打斗', '对打', '打一拳', 'punch', 'punching'],
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
    for key, definition in duel.items():
        if not definition.get('clip') or not definition.get('asset'):
            continue
        path = asset_path(definition['asset'])
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
    return {'version': 1, 'purpose': 'Natural-language candidates, never a prompt/action whitelist',
            'procedural_allowed': True, 'fbx_count': sum(e['provider'] == 'fbx' for e in entries),
            'entries': entries, 'scan_report': report.get('report_path'),
            'quality_scope': 'Source metadata and bounded source verification; no contact or naturalness certification'}


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


def search_motions(query, limit=8, catalog=None):
    if not isinstance(query, str):
        raise ValueError('query must be natural-language text')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError('limit must be an integer between 1 and 100')
    normalized = query.strip().casefold()
    candidates = []
    if normalized:
        for entry in (catalog or load_catalog())['entries']:
            terms = [term for term in entry.get('aliases', []) if _matched(normalized, term.casefold())]
            if not terms:
                continue
            # Longer exact phrases rank higher, allowing the director to see
            # specific right-hand punches ahead of general "punch" clips.
            score = max(len(term) for term in terms) + min(len(terms), 3) * .1
            candidates.append({**entry, 'score': round(score, 3),
                               'match_reasons': ['Matched candidate term: ' + term for term in terms],
                               'requires_semantic_review': True})
    candidates.sort(key=lambda item: (-item['score'], item['motion_id']))
    return {'query': query, 'candidates': candidates[:limit], 'candidate_count': len(candidates),
            'procedural_allowed': True, 'unknown_action_rejected': False,
            'scope': 'Candidates only; Hermes must preserve every original requested event and choose/create suitable motion.'}
