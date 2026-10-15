"""Pure-data object/room protocol shared by single-person and combat jobs."""
import json
import math
import re
from pathlib import Path
from duel_plan import PROJECT, number, atomic_json, job_directory

CATALOG = PROJECT/'knowledge/objects/catalog.json'
IDENT = re.compile(r'^[a-z][a-z0-9_]{0,31}$')


def object_catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def vector(raw, label, bounds):
    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError(label+'需要三个数值')
    return [number(v,label,lo,hi) for v,(lo,hi) in zip(raw,bounds)]


def validate_scene(raw, job_id, actor_count=1, duration=None):
    if not isinstance(raw,dict) or raw.get('version') != 1:
        raise ValueError('场景计划需要 version=1')
    if any(key in raw for key in ('actions','interaction','animations')):
        raise ValueError('物品操作字段为scene.interactions；人物素材animations属于wm_single_plan')
    if any(key in raw for key in ('camera', 'camera_policy', 'azimuth_deg', 'pitch_deg', 'fov')):
        raise ValueError('镜头字段属于人物计划single_plan.camera或双人camera，不能写入scene后忽略')
    seconds = number(raw.get('duration_seconds',duration), '场景时长', 1, 30)
    if duration is not None and abs(seconds-duration)>1e-6:
        raise ValueError('场景与人物计划的时长不一致')
    raw_interactions=raw.get('interactions',[])
    passage_doors={event['object'] for event in raw_interactions
                   if isinstance(event,dict) and event.get('action')=='pass_door' and
                   isinstance(event.get('object'),str)} if isinstance(raw_interactions,list) else set()
    room = raw.get('room',{})
    if not isinstance(room,dict) or not isinstance(room.get('enabled',False),bool):
        raise ValueError('room需要对象，enabled需要布尔值')
    size = vector(room.get('size',[6,5,3]),'房间尺寸',[(4,12),(4,12),(2.6,5)])
    objects = raw.get('objects',[])
    if not isinstance(objects,list) or not 0<=len(objects)<=16:
        raise ValueError('场景最多支持16个物品')
    result, ids, counts = [], set(), {}
    definitions = object_catalog()['objects']
    for obj in objects:
        if not isinstance(obj,dict) or obj.get('type') not in definitions:
            raise ValueError('当前物品支持 table、chair、window、door、box')
        kind = obj['type']; ident = obj.get('id')
        if not isinstance(ident,str) or not IDENT.fullmatch(ident) or ident in ids:
            raise ValueError('物品id需要唯一的小写英文标识')
        ids.add(ident); counts[kind]=counts.get(kind,0)+1
        definition = definitions[kind]
        is_passage_door=kind=='door' and ident in passage_doors
        default_size=[1.3,.14,2.1] if is_passage_door else definition['size']
        dims = vector(obj.get('size',default_size), '物品尺寸', list(zip(definition['min'],definition['max'])))
        if 'position' in obj:
            position = vector(obj['position'],'物品位置',[(-6,6),(-6,6),(0,2)])
        else:
            # Distinct defaults, with the action corridor in front of furnishings.
            n = counts[kind]-1
            defaults={'table':[-1.55+n*1.4,.55,0], 'chair':[-2.35+n*.65,-.65,0],
                      'window':[-1.35+n*1.6,size[1]/2,.85], 'door':[1.45+n*1.4,size[1]/2,0],
                      'box':[2.2+n*.65,.7,0]}
            position=vector(defaults[kind],'默认物品位置',[(-6,6),(-6,6),(0,2)])
        yaw = number(obj.get('yaw_deg',0),'物品朝向',-180,180)
        if kind in ('table','chair','box','door') and abs(position[2])>1e-6:
            raise ValueError('桌椅、箱子与门需要落地，当前不支持悬空摆放')
        if room.get('enabled',False):
            angle=math.radians(yaw)
            extent_x=(abs(math.cos(angle))*dims[0]+abs(math.sin(angle))*dims[1])/2
            extent_y=(abs(math.sin(angle))*dims[0]+abs(math.cos(angle))*dims[1])/2
            if abs(position[0])+extent_x>size[0]/2+.01 or position[2]+dims[2]>size[2]+.01:
                raise ValueError('物品尺寸或位置超出房间范围')
            if kind in ('window','door'):
                if abs(position[1]-size[1]/2)>.02 or abs(yaw)>.01:
                    raise ValueError('房间门窗需要位于后墙，朝向为0；系统会为它们生成墙洞')
            elif abs(position[1])+extent_y>size[1]/2+.01:
                raise ValueError('物品位置超出房间范围')
        state=obj.get('state','closed')
        if state not in ('closed','open') or (kind not in ('door','window') and state!='closed'):
            raise ValueError('门窗state支持closed或open')
        angle=number(obj.get('open_angle_deg',80 if is_passage_door else 70),'门窗开启角度',25,80)
        focus=obj.get('focus',True)
        if not isinstance(focus,bool): raise ValueError('focus需要布尔值')
        result.append({'id':ident,'type':kind,'size':dims,'position':position,'yaw_deg':yaw,
                       'state':state,'open_angle_deg':angle,'focus':focus})
    apertures=[o for o in result if o['type'] in ('door','window')]
    for i,a in enumerate(apertures):
        for b in apertures[i+1:]:
            if abs(a['position'][1]-b['position'][1])<.2 and abs(a['position'][0]-b['position'][0])<(a['size'][0]+b['size'][0])/2+.12:
                raise ValueError('门窗洞口互相重叠，请调整位置')
    interactions=raw.get('interactions',[])
    if not isinstance(interactions,list) or len(interactions)>8:
        raise ValueError('物品交互需要列表，最多8次')
    if interactions and actor_count!=1:
        raise ValueError('当前物品交互支持单人；双人支持带物品的打斗场景')
    if any(isinstance(event,dict) and event.get('action')=='vault_window' for event in interactions) and len(interactions)!=1:
        raise ValueError('翻窗第一版必须为唯一物品交互与最后动作；不与开关、其它操作或连续翻窗混编')
    passages=[event for event in interactions if isinstance(event,dict) and event.get('action')=='pass_door']
    if len(passages)>1:
        raise ValueError('门洞穿越第一版最多一次pass_door，不支持连续或往返穿门')
    passage_door=passages[0].get('object') if passages else None
    passage_direction=passages[0].get('direction','outward') if passages else None
    if passages and passage_direction not in ('outward','inward'):
        raise ValueError('pass_door.direction只支持outward或inward')
    current_door_side=('inside' if passage_direction=='outward' else 'outside') if passages else None
    if passages and any(not isinstance(event,dict) or event.get('object')!=passage_door or
                        event.get('action') not in ('open_door','pass_door','close_door') for event in interactions):
        raise ValueError('门洞穿越链只允许同一扇门的open_door/pass_door/close_door，不能混其它物品或动作')
    if not passages and any(isinstance(event,dict) and 'door_side' in event for event in interactions):
        raise ValueError('door_side本轮仅用于包含pass_door的穿门链；普通开关门保持旧协议')
    by_id={o['id']:o for o in result}; previous_end=0; states={o['id']:o['state'] for o in result}
    default_hand='left' if passages else 'right'
    normalized=[]
    for i,event in enumerate(interactions):
        if not isinstance(event,dict) or event.get('object') not in by_id:
            raise ValueError('交互引用了不存在的物品')
        obj=by_id[event['object']]; action=event.get('action')
        if action not in definitions[obj['type']]['actions']:
            raise ValueError('该物品尚不支持请求的交互')
        if passages:
            allowed={'id','actor','object','action','side','start','end'} | (
                {'direction'} if action=='pass_door' else {'door_side'})
            unknown=set(event)-allowed
            if unknown:
                raise ValueError('穿门链存在未执行字段：'+'、'.join(sorted(str(key) for key in unknown)))
        if event.get('actor','A')!='A' or event.get('side',default_hand) not in ('left','right'):
            raise ValueError('物品交互人物为A，手侧为left或right')
        start=number(event.get('start'),'交互起点',0,seconds)
        minimum=6 if action=='vault_window' else (3 if action=='pass_door' else 1.8)
        label='翻窗终点（至少6秒）' if action=='vault_window' else ('穿门终点（至少3秒）' if action=='pass_door' else '交互终点')
        end=number(event.get('end'),label,start+minimum,seconds)
        if start<previous_end-1e-6: raise ValueError('物品交互需要按顺序且不能重叠')
        previous_end=end
        if action.startswith(('open_','close_')):
            expected='closed' if action.startswith('open_') else 'open'
            if states[obj['id']]!=expected: raise ValueError('门窗当前状态与开关动作不匹配')
            states[obj['id']]='open' if expected=='closed' else 'closed'
        operation={'id':f'p{i+1:02d}','actor':'A','object':obj['id'],'action':action,
                   'side':event.get('side',default_hand),'start':start,'end':end}
        if 'direction' in event and action!='pass_door':
            raise ValueError('direction仅用于pass_door穿门事件，不能静默忽略')
        if 'door_side' in event and action not in ('open_door','close_door'):
            raise ValueError('door_side仅用于开关门事件，pass_door用direction确定固定门框两侧')
        if action in ('open_door','close_door') and ('door_side' in event or passages):
            door_side=event.get('door_side',current_door_side)
            if door_side not in ('inside','outside'):
                raise ValueError('door_side只支持inside或outside，指固定门框的两侧')
            if passages and door_side!=current_door_side:
                raise ValueError('开关门侧与人物当前门框侧不一致，不能跨侧瞬移；开门侧必须匹配穿门起侧，关门侧匹配穿门后的侧')
            operation['door_side']=door_side
        if action=='pass_door':
            if states[obj['id']]!='open':
                raise ValueError('pass_door要求门已打开；闭门穿越不能省略开门动作或自动准备打开')
            operation['direction']=passage_direction
            current_door_side='outside' if passage_direction=='outward' else 'inside'
        if action=='vault_window':
            approach=event.get('approach','walk_run')
            if approach not in ('walk_run','run','walk'):
                raise ValueError('翻窗approach支持walk_run、run或walk')
            operation['approach']=approach
            if approach=='walk_run':
                operation['walk_fraction']=number(event.get('walk_fraction',.45),'先走后跑的步行比例',.2,.7)
            elif 'walk_fraction' in event:
                raise ValueError('walk_fraction仅在approach=walk_run时有效，不能静默忽略')
        elif any(key in event for key in ('approach','walk_fraction')):
            raise ValueError('approach与walk_fraction仅用于vault_window翻窗交互')
        normalized.append(operation)
    timing=raw.get('timing_policy','auto')
    if timing not in ('auto','exact'): raise ValueError('timing_policy支持auto或exact')
    return {'version':1,'job_id':job_directory(job_id).name,'duration_seconds':seconds,'timing_policy':timing,
            'room':{'enabled':room.get('enabled',False),'size':size},'objects':result,'interactions':normalized}


def allocate_interaction_times(events,required,policy='auto'):
    """Keep order, overall duration and pauses; fit safe travel/operation times."""
    if not events: return []
    requested=[e['end']-e['start'] for e in events]
    if policy=='exact':
        if any(a+1e-6<b for a,b in zip(requested,required)):
            raise ValueError('指定交互时刻不足以完成安全走近和操作，请延长时长或把物品放近')
        return [dict(e) for e in events]
    if all(a+1e-6>=b for a,b in zip(requested,required)): return [dict(e) for e in events]
    available=sum(requested);minimum=sum(required)
    if minimum>available+1e-6:
        raise ValueError(f'交互至少需要{minimum:.1f}秒，请延长视频或把物品放近')
    slack=available-minimum;cursor=events[0]['start'];result=[]
    for i,event in enumerate(events):
        seconds=required[i]+slack*requested[i]/available
        result.append({**event,'start':cursor,'end':cursor+seconds})
        cursor+=seconds
        if i+1<len(events): cursor+=events[i+1]['start']-event['end']
    result[-1]['end']=events[-1]['end']
    return result


def load_scene(folder,job_id,actor_count,duration):
    path=Path(folder)/'scene_plan.json'
    if not path.exists(): return None
    return validate_scene(json.loads(path.read_text()),job_id,actor_count,duration)


def write_scene(job_id,raw):
    folder=job_directory(job_id)
    cast_path=folder/'cast.json'
    if not cast_path.exists(): raise ValueError('请先提交人物识别，再提交场景')
    from scene_cast import validate_cast
    cast=validate_cast(json.loads(cast_path.read_text()),job_id)
    if not cast['supported']: raise ValueError(cast['message'])
    scene=validate_scene(raw,job_id,cast['actor_count'])
    if cast['requires_interaction'] and not scene['interactions']:
        raise ValueError('用户要求物品交互，请填写interactions中的实际操作；不能只保存布景')
    if 'object_intents' in cast:
        from scene_intents import validate_intent_coverage
        validate_intent_coverage(cast,scene)
    path=folder/'scene_plan.json'
    if path.exists() and json.loads(path.read_text())!=scene:
        raise ValueError('任务已有不同场景，不允许覆盖')
    for filename in ('plan.json','single_plan.json'):
        if (folder/filename).exists():
            base=json.loads((folder/filename).read_text())
            validate_scene(scene,job_id,cast['actor_count'],base['duration_seconds'])
    atomic_json(path,scene)
    return scene


def scene_prompt(job_id):
    definitions=object_catalog()
    available={k:{'aliases':v['aliases'],'size':v['size'],'actions':v['actions']} for k,v in definitions['objects'].items()}
    return f'''场景与物品也由自然语言解析，用户无需填物品ID或模型路径。人物识别时补充 requires_scene、requires_interaction、requires_locomotion 和 object_intents。
用户要求房间/家具/门窗/箱子时 requires_scene=true；要求任何物品操作、扶桌、开关、翻越、坐下、搬拿等时 requires_interaction=true，同时requires_scene=true。仅人物动作保持false；仅布景的object_intents为空。
这些标记必须位于cast根对象，与actors同级，不能放在actors的某名人物内部。Python代码中使用True/False。object_intents记录全部操作，已支持与未支持的都不能遗漏。
有场景时，先提交人物识别，再调用 print(bpy.wm_scene_plan("{job_id}", scene))，确认ok=true，最后提交对应人物动作计划。场景与人物计划必须同一时长。
scene={{"version":1,"duration_seconds":秒,"room":{{"enabled":true或false,"size":[6,5,3]}},"objects":[{{"id":"table_1","type":"table"}}],"interactions":[]}}。
有操作时必须将完整interactions列表放在scene根对象，与objects同级。wm_single_plan提交人物素材、镜头以及可选的locomotion通用路线，严禁把interactions写进人物计划，否则提交会被拒绝。检查wm_scene_plan返回的interactions数量覆盖用户的每个操作。
单人独立目标走跑、转弯、加减速、停步使用requires_locomotion=true与single_plan.locomotion。静态物品与空interactions的场景可配路线；只走到物品旁不算触摸或开关，不伪造交互。扶桌/开关事件内的走近、vault_window内的walk_run及双人move各自有专用执行器，不另设通用路线标记；本轮通用路线与物品操作、翻窗或拳脚混编尚未接入。
timing_policy默认auto：后台会在原总时长内适当分配走近与操作时间，保留动作顺序和空档；用户明确指定各动作时间点时必须设为exact，时间不足则明确报错，不擅自改变用户时间。
物品目录：{json.dumps(available,ensure_ascii=False)}。
物品可选size=[宽,深,高]米、position=[x,y,z]米、yaw_deg、focus=true。默认位置留出中间人物活动区域；桌椅箱子默认靠后，窗户和门在后墙(y=房间深度/2)，窗底高0.85米、门底0；墙面自动生成真实洞口。没有特别位置要求时省略position。房间门窗朝向必须为0；多个同类可重复但id唯一。仅明确需要房间时room.enabled=true。物品不能省略或替换用户要求的类型与数量。
静态物品兼容单人已有动作和双人打斗。旧普通交互使用固定全景，旧翻窗是沿窗面固定侧视，旧门链为世界+X固定正侧剖视；这些旧预设不能证明指定斜角。需要角度/俯仰/位置/透视时在single_plan.camera填写实际执行参数，不放进scene：version=1，reference=world/object/actor，object/actor指定真实target，azimuth_deg=-180..180（0=-Y前方、90=+X右侧、-90=-X左侧、180=+Y后方），elevation_deg=-80..80，projection=orthographic|perspective，framing=full_body，fov_deg=20..90，occlusion_policy=transparent_architecture|strict。物品参考方位使用固定框yaw；要同时看到窗面与人物的斜侧要求规划物品相对约30..45度并核对原文，不旋转窗户代替转相机。world position/look_at每轴-30..30米，position不可与reference/target/azimuth/elevation同时提交，look_at可配方向模式。明确位置和方向不自动更改；无法构图或无遮挡时拒绝。transparent_architecture仅半透明白模参考，strict不偷偷透明化；环绕、特写、动态跟拍未接入新placement。新camera时旧外层参数省略或使用fixed/0/3.5/50兼容默认，不提交非默认两套参数。单人物品交互支持touch_table（扶桌）、open_window、close_window、open_door、close_door及vault_window（翻窗）。普通操作格式={{"actor":"A","object":"window_1","action":"open_window","side":"right","start":0,"end":5}}，至少1.8秒，建议4至5秒，按顺序不重叠，含走近/伸手/操作/松手；远处走近应留足时间，不要让人物高速滑动。窗门state默认closed，关窗/关门起始需要state=open，或者先有开启动作。开角open_angle_deg默认70，范围25..80。
单人门洞穿越使用pass_door，不能用普通Walk轮播或开关门替代。完整链未指定时长建议20秒：interactions=[{{"object":"door_1","action":"open_door","start":0,"end":6,"door_side":"inside"}},{{"object":"door_1","action":"pass_door","start":6,"end":14,"direction":"outward"}},{{"object":"door_1","action":"close_door","start":14,"end":20,"door_side":"outside"}}]。direction支持outward（固定门框inside到outside，默认）或inward（outside到inside）；inside指门框局部-Y、outside指局部+Y，与门扇铰链转动无关。开关门的door_side为inside/outside，仅在含pass_door的链有效；省略时按穿越方向和前面事件推导，但明确侧错误会拒绝，不能跨侧瞬移。
pass_door至少3秒，要求门已经open（初始state=open或此前open_door事件）；closed门不能自动准备打开、不能省略明确开门动作。只要求穿过未指定状态的门时可规划初始open门而不添加开关表演；明确保持闭门穿越或破坏门后穿越尚不支持，保留真实意图并说明。单人、同一扇门、最多一次pass_door，链中全部interactions仅允许该门open_door/pass_door/close_door，可仅pass或pass后close。人物只使用Idle/Walk身体来源、requires_locomotion=false、不提交single_plan.locomotion；该链自身复用连续移动和IK底座，不是未接入的任意动作混编。保留用户明确总时长与操作时刻，实际门扇/门框/身体净空、把手可达性、站位和安全时间由执行器检查，不可行即拒绝。范围仅平地穿门洞，不包括跑步穿门、楼梯、坡面、跨房间导航、多人、拳脚或其它物品操作。
穿门专用链的开关使用门扇上近铰链半径0.30米的真实可见双侧横拉杆，以及contact_front/contact_back表面接触锚，手部IK追踪门扇推拉小弧；旧远端handle保留给原开关流程，不宣称它能在大角度全程可达。仅被pass_door引用的门，raw对象缺size时helper应用[1.3,0.14,2.1]米，缺open_angle_deg时应用80；两字段独立处理，不依赖规划者记得补填。普通门与目录旧默认0.9米/70度保持不变。用户明确尺寸与开角必须保留，默认窄门或小角度可能无足够身体净空，由实际几何拒绝，不能暗改。含pass_door的单人场景及人物计划可1..30秒，其它单人动作与通用locomotion仍1..15。完整链20秒的0..6开、6..14过、14..20关只是未指定节奏时的初始分配；明确15秒仍为15秒，可行则执行，不可行则拒绝，不延长，auto仍按真实安全时间分配原总长；明确时刻必须exact并保持，时间不足不能缩减操作或高速滑过门洞。
含pass_door的专用链未指定手侧时side缺省left，规划优先左手接触横拉杆；普通无pass交互仍缺省right。用户明确right必须保留，不自动改左，由执行器验收可达、接触与碰撞；不可行则拒绝。
翻窗格式={{"actor":"A","object":"window_1","action":"vault_window","side":"right","start":0,"end":9,"approach":"walk_run","walk_fraction":0.45}}。approach支持walk_run（先走后跑，默认）、run（直接跑近）、walk（走近后翻越）；walk_fraction仅在walk_run有效，默认0.45、范围0.2..0.7，表示接近阶段的步行时间比例。根据用户顺序和节奏选择，不使用固定提示词映射。至少6秒，未指定时长建议8..10秒；用户明确指定的总时长或动作时刻保持原值，时间不足则报错。第一版翻窗必须是唯一物品交互及最后动作，不与其它显式开关、扶桌、连续翻窗或战斗混编。该事件实际执行接近、双手撑窗、身体通过窗洞、落地与站稳；普通Jump或开窗不能代替。用户未指定窗状态时，默认closed窗在视频开始前设为完全打开，executed_scene_plan.preconditions_applied记录此初始净空准备，effective_initial_state为open；原scene_plan仍保留closed，不改变复核来源。视频不表演无人开窗，不增加第二个interaction，也不宣称支持远距离自动开窗。原文明确要求窗户保持关闭、撞碎玻璃或先开窗再翻越时，本轮不能支持；撞碎要保留break意图，显式开窗要保留open_window意图，不得用前置准备代替并省略需求。
窗口可按用户要求改变位置与尺寸，原尺寸范围保持不变；翻窗执行器另限制窗宽>=1.0米、窗高>=1.0米、窗底高0.55..1.05米，并检查实际变形皮肤穿洞净空、撑窗可达性、连续轨迹和落地，无效则拒绝，不放宽碰撞或净空阈值。支持的接近速度档为walk/run/walk_run，不能承诺未接入的任意速度控制。窗外留出落地区域，不能将窗后放置障碍物。
单人物品交互仍提交wm_single_plan，duration_seconds与场景相同，camera_policy=fixed。扶桌与开关操作animations只使用mixamo_fbx/basic/Idle.fbx或Walk.fbx作为身体来源；翻窗可使用Idle.fbx、Walk.fbx、Run.fbx，且接近顺序由vault事件决定。该流程自动使用手脚IK与路径控制。其它拳脚/跑跳混编、坐椅、搬拿、破坏、其它障碍攀爬翻越及双人物品交互尚未接入；关键要求不支持时明确说明，不要提交删去关键动作的计划。'''
