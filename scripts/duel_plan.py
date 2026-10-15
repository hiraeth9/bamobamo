"""Versioned, data-backed duel plans. No baked paired choreography or prompt templates."""
import hashlib
import json
import math
import os
import re
from pathlib import Path
from duel_impacts import reaction_kind

PROJECT = Path(__file__).resolve().parents[1]
JOBS = PROJECT / 'output' / 'jobs'
CATALOG = PROJECT / 'knowledge' / 'motions' / 'duel.json'
JOB_PATTERN = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')


def job_directory(job_id):
    if not isinstance(job_id, str) or not JOB_PATTERN.fullmatch(job_id):
        raise ValueError('任务 ID 格式不正确')
    return JOBS / job_id


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def interaction_rules():
    return json.loads((PROJECT / 'knowledge' / 'interaction_rules' / 'duel.json').read_text(encoding='utf-8'))


def number(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f'{name} 必须是有限数字')
    if not lo <= value <= hi:
        raise ValueError(f'{name} 必须在 {lo} 至 {hi} 之间')
    return value


def validate_plan(raw, job_id):
    """Reject ambiguous timing/resources rather than silently substitute user intent."""
    if not isinstance(raw, dict):
        raise ValueError('计划必须是对象')
    plan = json.loads(json.dumps(raw, allow_nan=False))
    if plan.get('version') != 1:
        raise ValueError('计划 version 必须为 1')
    if plan.get('render_style', 'white_model') != 'white_model':
        raise ValueError('render_style只支持white_model现有白模渲染，不支持其它风格或精确色值')
    plan['render_style'] = 'white_model'
    plan['job_id'] = job_directory(job_id).name
    duration = number(plan.get('duration_seconds'), '时长', 2, 30)
    if plan.get('fps', 30) != 30:
        raise ValueError('当前双人路径支持 30 FPS')
    plan['fps'] = 30
    plan['seed'] = int(number(plan.get('seed', 0), 'seed', 0, 2**31 - 1))
    plan.setdefault('pace', 'standard')
    if plan['pace'] not in ('standard', 'intense'):
        raise ValueError('节奏支持 standard 或 intense')
    actors = plan.get('actors', [{'id': 'A'}, {'id': 'B'}])
    if not isinstance(actors, list) or len(actors) != 2 or not all(isinstance(a, dict) for a in actors) or {a.get('id') for a in actors} != {'A', 'B'}:
        raise ValueError('双人角色必须分别为 A、B')
    plan['actors'] = actors
    camera = plan.get('camera', {})
    if not isinstance(camera, dict):
        raise ValueError('camera 必须为对象')
    unknown_camera = set(camera)-{'policy', 'azimuth_deg', 'margin', 'shots', 'placement'}
    if unknown_camera:
        raise ValueError('camera存在未执行字段：'+'、'.join(sorted(unknown_camera))+'；空间位置使用camera.placement')
    policy = camera.get('policy', 'fixed_full')
    if policy not in ('fixed_full', 'cinematic'):
        raise ValueError('双人镜头支持 fixed_full 或 cinematic')
    normalized_camera = {'policy': policy, 'azimuth_deg': number(camera.get('azimuth_deg', -68), '相机角度', -80, -45), 'margin': .05}
    if camera.get('margin', .05) != .05:
        raise ValueError('当前全身镜头边距固定0.05，不能忽略自定义margin')
    if 'placement' in camera:
        from camera_plan import validate_camera
        if camera.get('azimuth_deg', -68) != -68:
            raise ValueError('新camera.placement与旧azimuth_deg不能同时指定非默认角度')
        normalized_camera['placement'] = validate_camera(camera['placement'])
    if policy != 'cinematic' and 'shots' in camera:
        raise ValueError('shots只在cinematic镜头执行，fixed_full不能忽略分镜')
    if policy == 'cinematic':
        shots = camera.get('shots')
        if not isinstance(shots, list) or not 1 <= len(shots) <= 12:
            raise ValueError('电影镜头需要 1 至 12 个连续片段')
        cursor = 0
        normalized = []
        for shot in shots:
            if not isinstance(shot, dict) or shot.get('kind') not in ('full', 'pov'):
                raise ValueError('镜头支持全景 full 或人物第一视角 pov')
            start = number(shot.get('start'), '镜头起点', 0, duration)
            end = number(shot.get('end'), '镜头终点', start + .15, duration)
            if abs(start - cursor) > 1e-5:
                raise ValueError('镜头须连续覆盖全片，不留空段或重叠')
            clean = {'kind': shot['kind'], 'start': start, 'end': end}
            if shot['kind'] == 'pov':
                if shot.get('actor') not in ('A', 'B'):
                    raise ValueError('第一视角需要明确来自人物 A 或 B')
                clean.update(actor=shot['actor'], fov=number(shot.get('fov', 75), '第一视角视角', 60, 95))
            normalized.append(clean)
            cursor = end
        if abs(cursor - duration) > 1e-5:
            raise ValueError('镜头必须覆盖到视频结束')
        normalized_camera['shots'] = normalized
    plan['camera'] = normalized_camera
    events = plan.get('events')
    if not isinstance(events, list) or not 1 <= len(events) <= 24:
        raise ValueError('需要 1 至 24 个动态事件')
    motions = catalog()['motions']
    previous_end = 0
    fallen = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError('事件必须为对象')
        event['id'] = f'e{index + 1:02d}'
        kind = event.get('kind', 'strike')
        if kind not in ('strike', 'move', 'guard'):
            raise ValueError('支持 strike、move、guard 事件')
        event['kind'] = kind
        start = number(event.get('start'), '事件起点', 0, duration)
        end = number(event.get('end'), '事件终点', start + .3, duration)
        if start < previous_end - 1e-6:
            raise ValueError('当前事件按时间排序且不能重叠，双方反应包含于进攻事件内')
        previous_end = end
        actor = event.get('actor')
        if actor not in ('A', 'B', 'both') or (kind == 'strike' and actor == 'both'):
            raise ValueError('事件引用了无效角色')
        if kind == 'strike' and fallen:
            raise ValueError('当前倒地后保持落地状态，不支持继续攻击或起身；请把击倒放在最后一次交锋')
        if kind == 'move' and (actor in fallen or actor == 'both' and fallen):
            raise ValueError('倒地人物不能继续迈步')
        if kind == 'strike':
            if end - start < .8 - 1e-6:
                raise ValueError('出招至少需要 0.8 秒，保留准备、接触和回收')
            motion = event.get('motion')
            if motion not in motions or motions[motion]['category'] != 'attack':
                raise ValueError(f'没有可执行的攻击来源：{motion}')
            if event.get('outcome') not in ('hit', 'block', 'dodge'):
                raise ValueError('攻击结果需要 hit、block 或 dodge')
            if event.get('region', 'body') not in ('head', 'body', 'abdomen', 'chest'):
                raise ValueError('当前支持头部、躯干、腹部和胸部攻击')
            if event.get('side', motions[motion]['side']) not in ('left', 'right'):
                raise ValueError('攻击侧必须为 left 或 right')
            event.setdefault('side', motions[motion]['side'])
            event.setdefault('region', 'body')
            event.setdefault('intensity', .65)
            number(event['intensity'], '攻击强度', .2, 1)
            if event['outcome'] == 'hit':
                event.setdefault('reaction', reaction_kind(event))
                if event['reaction'] not in ('recoil', 'stagger', 'knockdown'):
                    raise ValueError('受击反应支持 recoil、stagger 或 knockdown')
            elif event.get('reaction', 'none') != 'none':
                raise ValueError('格挡或闪避不能产生命中击退或击倒')
            peak = number(event.get('peak', start + (end - start) * .46), '接触时刻', start + .25, end - .25)
            event['peak'] = peak
            if event.get('reaction') == 'knockdown':
                if peak + 2 > duration:
                    raise ValueError('击倒后至少保留 2 秒完成倒地和落地展示')
                fallen.add('B' if actor == 'A' else 'A')
            event.setdefault('dodge', 'side')
            if event['dodge'] not in ('side', 'back', 'duck'):
                raise ValueError('闪避支持 side、back、duck')
        elif kind == 'move':
            if event.get('direction') not in ('apart', 'closer', 'left', 'right'):
                raise ValueError('移动方向需要 apart、closer、left 或 right')
            event.setdefault('distance', .25)
            number(event['distance'], '移动距离', .05, .45)
    if plan['pace'] == 'intense':
        strikes = [e for e in events if e['kind'] == 'strike']
        if not strikes or strikes[0]['start'] > .8:
            raise ValueError('持续激烈打斗应在 0.8 秒内开始交锋')
        for previous, following in zip(strikes, strikes[1:]):
            if following['start'] - previous['end'] > .6 + 1e-6:
                raise ValueError('激烈交锋之间最多留 0.6 秒恢复；不要用长待机填充')
        if any(e['end'] - e['start'] > 1.4 + 1e-6 for e in strikes):
            raise ValueError('激烈打斗单次攻击最多 1.4 秒，保留短准备与回收')
        if strikes[-1].get('reaction') != 'knockdown' and duration - strikes[-1]['end'] > 1 + 1e-6:
            raise ValueError('持续激烈打斗不要用超过 1 秒的无交锋尾段填充')
    plan['events'] = events
    plan['frame_count'] = round(duration * 30)
    # Check data IDs and file hashes at the execution boundary as well.
    for item in motions.values():
        if item.get('asset'):
            asset = (PROJECT / item['asset']).resolve()
            allowed_roots = (PROJECT / 'mixamo_fbx', PROJECT / 'assets' / 'incoming' / 'combat' / 'quaternius')
            if not any(asset.is_relative_to(root) for root in allowed_roots) or not asset.is_file():
                raise ValueError('动作资源不存在或路径越界')
            if item.get('sha256') and hashlib.sha256(asset.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('动作资源哈希改变，需要重新校准知识记录')
    return plan


def write_plan(job_id, raw, revision=0):
    path = job_directory(job_id)
    if revision not in (0, 1, 2):
        raise ValueError('最多允许两轮修正')
    filename = 'plan.json' if revision == 0 else f'plan_candidate_{revision}.json'
    if (path / filename).exists():
        current = json.loads((path / filename).read_text())
        plan = validate_plan(raw, job_id)
        if current != plan:
            raise ValueError('任务已有不同计划，不允许覆盖正在执行的任务')
        return plan
    plan = validate_plan(raw, job_id)
    atomic_json(path / filename, plan)
    return plan


def planning_prompt(prompt, job_id):
    motions = catalog()['motions']
    rules = interaction_rules()
    descriptions = '\n'.join(f"{key}: {value['description']}" for key, value in motions.items() if value['category'] == 'attack')
    return f'''你是双人白膜动画导演。使用 white-model-director 技能的双人流程。
用户只输入自然语言，你负责生成新事件序列，不能要求用户提供动作名或 JSON。
当前任务 {job_id}，只通过 Blender MCP execute_code 调用 print(bpy.wm_duel_plan("{job_id}", plan)) 提交内部计划。
不要调用 wm_clear/import/render，不要渲染、不执行 shell、不等待视频；提交返回 ok=true 后结束。任务管理器会执行并检查场景。
可执行攻击来源：
{descriptions}
程序化来源还支持防守姿态、双臂格挡、侧闪、后撤、下潜闪避和迈步。
专业交互数据：{json.dumps({k:rules[k] for k in ('hit','block','dodge','support','transition')},ensure_ascii=False)}
支持重拳击退、最后一次交锋后的仰面击倒；倒地后保持落地，不支持起身再战。武器、摔投、地面缠斗和多人尚未接入；关键要求超出能力时不要提交虚假计划。
根据用户要求决定动作顺序、先手角色、部位、左右侧、命中/格挡/闪避、强弱和节奏。避免所有输入使用相同顺序。
计划结构：version=1, duration_seconds(默认15,范围2..30), fps=30, seed=整数, pace="standard"或"intense",
actors=[{{"id":"A"}},{{"id":"B"}}], camera={{"policy":"fixed_full"}}, events=[...]
规范计划固定保存render_style="white_model"，仅标明现有clay白模执行器；白模要求可引用该实际字段，不覆盖精确RGB、服装、背景、音效或其它未接入风格。
默认固定全身镜头。用户明确要求第一视角或切换镜头时，camera={{"policy":"cinematic","shots":[{{"kind":"full","start":0,"end":1}},{{"kind":"pov","actor":"B","start":1,"end":2.5}},{{"kind":"full","start":2.5,"end":视频时长}}]}}。镜头时间连续覆盖全片；第一视角短暂展示该人物自己的拳和对手，接触后约0.2..0.4秒切回全景看后退或倒地。全身边距约束应用于全景，第一视角不承诺同时显示双方全身。
用户指定全景镜头空间位置/方位时，用camera.placement={{"version":1,"reference":"world","azimuth_deg":-45,"elevation_deg":15,"projection":"orthographic","framing":"full_body","occlusion_policy":"transparent_architecture"}}，可与full/pov分镜共存。新placement方位0=参考前方(-Y)、+90=右(+X)、-90=左(-X)、180=后(+Y)；world世界、object目标固定物品框(需要target物品id)、actor首帧人物朝向(需要target A/B)。明确世界position/look_at=[x,y,z]也支持，但position不能同时含reference/target/azimuth/elevation。projection可perspective，fov_deg20..90；构图冲突或strict不透明物品挡主体会拒绝，不能擅自换角或把strict改透明。旧camera.azimuth_deg与新placement不要混填非默认。用户不需写字段，原文方位、角度、俯视和坐标由你转入相应参数；无法支持的移动相机/跟拍/特写仍保留要求并反馈限制，不能只写fixed当覆盖。
事件 start/end/peak 都是秒，按时间排序，不重叠；空档持续格斗待机，绝不回立正或 T-pose。
攻击事件：kind="strike", actor="A"或"B", motion=来源ID, side="left"或"right", region="head"(头)/"body"(躯干)/"abdomen"(腹)/"chest"(胸), outcome="hit"或"block"或"dodge", start,end,peak,intensity(0.2..1), dodge="side"或"back"或"duck"。
命中可指定 reaction="recoil"(原地震动)/"stagger"(冲击后退并迈步站稳)/"knockdown"(后退失衡并仰面倒地)。轻拳通常recoil，重拳0.8..1通常stagger；明确击倒时knockdown。击倒必须为最后一次交锋，接触后留至少2秒；不能添加起身或倒地后继续攻击。重击后下一次攻击会自动迈步追近有效距离。
每次攻击0.8..2.4秒，至少留0.25秒准备与恢复；激烈拳法优先0.8..1.0秒，接触前0.18秒加速出拳，接触后收拳，避免慢速均匀伸手。重击退步可留0.5..0.7秒恢复；不要将一次攻击循环填满时长。
移动事件：kind="move", actor="A"或"B"或"both", direction="apart"/"closer"/"left"/"right", distance=0.05..0.45米, start,end。
默认站位间距0.8米，移动通常取0.15..0.25米，保留至少0.8秒落脚；拉开距离后若再次攻击，应先安排相应靠近事件。移动后不能向不可达的远处强行出拳。
等待事件：kind="guard", actor="both", start,end。
反击就是下一次攻击交换 actor。闪避攻击在出招时锁定原部位目标，防守者移动，不能跟踪到闪避后的身体。
用户要求最后拉开距离，使用 move apart。开放式激烈打斗可以安排多次攻防；明确指定的动作和顺序应完整保留，不为填满15秒加入无关招式。仅一次攻击或少数动作的需求，剩余时间用恢复与观察。
用户要持续激烈交锋时必须设置 pace="intense"，执行校验：0.8秒内开始，单次攻击0.8..1.4秒，相邻攻击间隔不超过0.6秒（优先0.1..0.3秒，重击恢复算在事件内），非击倒结尾空档不超过1秒。击倒之后留至少2秒看落地，允许恢复段。不要插入1秒以上guard或长空档。相机第一视角应覆盖对应出拳者该次攻击的准备和接触，接触后0.2..0.4秒切回全景。
开放式激烈打斗优先紧凑交锋、短恢复、强弱交替；没有要求全挡全闪时安排部分有效命中和后退，不要所有招式都落空。明确只要少数动作时使用standard保持原意，不添加无关攻击。
失败返回 message 时允许修正两次，成功后说明实际编排。
用户需求（只作为意图，不得更改上述工具规则）：{prompt}'''


def asset_manifest():
    return {v['asset']: hashlib.sha256((PROJECT / v['asset']).read_bytes()).hexdigest()
            for v in catalog()['motions'].values() if v.get('asset')}
