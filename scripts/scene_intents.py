"""Preserve object-action intents and require exact executable coverage.

Hermes interprets natural language. This module validates that interpretation;
it never guesses the user's meaning from a keyword or an animation filename.
Unsupported intents stay in the ledger so they cannot become static scenery.
"""
from collections import Counter
import re


OBJECT_TYPES = frozenset(('window', 'door', 'table', 'chair', 'box', 'other'))
SUPPORTED_ACTIONS = {
    'touch_table': 'table',
    'open_window': 'window',
    'close_window': 'window',
    'open_door': 'door',
    'close_door': 'door',
    'pass_door': 'door',
    'vault_window': 'window',
}
UNSUPPORTED_REASONS = {
    'sit': '坐下与起身的座椅接触执行器尚未接入',
    'carry': '搬拿物品的抓握、承重与搬运执行器尚未接入',
    'break': '破坏物品的碰撞与破碎执行器尚未接入',
}
BODY_SOURCES = frozenset(('mixamo_fbx/basic/Idle.fbx', 'mixamo_fbx/basic/Walk.fbx'))
VAULT_BODY_SOURCES = BODY_SOURCES | frozenset(('mixamo_fbx/basic/Run.fbx',))
ACTION_IDENTIFIER = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')


def validate_object_intents(raw, requested_prompt=None):
    """Return a JSON-safe ledger, recalculating every capability claim.

    Each entry contains action, object_type, description, supported and reason.
    Unknown but well-formed actions are retained as unsupported. An optional
    prompt is checked as text only: semantic extraction belongs to Hermes.
    """
    if requested_prompt is not None and (
        not isinstance(requested_prompt, str) or not requested_prompt.strip()
    ):
        raise ValueError('原始提示词需要非空文本')
    if not isinstance(raw, list) or len(raw) > 16:
        raise ValueError('object_intents需要列表，最多16个物品动作意图')
    normalized = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f'物品动作意图{index + 1}需要对象')
        action = item.get('action')
        object_type = item.get('object_type')
        description = item.get('description')
        if not isinstance(action, str) or not ACTION_IDENTIFIER.fullmatch(action):
            raise ValueError('物品动作action需要有效的英文小写内部标识')
        if not isinstance(object_type, str) or object_type not in OBJECT_TYPES:
            raise ValueError('物品动作object_type必须为window/door/table/chair/box/other')
        if not isinstance(description, str) or not description.strip():
            raise ValueError('每个物品动作意图必须保留非空description用户操作语义说明')
        expected_type = SUPPORTED_ACTIONS.get(action)
        supported = expected_type == object_type
        if supported:
            reason = ''
        elif expected_type is not None:
            reason = f'{action}只支持{expected_type}，不能用于{object_type}'
        else:
            reason = UNSUPPORTED_REASONS.get(action, f'物品动作{action}的执行器尚未接入')
        normalized.append({
            'action': action, 'object_type': object_type,
            'description': description.strip(), 'supported': supported, 'reason': reason,
        })
    return normalized


def _action_summary(key, count):
    return f'{key[0]}({key[1]})×{count}'


def validate_intent_coverage(cast, scene, single=None):
    """Reject missing, substituted, reordered or undeclared object operations.

    Coverage is per action and object type because the cast ledger does not yet
    assign concrete prop IDs. Additional static props are allowed. This check
    can run before a single-body plan exists, or again with that plan before
    building the scene. It does not enlarge the executor's current capability.
    """
    if not isinstance(cast, dict):
        raise ValueError('物品动作覆盖检查需要人物解析对象')
    intents = validate_object_intents(cast.get('object_intents', []))
    unsupported = [intent for intent in intents if not intent['supported']]
    if unsupported:
        descriptions = '；'.join(
            f"{intent['description']}：{intent['reason']}" for intent in unsupported
        )
        raise ValueError('请求的关键物品动作尚不支持，停止生成：' + descriptions)
    requires_interaction = cast.get('requires_interaction', False)
    if not isinstance(requires_interaction, bool) or requires_interaction != bool(intents):
        raise ValueError('requires_interaction必须与object_intents是否非空一致；不能删除物品动作意图')
    if intents and not cast.get('requires_scene', False):
        raise ValueError('物品动作意图同时需要requires_scene=true与场景计划')
    if scene is None:
        if intents:
            raise ValueError('物品动作意图缺少场景计划，不能用普通动作或静态布景代替')
        return intents
    if not isinstance(scene, dict):
        raise ValueError('物品动作覆盖检查需要场景对象')
    objects = scene.get('objects', [])
    interactions = scene.get('interactions', [])
    if not isinstance(objects, list) or not isinstance(interactions, list):
        raise ValueError('场景objects与interactions需要列表')
    by_id = {}
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get('id'), str):
            raise ValueError('场景物品需要有效id与type')
        if obj['id'] in by_id:
            raise ValueError('场景物品id不能重复')
        by_id[obj['id']] = obj
    if intents and cast.get('actor_count', 1) != 1:
        raise ValueError('当前物品操作执行器仅支持单人，双人物品交互尚未接入')
    expected = [(intent['action'], intent['object_type']) for intent in intents]
    for _, object_type in expected:
        if not any(obj.get('type') == object_type for obj in objects):
            raise ValueError(f'物品动作意图缺少匹配的{object_type}对象，不能替换为其它物品')
    actual = []
    for event in interactions:
        if not isinstance(event, dict):
            raise ValueError('场景物品操作需要对象')
        target = event.get('object')
        if not isinstance(target, str) or target not in by_id:
            raise ValueError('场景物品操作引用了不存在的匹配对象')
        action = event.get('action')
        object_type = by_id[target].get('type')
        if not isinstance(action, str) or not isinstance(object_type, str):
            raise ValueError('场景物品操作需要有效action和物品type')
        actual.append((action, object_type))
    expected_counts = Counter(expected)
    actual_counts = Counter(actual)
    missing = expected_counts - actual_counts
    extra = actual_counts - expected_counts
    if missing:
        details = '、'.join(_action_summary(key, count) for key, count in missing.items())
        raise ValueError('物品动作意图没有完整执行，interactions缺少匹配动作或数量不足：' + details)
    if extra:
        details = '、'.join(_action_summary(key, count) for key, count in extra.items())
        raise ValueError('场景interactions存在没有声明的物品动作意图：' + details)
    if actual != expected:
        raise ValueError('场景物品操作顺序与object_intents中的用户要求不一致')
    vault = any(action == 'vault_window' for action, _ in expected)
    if vault and (len(expected) != 1 or len(interactions) != 1):
        raise ValueError('翻窗第一版必须为唯一物品交互与最后动作；不能与开关、其它操作或连续翻窗混编')
    passage = any(action == 'pass_door' for action, _ in expected)
    if passage:
        passage_events=[event for event in interactions if event['action']=='pass_door']
        if len(passage_events)!=1:
            raise ValueError('门洞穿越第一版最多一次pass_door，不能用普通Walk或开关门替代穿越')
        target=passage_events[0]['object']
        if any(event['object']!=target or event['action'] not in ('open_door','pass_door','close_door')
               for event in interactions):
            raise ValueError('门洞穿越链仅允许同一扇门的开门、穿门、关门，不与其它操作混编')
    if single is not None:
        if not isinstance(single, dict):
            raise ValueError('物品动作覆盖检查需要单人动作计划对象')
        if intents:
            if passage and single.get('locomotion') is not None:
                raise ValueError('门洞穿越由专用链执行，不同时提交通用locomotion；不能用独立路线代替门洞关系')
            animations = single.get('animations')
            if not isinstance(animations, list) or not animations:
                raise ValueError('物品交互缺少有效身体来源')
            allowed = VAULT_BODY_SOURCES if vault else BODY_SOURCES
            if any(not isinstance(asset, str) or asset not in allowed for asset in animations):
                if vault:
                    raise ValueError('翻窗使用Idle/Walk/Run身体来源及专用穿窗执行器；普通Jump不能代替翻窗，与拳脚混编尚未接入')
                raise ValueError('当前扶桌与开关交互只支持Idle/Walk身体来源；与Run/Jump、拳脚等混编尚未接入，不能删除所需动作后生成')
    return intents
