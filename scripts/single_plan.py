"""Async single-actor dispatch around the existing animation/render implementation."""
import json
from duel_plan import PROJECT, atomic_json, job_directory, number
from locomotion_plan import validate_locomotion, validate_locomotion_coverage
from camera_plan import validate_camera


def validate_single(raw, job_id):
    if not isinstance(raw, dict) or raw.get('version') != 1:
        raise ValueError('单人计划需要 version=1')
    if any(key in raw for key in ('interactions','interaction','events','objects')):
        raise ValueError('物品与interactions必须提交到wm_scene_plan的scene，不能写进wm_single_plan；人物计划不会执行这些字段')
    unknown = set(raw) - {'version', 'job_id', 'animations', 'duration_seconds', 'frame_count', 'fps',
                          'camera_policy', 'pitch_deg', 'distance_ratio', 'fov', 'locomotion', 'camera', 'render_style'}
    if unknown:
        raise ValueError('单人计划存在未执行字段：' + '、'.join(sorted(str(key) for key in unknown)))
    if raw.get('render_style', 'white_model') != 'white_model':
        raise ValueError('render_style只支持white_model现有白模渲染，不支持其它风格或精确色值')
    if 'fps' in raw and (type(raw['fps']) not in (int, float) or raw['fps'] != 30):
        raise ValueError('单人计划只支持30FPS')
    assets = raw.get('animations')
    if not isinstance(assets, list) or not 1 <= len(assets) <= 16:
        raise ValueError('需要 1 至 16 个真实动作资源')
    for asset in assets:
        if not isinstance(asset, str):
            raise ValueError('动作资源路径必须是文本')
        path = (PROJECT / asset).resolve()
        if not path.is_relative_to(PROJECT / 'mixamo_fbx') or path.suffix.lower() != '.fbx' or not path.is_file():
            raise ValueError('动作路径不存在或越界')
    policy = raw.get('camera_policy', 'fixed')
    if policy not in ('fixed', 'orbit'):
        raise ValueError('单人镜头支持 fixed 或 orbit')
    duration = number(raw.get('duration_seconds', 6), '时长', 1, 30)
    if 'frame_count' in raw and (type(raw['frame_count']) is not int or raw['frame_count'] != round(duration * 30)):
        raise ValueError('frame_count必须与明确时长和30FPS一致')
    if duration > 15:
        # Only a saved, independently validated passage scene enables the
        # longer specialized chain. This applies to helper submission, worker
        # resume and the door CLI without trusting a flag in the person plan.
        if 'locomotion' in raw:
            raise ValueError('通用路线移动时长仍为1至15秒，不能借穿门场景扩大上限')
        from scene_plan import load_scene
        scene = load_scene(job_directory(job_id), job_id, 1, duration)
        if not scene or not any(event['action'] == 'pass_door' for event in scene['interactions']):
            raise ValueError('单人时长超过15秒仅限已保存、同一时长且通过验证的pass_door穿门场景；其它单人动作仍为1至15秒')
    plan = {'version': 1, 'job_id': job_directory(job_id).name, 'animations': assets,
            'render_style': 'white_model',
            'duration_seconds': duration, 'frame_count': round(duration * 30), 'fps': 30,
            'camera_policy': policy, 'pitch_deg': number(raw.get('pitch_deg', 0), '俯仰', -80, 80),
            'distance_ratio': number(raw.get('distance_ratio', 3.5), '距离比例', 1.2, 6),
            'fov': number(raw.get('fov', 50), '视角', 20, 90)}
    if 'camera' in raw:
        # Keep only harmless legacy defaults for old readers and reloads. A
        # second explicit, non-default placement must never be silently lost.
        defaults = {'camera_policy': 'fixed', 'pitch_deg': 0, 'distance_ratio': 3.5, 'fov': 50}
        if any(key in raw and raw[key] != value for key, value in defaults.items()):
            raise ValueError('新camera不能与非默认camera_policy/pitch_deg/distance_ratio/fov混用；请只用camera明确镜头要求')
        plan['camera'] = validate_camera(raw['camera'])
        if plan['camera'].get('reference') == 'actor' and plan['camera']['target'] != 'A':
            raise ValueError('单人camera人物参考target只允许A')
        if plan['camera'].get('reference') == 'object':
            from scene_plan import load_scene
            scene = load_scene(job_directory(job_id), job_id, 1, duration)
            if not scene or plan['camera']['target'] not in {obj['id'] for obj in scene['objects']}:
                raise ValueError('camera.target必须引用已保存场景中的真实物品')
    if 'locomotion' in raw:
        plan['locomotion'] = validate_locomotion(raw['locomotion'], duration)
        validate_locomotion_coverage(None, None, plan)
        # Reject impossible distances and turns while Hermes can still amend
        # an unsaved plan in the same session. Static geometry is checked again
        # by the scene executor with the actual room and obstacle bounds.
        from locomotion_path import compile_timeline
        compile_timeline(plan['locomotion'], duration, fps=30)
    return plan


def write_single(job_id, raw):
    plan = validate_single(raw, job_id)
    folder=job_directory(job_id)
    cast_path=folder/'cast.json'
    if cast_path.exists():
        from scene_cast import validate_cast
        from scene_plan import load_scene
        from scene_intents import validate_intent_coverage
        cast=validate_cast(json.loads(cast_path.read_text()),job_id)
        if not cast['supported']: raise ValueError(cast['message'])
        scene=load_scene(folder,job_id,1,plan['duration_seconds'])
        validate_locomotion_coverage(cast,scene,plan)
        if 'object_intents' in cast:
            validate_intent_coverage(cast,scene,plan)
    path = folder / 'single_plan.json'
    if path.exists() and json.loads(path.read_text()) != plan:
        raise ValueError('任务已有不同单人计划')
    atomic_json(path, plan)
    return plan


def single_planning_prompt(prompt, job_id):
    assets = '\n'.join(str(p.relative_to(PROJECT)) for p in sorted((PROJECT / 'mixamo_fbx').rglob('*.fbx')))
    return f'''单个角色的自然语言任务使用已有动作素材。只通过 MCP execute_code 调用 print(bpy.wm_single_plan("{job_id}", plan))，ok=true后结束，不调用wm_clear/import/camera/render，不执行shell，不等待视频。
内部计划：version=1, animations=[真实资源路径...], duration_seconds普通单人为1..15（未指定默认6；单人翻窗未指定建议8..10秒）；仅已保存且验证通过、包含pass_door的单人场景可1..30，完整开门→穿门→关门链未指定建议20秒。通用locomotion仍1..15，不借门链扩展；camera_policy="fixed"或"orbit", pitch_deg=-80..80, distance_ratio=1.2..6, fov=20..90。用户明确指定时长必须保留，不得擅自延长。
规范计划固定保存render_style="white_model"，仅表示当前统一clay白模执行器。白模视频要求可引用该真实字段；不代表精确灰RGB、衣服、材质细节、背景或音频风格，未支持的明确要求仍保留并反馈。旧保存计划缺字段时不能凭新默认值伪造旧计划执行证据。
旧镜头字段保留兼容；需要明确斜侧/方位/俯仰、相机位置、看向目标或透视时提交新的camera对象，不能只提交camera_policy=fixed并声称有角度。camera={{"version":1,"reference":"object","target":"window_1","azimuth_deg":-45,"elevation_deg":15,"projection":"orthographic","framing":"full_body","fov_deg":50,"occlusion_policy":"transparent_architecture"}}；target必须是已保存场景中的真实id。reference支持world/object/actor，object以固定框yaw为参考，actor只用A实际首帧朝向；world无target。azimuth=-180..180，0表示参考正前方(-Y)、90右侧(+X)、-90左侧(-X)、180背后(+Y)，是相机所在方向，不能套用旧双人角度约定；elevation=-80..80。projection只能orthographic或perspective；framing仅full_body；fov_deg=20..90，指定透视视场角应使用perspective。相机固定不随人物转向跟拍。
明确世界坐标可用camera.position=[x,y,z]与可选look_at=[x,y,z]，坐标每轴-30..30米；position不能同时提交reference/target/azimuth_deg/elevation_deg。look_at也可配方向模式；明确位置/方向不会自动换角、后移，构图或遮挡不能满足时拒绝。方向模式可自动拟合距离/全身构图。occlusion_policy=transparent_architecture用于半透明建筑白模参考，实体碰撞保留；严格不透明要求用strict，无法无遮挡时拒绝而非偷改透明度。
新camera与外层旧字段不可双重控制，省略旧字段或只保留兼容默认fixed/0/3.5/50；非默认旧pitch/fov/distance/orbit会拒绝，不能丢弃用户参数。新固定camera支持普通单人、静态布景、交互、翻窗、门链和路线；placement不支持环绕动画、特写、动态跟拍和多分镜。普通单人无新camera的旧orbit流程仍保留，但不能将旧orbit与新camera混用。未支持要求写入requirements并在独立复核unsupported，无法执行的任意新动作不能删。用户要求全身采用framing=full_body；没有明确镜头要求可使用旧fixed/0/3.5/50。动作意图、顺序和资源由你解释，不要求用户选文件。现有单人多动作使用原有NLA混合与等长重定时，不保证任意组合自然。
需要通用走跑、先走再跑、转弯、原地转身、起步停步，或走到指定地面位置/某物品附近而不操作物品时，必须提交single_plan.locomotion并在cast标记requires_locomotion=true。普通Walk/Run素材列表不能覆盖目标路线、速度顺序、转弯或停在目标位置。只有明确原地展示动作、不要求真实地面移动时，才使用旧素材轮播。用户不需要写路径或内部字段，坐标、路线、时间和步态由你理解原文后规划。
locomotion={{"version":1,"start_position":[0,2],"start_yaw_deg":0,"segments":[{{"start":0,"end":2.5,"gait":"walk","waypoints":[[0,2],[0,0.8]]}},{{"start":2.5,"end":5,"gait":"run","waypoints":[[0,0.8],[0,-2]]}},{{"start":5,"end":6,"gait":"idle"}}]}}。该示例只是字段结构，必须按用户要求改路线、顺序、时长，不能套为固定动作。yaw=0朝-y，90朝+x，-90朝-x，180朝+y；向前路线须与人物朝向一致。初始位置和全部waypoints为[x,y]米，每轴-6..6；segments1..16，时间按序不重叠，每段至少0.5秒，end不得超过总时长。允许时间空档，人物保持原地；不要通过时间空档偷偷改变位置或添加招式。
gait支持walk、run、idle、turn。walk/run必须有2..8个waypoints，首点等于当前人物位置，末点成为下一段起点，不能瞬移，相邻点不能相同；idle无waypoints、无end_yaw_deg；原地turn使用end_yaw_deg=-180..180且无waypoints。除这些字段外不能添加未执行的速度、目标、身体动作或转身字段。移动速度由路线和时长决定，后端据实际路线检查速度、加速度和转向；不可行计划报错，不能擅自修改明确用户时间。
步行上限1.65米/秒，跑步上限3.5米/秒，加减速上限3.8米/秒²；原地转身使用平滑加减速，90度至少1.125秒，180度至少2.25秒。向前移动的起始朝向必须对齐路线；反向路线会先转身并消耗该移动段时间。用户未指定路程时应在总时长内选择可行的短距离，不擅自增长路线。用户要求停住/站稳时安排至少0.5秒idle；不通过瞬移收脚。提交helper会在保存前检查时间可行性，ok=false时根据报错修正内部路线或用户未指定的时间分配再提交，不结束任务或删掉关键动作。
本轮路线执行以最终站稳结束；尾部可见静止不足0.4秒时，从最后活动原时段内部预留缺额收脚，实际到点/转身完成时刻另记执行计划。不改变总视频时长；若用户明确要求持续移动直到最后一帧，该边界目前不能满足，保留要求并说明限制。
中间waypoints是路线引导点，为平滑转弯可在不超过0.35米范围内调整；必须准确到达的位置应设为一个移动段的末点，再从该位置开始下一段。段末点不会用转角平滑替代。
通用路线移动animations仅使用真实Idle.fbx/Walk.fbx/Run.fbx身体来源，按路程推进步相并混合步态，路线和顺序来自locomotion，不来自animations轮播。新camera可明确固定方位、俯仰、投影或世界position/look_at，并以full_body全程构图；无新camera时使用旧fixed/0/3.5/50。可带房间和静态物品，但scene.interactions必须为空；走到桌旁不等于扶桌，走近门不等于开门或穿门，走近窗不等于翻窗。通用移动与开关/触摸/翻窗/跳跃/拳脚串联尚未接入；显式操作仍保留全部object_intents并反馈限制，不要删除需求。翻窗事件中的walk_run/run/walk接近由原翻窗执行器完成，不能同时提交locomotion。
requires_locomotion仅表示独立通用路线，不按走跑关键词设置。扶桌、开关门窗事件本身已有走近；例如“走到窗前打开窗户”由open_window完成接近与开窗，不提交locomotion且requires_locomotion=false。vault_window的approach及双人move同理使用各自原协议。专用接近不当作本轮未支持的通用串联，只有额外独立路线/走跑切换/转身停步超出专用事件接近时才保留为独立要求。
单人开门→穿门→另一侧关门由scene.interactions的open_door/pass_door/close_door专用链执行，requires_locomotion=false、不提交locomotion；人物animations只用Idle/Walk。pass_door.direction=outward是固定门框inside到outside，inward相反，pass至少3秒且门已open。开关门door_side仅含pass的链可用，缺省按链推导，不能把另一侧关门规划到原侧。最多一门一次穿越，全部交互仅该门开/过/关，不能普通Walk加静态门替代穿越。无新camera时镜头使用门链旧世界+X固定正侧剖视全景；用户指定固定镜头要求使用上述camera对象，不用默认值证明斜角，透明白模与严格不透明分别按occlusion_policy执行；完整链未指定建议20秒，专用门链总长1..30；明确总长（包括15秒）保持原值，安全时间不足拒绝；此能力不包含跑步穿门、楼梯坡面、跨房间导航、拳脚、多人物品交互或其它独立动作串联。
穿门链的开关接触近铰链真实双侧横拉杆，contact_front/contact_back随门扇变化，不能承诺原far handle在80度全角可达。未指定门宽与开角时在scene显式推荐size=[1.3,0.14,2.1]、open_angle_deg=80，旧资产默认与明确用户尺寸不改；不满足实际身体净空时拒绝。完整20秒链未指定节奏先分开门0..6秒、穿门6..14秒、关门14..20秒，auto按实际安全时间在原总长内调整，指定时刻保持exact，safe时间不足反馈而非压成高速穿越。
穿门链未指定手侧时优先left，scene规范化也缺省left；旧无pass交互仍缺省right。明确用户right保留并由执行器检查，不能为通过验收改手侧。
已核实 Kicking 有较大位移，原地踢击优先Mma Kick。多个意图选择多个相关资源，不增加无关招式。
普通Walk/Run/Jump素材轮播只提供各自动作姿态，静态布景不提供目标路径。要求跑向窗户再翻越时，关键意图是穿过窗洞到另一侧，必须由scene.interactions中的vault_window事件执行。翻窗animations仅使用Idle/Walk/Run身体来源；不能用Jump片段替代撑窗、穿越和落地。先走后跑使用该事件approach=walk_run，walk_fraction控制接近段步行比例；仅跑步或仅走路分别run/walk。翻窗第一版为唯一物品交互与最后动作，不混入拳脚或显式开关操作。未指定状态的默认闭窗仅在视频开始前设为打开，属于初始净空准备并写执行日志，不表演无人开窗；明确保持关闭、撞碎玻璃或先开窗再翻越均不能用此替代。
可用资源：\n{assets}\n用户需求：{prompt}'''
