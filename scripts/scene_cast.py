"""The agent interprets visible people before selecting a motion engine."""
import json
import re
from duel_plan import atomic_json, job_directory
from scene_intents import validate_object_intents


REQUIREMENT_CATEGORIES = frozenset(('action', 'sequence', 'timing', 'camera', 'scene', 'character', 'style', 'audio'))
REQUIREMENT_ID = re.compile(r'^[a-z][a-z0-9_]{0,31}$')


def validate_requirements(raw, prompt=None):
    """Keep explicit user requirements, including unsupported requests."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 128:
        raise ValueError('requirements需要1至128项完整自然语言需求')
    result, ids = [], set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {'id', 'category', 'source_text', 'description'}:
            raise ValueError('每项requirements只允许id、category、source_text、description，四项均必填')
        ident, category = item['id'], item['category']
        if not isinstance(ident, str) or not REQUIREMENT_ID.fullmatch(ident) or ident in ids:
            raise ValueError('requirements.id需要唯一的小写英文标识，例如r01')
        if not isinstance(category, str) or category not in REQUIREMENT_CATEGORIES:
            raise ValueError('requirements.category支持action、sequence、timing、camera、scene、character、style、audio')
        source, description = item['source_text'], item['description']
        if not isinstance(source, str) or not source.strip() or len(source) > 4000:
            raise ValueError('requirements.source_text需要原提示词逐字子串，最多4000字')
        if prompt is not None and (not isinstance(prompt, str) or source not in prompt):
            raise ValueError('requirements.source_text必须是原提示词逐字子串，不能改写或补写')
        if not isinstance(description, str) or not description.strip() or len(description) > 2000:
            raise ValueError('requirements.description需要非空明确需求，最多2000字')
        ids.add(ident)
        result.append({'id': ident, 'category': category, 'source_text': source,
                       'description': description.strip()})
    return result


def _request(job_id):
    path = job_directory(job_id) / 'request.json'
    return json.loads(path.read_text()) if path.exists() else {}


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
    for key in ('requires_scene','requires_interaction','requires_locomotion'):
        if not isinstance(raw.get(key,False),bool):
            raise ValueError(key+'需要布尔值')
    requires_scene=raw.get('requires_scene',False)
    requires_interaction=raw.get('requires_interaction',False)
    requires_locomotion=raw.get('requires_locomotion',False)
    intents=validate_object_intents(raw.get('object_intents',[]))
    if 'object_intents' in raw and bool(intents)!=requires_interaction:
        raise ValueError('requires_interaction必须覆盖全部物品操作，与object_intents是否为空保持一致；翻越也属于物品交互')
    if requires_interaction and not requires_scene:
        raise ValueError('物品交互必须同时要求场景')
    if count <= 2 and [a['id'] for a in normalized] != ['A', 'B'][:count]:
        raise ValueError('内部人物标识依次使用 A、B')
    unsupported=[item for item in intents if not item['supported']]
    supported = count <= 2 and not unsupported and not (count!=1 and intents) and not (
        requires_locomotion and (count!=1 or requires_interaction)
    )
    message=f'识别到 {count} 名人物'
    if count > 2:
        message+='；当前可生成 1 人动作或 2 人打斗，3 人及以上的协同动作尚未实现。'
    if count!=1 and intents:
        message+='；物品操作与翻窗执行器当前仅支持单人，多人物品交互尚未接入。'
    if requires_locomotion and count!=1:
        message+='；通用路线移动执行器当前仅支持单人，多人路线移动尚未接入。'
    if requires_locomotion and requires_interaction:
        message+='；通用路线移动与物品操作或翻窗的串联尚未接入，不能删去关键操作后生成。'
    if unsupported:
        message+='；无法完成以下关键要求：'+ '；'.join(item['description']+'：'+item['reason'] for item in unsupported)
        message+='。已停止生成，不能用普通动作或静态布景替代。'
    cast={'version': 1, 'job_id': job_directory(job_id).name, 'actors': normalized,
            'actor_count': count, 'supported': supported,
            'requires_scene':requires_scene,'requires_interaction':requires_interaction,
            'engine': ('single' if count == 1 else 'duel') if supported else None,
            'message':message}
    # Old saved plans remain readable. New automatic submissions must include
    # the complete operation ledger, including actions we cannot execute yet.
    if 'object_intents' in raw: cast['object_intents']=intents
    if 'requires_locomotion' in raw: cast['requires_locomotion']=requires_locomotion
    request = _request(job_id)
    if request.get('mode') == 'auto' and request.get('generation_contract_version') == 2 and 'requirements' not in raw:
        raise ValueError('统一入口v2必须保留完整requirements需求账本，包括不支持的要求')
    if 'requirements' in raw:
        cast['requirements'] = validate_requirements(raw['requirements'], request.get('prompt'))
    return cast


def write_cast(job_id, raw):
    request_path=job_directory(job_id)/'request.json'
    if request_path.exists() and json.loads(request_path.read_text()).get('mode')=='auto':
        if not isinstance(raw,dict) or any(key not in raw for key in ('requires_scene','requires_interaction','requires_locomotion','object_intents')):
            raise ValueError('统一入口需要明确提交requires_scene、requires_interaction、requires_locomotion和object_intents，不能省略场景、路线与全部操作需求识别')
    cast = validate_cast(raw, job_id)
    path = job_directory(job_id) / 'cast.json'
    if path.exists() and json.loads(path.read_text()) != cast:
        raise ValueError('任务已有不同人物解析')
    atomic_json(path, cast)
    return cast


def automatic_planning_prompt(prompt, job_id, dual_protocol, single_protocol):
    from scene_plan import scene_prompt
    return f'''这是同一个自然语言视频服务，用户不选择单人或双人版本。
先理解画面里需要几名人物、他们的身份和动作关系。人数由语义判断，不按关键词、动作种类或次数判断；“一个人连续两次出拳”是1人，“张三向李四反击”是2人，姓名中的数字不是人数。明确数量优先；未明确时独自练习通常是1人，相互打斗通常是2人。
先通过 MCP execute_code 调用 print(bpy.wm_scene_cast("{job_id}", {{"actors":[{{"id":"A","name":"用户描述的第一名人物"}}, ...],"requires_scene":是否要求场景物品,"requires_interaction":是否要求任何物品操作或穿越关系,"requires_locomotion":是否要求通用地面路线移动,"object_intents":[{{"action":"内部动作标识","object_type":"物品类型","description":"用户要求的操作和结果"}}],"requirements":[{{"id":"r01","category":"action","source_text":"原提示词逐字子串","description":"该明确需求"}}]}}))。三个布尔值、object_intents和requirements列表均为必填项，且与actors同级；静态布景及纯人物动作的object_intents=[]。
requirements是完整需求账本，逐项保留每个明确动作、顺序、时间、镜头、场景、人物、风格和音频要求；category支持action/sequence/timing/camera/scene/character/style/audio，id唯一（如r01），source_text必须逐字出现在原提示词中，description解释该明确要求。不得将多个独立要求塞成一句模糊保证，更不能只记录已支持动作；环绕、特写、音频、任意新动作等无法执行的要求也必须保留并在独立复核判unsupported。自然语言控制由你映射内部字段，用户无需提供资源名或参数。新auto请求generation_contract_version=2必填该账本，旧任务读取兼容。
明确人物数量与身份也必须逐项列入requirements，即使actors与actor_count已经记录；“一个人”须有character要求，不因动作计划已有A而跳过。账本是检查起点，后续独立复核必须从原文重新提取并补充遗漏，不能只遍历账本。
通用走跑、先走后跑、转弯、转身、起停和走到地面位置/物品附近而无操作时requires_locomotion=true，单人计划必须有locomotion路线和时间；明确只原地展示动作时为false。走到桌旁/门边/窗前只是接近目标，不自动添加扶桌、开门、翻窗操作；用户明确操作时仍保留物品意图。通用路线移动与物品操作/翻窗/拳脚串联尚未接入，不能删除任何关键要求；翻窗事件自带的walk_run/run/walk接近不另标requires_locomotion，不同时提交locomotion。双人战斗中的move仍用原双人协议，本轮通用路线移动仅单人。多人明确独立路径、编队走跑等需反馈尚未实现，不能删成人数或改成打斗。
requires_locomotion不是“原文包含走跑词语”标记：扶桌、开关门窗已经由各自交互事件执行走近，vault_window已经执行其approach，双人move已经由双人事件执行，这些自身接近与移动不另标requires_locomotion。比如“走到窗前打开窗户”用open_window及其内置走近，requires_locomotion=false；不能因此拒绝已支持要求。只有超出专用事件接近、明确独立路线/走跑切换/转身停步的通用运动才使用locomotion，若同时要求物品操作则本轮保留需求并反馈串联限制。
object_intents按用户要求的顺序完整列出全部物品操作，包括当前不支持的动作。扶桌使用touch_table，开关窗使用open_window/close_window，开关门使用open_door/close_door；翻窗、跑步跳跃窗户、从窗洞跳出去均使用vault_window，object_type=window，必须保留“穿过窗洞到另一侧”结果，不能拆成普通Jump加静态窗户。单人翻窗已接入独立事件执行器，先走再跑在该事件approach=walk_run中执行；普通素材列表是身体来源，不是翻窗路径。第一版翻窗为唯一物品交互和最后动作，不能与其它显式开关或战斗混编。用户未指定状态时，默认关闭窗会在视频开始前设为完全打开，作为初始净空准备写入executed_scene_plan.preconditions_applied；视频不表演无人开窗，effective_initial_state为open。该前置步骤不是已实现的远距离自动开窗，不代表用户要求的独立开窗动作。原文明确要求保持关闭、撞碎玻璃或先开窗再翻越时，必须反馈本轮无法覆盖，不得用前置准备替代。用户明确要求撞碎玻璃、坐椅、搬拿或破坏时必须另外保留break/sit/carry等实际意图并反馈不支持，不能删去关键要求。否定或仅作为背景提及的动作不算请求操作。
穿门、走出门外、从门洞进入使用pass_door，object_type=door，保留人物真正到固定门框另一侧的结果。比如“打开门，走出去，转身关门”完整object_intents顺序为open_door/pass_door/close_door，requires_scene=true、requires_interaction=true、requires_locomotion=false；接近与穿越由专用链执行，不另提交通用路线。只支持单人同一扇门最多一次穿越，全部交互仅该门开/过/关，Idle/Walk来源，无新camera时镜头为旧世界+X固定正侧剖视全景；指定角度/俯仰/位置必须用single.camera实际字段，不把默认视图当成完成指定斜角，环绕不属新placement；完整链未指定时长建议20秒，只有含pass_door的专用单人场景支持1..30秒；其它单人动作和通用路线仍1..15秒。只穿已打开门可仅pass；闭门必须有明确open_door才能通过，不能自动准备打开后省略用户开门动作。direction=outward为inside到outside，inward相反；inside/outside指固定框局部-Y/+Y，开关门door_side按实际侧或链状态填写。明确保持门关闭却穿越、破坏门、穿越后接拳脚/其它物品/独立走跑路线、多次或多人穿门均保留真实意图并反馈范围限制，不能删需求。
专用穿门链开关通过近铰链的真实双侧横拉杆contact_front/contact_back接触，不把远端旧把手的全角度可达性当作已支持。未指定门尺寸或角度时建议宽1.3米、开角80度；旧资产默认不变，明确用户尺寸和角度严格保留，不可行由几何拒绝。未指定完整链节奏可先分开门0..6、穿门6..14、关门14..20秒，再按原总长内安全时间策略执行；明确总时长（包括15秒）及操作时刻不可改，安全时间不足拒绝，不擅自延长。
1人内部id为A；2人为A、B，身份按此映射到动作计划；3人及以上仍完整列出人物，不得删成两人。若 supported=false，结束并说明message，不提交动作计划、不渲染。若 supported=true，只执行对应人数的一个协议。可在同一次 execute_code 中先保存人物解析，再保存所选动作计划。
用户需求：{prompt}
<场景协议>
{scene_prompt(job_id)}
</场景协议>
<双人协议>
{dual_protocol}
</双人协议>
<单人协议>
{single_protocol}
</单人协议>'''
