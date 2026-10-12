"""The agent interprets visible people before selecting a motion engine."""
import json
from duel_plan import atomic_json, job_directory


def validate_cast(raw, job_id):
    if not isinstance(raw, dict):
        raise ValueError('人物解析必须是对象')
    actors = raw.get('actors')
    if not isinstance(actors, list) or not 1 <= len(actors) <= 100:
        raise ValueError('需要明确画面中的人物列表')
    normalized = []
    for actor in actors:
        if not isinstance(actor, dict):
            raise ValueError('每名人物需要 id 与 name')
        ident, name = actor.get('id'), actor.get('name')
        if not isinstance(ident, str) or not ident or len(ident) > 32 or not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('人物 id 与 name 必须是有效文本')
        normalized.append({'id': ident, 'name': name.strip()})
    if len({a['id'] for a in normalized}) != len(normalized):
        raise ValueError('人物 id 不能重复')
    count = len(normalized)
    if count <= 2 and [a['id'] for a in normalized] != ['A', 'B'][:count]:
        raise ValueError('内部人物标识依次使用 A、B')
    supported = count <= 2
    return {'version': 1, 'job_id': job_directory(job_id).name, 'actors': normalized,
            'actor_count': count, 'supported': supported,
            'engine': ('single' if count == 1 else 'duel') if supported else None,
            'message': f'识别到 {count} 名人物' if supported else f'识别到 {count} 名人物；当前可生成 1 人动作或 2 人打斗，3 人及以上的协同动作尚未实现。'}


def write_cast(job_id, raw):
    cast = validate_cast(raw, job_id)
    path = job_directory(job_id) / 'cast.json'
    if path.exists() and json.loads(path.read_text()) != cast:
        raise ValueError('任务已有不同人物解析')
    atomic_json(path, cast)
    return cast


def automatic_planning_prompt(prompt, job_id, dual_protocol, single_protocol):
    return f'''这是同一个自然语言视频服务，用户不选择单人或双人版本。
先理解画面里需要几名人物、他们的身份和动作关系。人数由语义判断，不按关键词、动作种类或次数判断；“一个人连续两次出拳”是1人，“张三向李四反击”是2人，姓名中的数字不是人数。明确数量优先；未明确时独自练习通常是1人，相互打斗通常是2人。
先通过 MCP execute_code 调用 print(bpy.wm_scene_cast("{job_id}", {{"actors":[{{"id":"A","name":"用户描述的第一名人物"}}, ...]}}))。
1人内部id为A；2人为A、B，身份按此映射到动作计划；3人及以上仍完整列出人物，不得删成两人。若 supported=false，结束并说明message，不提交动作计划、不渲染。若 supported=true，只执行对应人数的一个协议。可在同一次 execute_code 中先保存人物解析，再保存所选动作计划。
用户需求：{prompt}
<双人协议>
{dual_protocol}
</双人协议>
<单人协议>
{single_protocol}
</单人协议>'''
