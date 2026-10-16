"""Optional calibrated scene recipes callable by the general Hermes director.

Recipe errors are tool feedback, not a verdict about the user's request.
The director may repair the recipe or build freely with Blender's Python API.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from duel_plan import PROJECT, atomic_json, job_directory, validate_plan
from single_plan import validate_single
from scene_plan import validate_scene

_RUNNING = {}


def render_runtime_info():
    """Report local resource facts without changing requested rendering."""
    cores = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count()
    quota = None
    try:
        limit, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if limit != 'max':
            quota = int(limit) / int(period)
    except (OSError, ValueError, ZeroDivisionError):
        pass
    renderer = None
    try:
        import gpu
        renderer = gpu.platform.renderer_get()
    except (ImportError, AttributeError, RuntimeError):
        pass
    return {
        'renderer': renderer, 'visible_cpu_cores': cores, 'cpu_quota_cores': quota,
        'software_graphics': bool(renderer and any(word in renderer.lower() for word in ('llvmpipe', 'softpipe', 'software'))),
        'preview_advice': '代表帧用wm_agent_preview后台快照渲染，勿在MCP同步渲染多帧。白模可用WORKBENCH的STUDIO/MATERIAL及FXAA检查清晰外形；明确材质/引擎要求仍保留。实际视频质量以渲染帧为准。',
    }


def _quality_summary(quality):
    result = {key: quality[key] for key in ('passed', 'kind', 'frame_count', 'scope') if key in quality}
    result['failures'] = {key: value[:5] for key, value in quality.items()
                          if key.endswith('_failures') and isinstance(value, list) and value}
    camera = quality.get('camera_quality', quality.get('camera_placement', {}))
    result['camera'] = {key: camera[key] for key in
                        ('passed', 'occlusion_failure_count', 'minimum_body_margin') if key in camera}
    result['camera_applied'] = quality.get('camera_facts', quality.get('camera_bounds', {})).get('applied')
    result['failed_contacts'] = [item for item in quality.get('contacts', []) if not item.get('passed', True)][:5]
    return result


def _folder(job_id):
    folder = job_directory(job_id)
    if not (folder / 'request.json').is_file():
        raise ValueError('任务不存在，请使用当前任务job_id')
    return folder


def reference_info():
    from camera_plan import camera_capabilities
    return {
        'purpose': '素材优先的混合运动：合适素材驱动全身主动作，动作间平滑衔接，IK局部修正接触；缺少的动作才同骨架生成。组件不接受的动作、人数或时序不代表整条提示词不能生成，可以直接execute_code使用bpy构建。',
        'animation_assets': [str(p.relative_to(PROJECT)) for p in sorted((PROJECT / 'mixamo_fbx').rglob('*.fbx'))],
        'additional_assets_root': str(PROJECT / 'assets'),
        'knowledge_root': str(PROJECT / 'knowledge'),
        'render_runtime': render_runtime_info(),
        'robot_workflow': {
            'default_character': 'beta',
            'character_catalog': str(PROJECT / 'knowledge/characters/catalog.json'),
            'motion_catalog': str(PROJECT / 'knowledge/motions/catalog.json'),
            'tool_documentation': str(PROJECT / 'docs/机器人混合动作工具说明.md'),
            'character_tools': {
                'info': 'bpy.wm_agent_character_info()：紧凑真实资产信息',
                'ensure': 'bpy.wm_agent_actor_ensure(job_id, actor_id, position=[0,0,0], yaw=0, color=[.82,.82,.84,1])：位置米、朝向弧度；重复调用保持正确动画。返回armature_object/root_object/mesh_objects。',
                'query': 'bpy.wm_agent_actor_info(job_id, actor_id)：返回真实对象和bone_roles',
                'list': 'bpy.wm_agent_actor_list(job_id)：查看当前任务实际登记人物、可见性和缺失对象；修复沿用真实actor_id，避免重复人物。',
                'remove': 'bpy.wm_agent_actor_remove(job_id, actor_id)：按实际所有权删除指定人物及其登记/时间线，保留其他人物和物品；删除错误重复人物前确认要保留的正确人物。',
                'adopt': 'bpy.wm_agent_actor_adopt(job_id, actor_id, armature_name, root_name=None)：登记recipe中的真实Beta；检查原蒙皮/rest身份。',
            },
            'motion_tools': {
                'search': 'bpy.wm_agent_motion_search(完整动作意图, limit=8)：返回候选；无候选仍允许同骨架生成。',
                'info': 'bpy.wm_agent_motion_info(motion_id)：路径、源FPS/窗口、已校准信息和真实hash。',
                'library': 'bpy.wm_agent_motion_apply(job_id, actor_id, {"event_id":"walk_1","motion_id":"Walk","start_frame":1,"end_frame":60,"root_strategy":"source","time_scale":1,"fit":False,"blend_in_frames":6})：登记指定段并烘焙时间线。按实际源时长选结束帧，不把短动作默认fit拉满几秒。',
                'sequence': 'MCP普通import agent_motion_sequence.library_sequence_events(job_id, actor_id, event_id, segments, start_frame=1, transition_frames=6)：segments指定实际motion_id、可选秒制source_window/time_scale。默认原速和完整素材，旧决斗短窗仅返回建议，不自动裁掉恢复；按实际源时长安排真实重叠过渡，返回开放plan.events和时序摘要。按摘要设置真实场景帧范围，登记完整序列后统一commit。它不把自然语言限制成动作枚举，不自动证明步态相位或接触匹配。',
                'event': 'bpy.wm_agent_timeline_event(job_id, actor_id, event_id, event)：保存开放语义的运动方案，source=library/procedural/hybrid是来源分类。随后motion_commit实际执行。若覆盖自动保全的窗口外快照且没指定priority，新明确编辑自动提高优先级；priority_diagnostic返回effective_priority及shadowed_by_preserved_base，显式priority原样保留。',
                'commit': 'bpy.wm_agent_motion_commit(job_id, actor_id)：组合已登记事件并烘焙完整通道；多个相关人物使用actor_id=["A","B"]，先计算基础姿态，再逐帧按实际依赖顺序求同帧IK并统一写回。先A打B再B反击可以交换顺序；真正同帧循环在修改动画前指出错误帧。dynamic_commit_order/dependency_windows描述实际调度。bpy直接构建后用capture=True, frame_start=..., frame_end=..., event_id=...捕获实际求值姿态；capture仍逐人操作。局部capture保留其他既有帧，actual_bake_range是实际保全范围。',
                'locomotion': 'bpy.wm_agent_locomotion_apply(job_id, actor_id, path, event_id="locomotion_base", intent="原文走跑停要求")：可选已校准平地步态。path连续逐帧{frame,root:[世界米],yaw:弧度,gait:"walk"/"run"/"idle"}；一个相位时钟驱动原Idle/Walk/Run与双足IK，保存同骨架完整姿态并保全窗口外运动。刹车阶段仍用移动步态，真实根速度逐步减到零，留足落脚站稳时间。后来叠加挥手等局部层时读取本事件priority并设更高priority。仅支持世界Z=0、无parent/单位scale/竖直root；其他需求自由bpy实现，不限制提示词。',
                'timeline': 'bpy.wm_agent_timeline_info(job_id, actor_id)：查询已登记段，稳定event_id替换局部计划，保留其他段。',
                'controls': 'bpy.wm_agent_actor_controls(job_id, actor_id)；wm_agent_controls_match匹配当前FK末端，设置目标及IK权重后可capture烘焙。',
                'check': 'bpy.wm_agent_motion_check(job_id, actor_id=None, check_camera=True)：真实几何与通道检查，默认复查已登记接触窗口及ground_motion的实际皮肤对地要求；empty表示没有登记该类要求，不能当作原文要求已满足。看passed与实际误差，不认证自然度。',
                'pov': 'bpy.wm_agent_camera_pov(job_id, actor_id, target_actor_id, start_frame, end_frame, fov=75, return_camera="Camera", shot_id="B_counter")：从actor_id的真实头骨眼位观察target_actor_id，烘焙镜头并按帧切回全景；修复保留shot_id，运动修改后重新调用。原身体不拆分，头颈仅在该窗口渲染遮罩，必须检查实际出拳手臂与目标可见。重叠窗口返回真实冲突，请安排不重叠的实际镜头。',
                'full_camera': 'bpy.wm_agent_camera_full(job_id, {"reference":"world","azimuth_deg":60,"elevation_deg":20,"projection":"perspective"}, actor_ids=None, focus_objects=None)：复用已校准固定镜头，按真实人物动作范围构图并保持方向；focus_objects支持实际MESH名及{"id":"chair","objects":["实际椅座","实际椅背","实际椅腿"]}逻辑物品组。整组几何入框，组内正常互挡不误判为整物品缺失，外墙遮挡仍检查。拟合阶段最多12代表帧含首尾，visibility.helper_sample_frames/helper_sample_scope只证明这些帧；backend_full_frame_visibility_required=True表示仍须后台独立全片检查。相机不改变角色动画。',
                'camera_check': 'bpy.wm_agent_camera_check(job_id, requirements=None, frame_start=None, frame_end=None, detail=False)：requirements非None先登记场景镜头需求，再检查真实active camera、求值skin/关注物品入框及不透明物遮挡；None复查已登记需求，缺登记返回missing。默认全片；长视频可frame_start/frame_end分段检查并如实说明scope，不把局部检查称全片认证。passed才表示指定几何检查通过，ok只表示检查调用完成；scope与实际预览另看。新提交保存camera_visibility_version=1，后台重载独立检查所有帧，失败交Hermes保留动作修复后重提交。',
                'preview': 'bpy.wm_agent_preview(job_id, frames=[1,60,90], resolution=[640,360])：保存独立快照并异步headless渲染真实代表帧，返回preview_id；随后wm_agent_preview_status(job_id, preview_id)查询。不要在MCP内同步render多帧或长时间循环等预览。',
            },
            'camera_visibility_requirements': {
                'version': 1,
                'shots': [{'camera': 'ActualFullCamera', 'frame_start': 1, 'frame_end': 600,
                           'actor_ids': ['A'], 'focus_groups': {
                               'chair': ['ActualChairSeat', 'ActualChairBack', 'ActualChairLeg1',
                                         'ActualChairLeg2', 'ActualChairLeg3', 'ActualChairLeg4'],
                               'door': ['ActualDoorPanel']},
                           'framing': 'full_body', 'margin': .04, 'fixed_transform': True}],
            },
            'camera_visibility_usage': '示例对象名、600帧和fixed_transform只示范形状，按原文及真实场景替换。focus_groups按逻辑物品纳入全部真实MESH部件，不只登记座面漏椅腿或省略门；整组投影入框，允许组内正常互挡，仍检查外墙等遮挡。focus_objects旧字符串列表仍适用于单独物件。全景用full_body；近景/POV用regions，精确可选head/trunk/left_arm/right_arm/left_hand/right_hand/left_foot/right_foot。多个shot可在同帧对不同人物/区域作检查，不用全身规则误判第一视角。检查成功不证明手掌贴合、自然度或每像素无遮挡。',
            'camera_optional_constraints': '按原文选择fixed_transform、expected_position:[世界米]、look_at:[世界米]、expected_azimuth_deg、expected_elevation_deg、reference_yaw_deg、expected_fov_deg、expected_projection；azimuth/elevation须同时提供look_at世界参考目标，否则登记报错。未指定不强迫固定或某个角度。明确数字不能为通过检查静默改动。模糊斜侧/略俯允许Hermes调整机位或镜头以显示目标；不可偷改人物、物品运动、隐藏/删除墙体或改成透明，除非用户明确允许cutaway。完成动画→登记/检查镜头→实际预览→提交；重载失败保留动作反馈修复。',
            'response_size': 'timeline_info/motion_check默认返回紧凑真实摘要；detail=True返回全量。修改既有事件先取timeline_info(..., detail=True)，不要将缺少keyframes/targets/root_path的紧凑摘要回写为完整事件。完整关键帧与来源在场景中持久保存，勿把所有每帧姿态/曲线数据反复打印注入模型。',
            'procedural_event_format': {
                'source': 'procedural', 'start_frame': 61, 'end_frame': 90,
                'mask': ['mixamorig:RightArm', 'mixamorig:RightForeArm', 'mixamorig:RightHand'],
                'root_strategy': 'hold', 'blend_in_frames': 6, 'blend_out_frames': 6,
                'keyframes': [{'frame': 61, 'pose': {'mixamorig:RightArm': {
                    'location': [0, 0, 0], 'rotation_quaternion': [1, 0, 0, 0], 'scale': [1, 1, 1]}}}],
            },
            'procedural_example_note': '示例只说明JSON形状，四元数不是经过验收的招手姿态。按真实bone_roles与休止轴构建准备/动作/恢复关键帧；用户输入保持自然语言。',
            'root_strategy': 'source提取并累计素材平面根运动；trajectory使用root_path=[{"frame":61,"root":[0,-1.2,0]}]，root坐标为米；hold保持空间状态。source_window=[起点秒,终点秒]；root_trajectory/source_window_frames不是执行字段。masked overlays不独占根。循环走跑用loop=True、fit=False保持源节奏；减速要求实际根速度和刹步支撑，不是仅切Idle+hold。',
            'support': '现有走跑/翻窗/门窗/双人recipe可复用；完整build/load仅用于整场构建，不作为追加动作工具。',
            'held_props': '手持/搬运/推拉/接触物品须通过真实目标绑定、骨骼parent或约束保证接触。IK先求值再烘焙完整姿态，正确烘焙后可mute以免二次求解；禁止未求值便关IK或仅让手与物品各自独立运动后声称接触。通过targets.binding和contact窗口登记接触，再检查实际末端与目标及保存重载结果；约束开关不是完成证据。为物品设置prop_id或wm_object_role="prop"；身体保持原Beta蒙皮，附属物不作为人物身份几何。',
            'bound_contact_targets': {
                'example': {'RightHand': {'binding': {'actor_id': 'B', 'bone': 'Head', 'local_point': [0, 0, 0]},
                              'effector_point': [0, 0, 0],
                              'keyframes': [{'frame': 21, 'influence': 1}, {'frame': 25, 'influence': 1},
                                            {'frame': 31, 'influence': 0}],
                              'contact': {'start_frame': 21, 'end_frame': 25, 'tolerance_m': .03, 'kind': 'strike'}}},
                'binding': '可用{object:真实物体名,local_point:[3]}、{actor_id:目标人物,bone:实际骨名或唯一短名,local_point:[3]}或{object:真实mesh名,vertex_index:整数}。目标按同帧evaluated变换/蒙皮求值。',
                'effector': 'effector={object:自身真实mesh名,vertex_index:整数}检查真实蒙皮点；effector_point是末端骨局部偏移，默认零只测骨起点。骨局部使用导入骨坐标单位，目标物体局部使用该物体单位，经matrix_world变为世界米。',
                'pole': 'pole为世界米；pole_binding可按相同绑定格式跟随稳定的链外骨或物体，pole_angle为弧度。护架及出收拳保持一致的肩部pole，避免移动时肘钉在旧世界点或阶段切换翻肘。',
                'scope': '示例只说明格式，Head零偏移不是实际头部表面；需从实际几何选择拳面/掌面与目标表面。绑定默认全事件接触，contact可限定实际窗口；护架等仅跟随姿态可设contact=False，不登记触碰要求。收招后influence=0解除IK。',
                'coordination': '相关演员事件先登记，motion_commit(job_id,[actor_ids])统一协调；已登记接触默认由motion_check和独立重载探针复查。',
            },
            'interaction_helpers': {
                'module': 'agent_interactions',
                'functions': ['guard_events', 'hand_strike_events', 'recoil_events',
                              'support_step_events', 'register_interaction', 'verify_interaction'],
                'purpose': '素材优先主运动完成后，用这些工具修接触或补充缺少的动作；不要用程序化护架/攻击默认替换合适的素材全身运动。不重建场景或选择相机，通过MCP execute_code普通import，完整签名可用inspect.signature查看。',
                'guard': 'guard_events(job_id, actor_id, event_id, start_frame, end_frame)：只生成双手护头与基本握拳，默认首帧完成准备，双手随自身头部移动；不改变腿或根节点。先登记并commit基础姿态，再编译依赖当前姿态的攻击和脚步。',
                'strike': 'hand_strike_events(job_id, actor_id, event_id, target_binding, start_frame=..., impact_frame=..., end_frame=..., hand="RightHand", outcome="hit")：从实际护架生成准备、肩胯参与、1至2帧接触和收招。hit目标为真实身体表面；block绑定实际格挡部位或物体；miss用aim_offset_world偏置擦过并实测擦过点净距，不登记命中。左右侧与时刻按原文。approach_required表示需要先接近后重新编译，不拉长手臂。',
                'reaction': 'recoil_events(..., impact_frame=..., end_frame=..., direction_world=[3], amount=.5, contact_id=实际命中ID)：生成接触之后的头躯干反应。outcome为block/miss时不生成受击事件。它不自动平移根节点；重击后退另配support_step_events。',
                'steps': 'support_step_events(job_id, actor_id, event_id, steps, root_path)：steps为{foot:"RightFoot",lift_frame:...,land_frame:...,position:[实际足底世界米]}列表，左右交替；root_path为{frame:...,root:[世界米]}列表。平地默认floor_z=0，真实足底支撑、抬脚弧线及有限髋部降低，不倒放前进Walk。',
                'workflow': '生成helper只返回plan。print(register_interaction(job_id,plan))登记事件并保存紧凑检查要求；同event_id替换会清理该计划旧的多余子事件。相关人物统一motion_commit，再print(verify_interaction(job_id))复查实际结果。检查要求随scene保存，跨MCP调用无需保留局部plan变量；也兼容手工timeline_event和verify_interaction(job_id,plans)。不要打印整份每帧plan.events。',
                'additional_modules': {
                    'agent_leg_interactions': {
                        'functions': ['leg_strike_events'],
                        'usage': 'leg_strike_events(job_id, actor_id, event_id, target_binding, start_frame=..., impact_frame=..., end_frame=..., foot="RightFoot")：原Beta平地前踢，抬膝、伸击、收腿和落地，另一脚固定支撑。左右脚和斜向可变；先commit护架、走停和目标基础运动再编译，改变基础运动后重新编译。approach_required先接近，contact_release_required先释放攻击脚旧支撑；持续行走或移动支撑不能直接当静止前踢处理。不表示支持任意踢法。',
                    },
                    'agent_contact_interactions': {
                        'functions': ['contact_events'],
                        'usage': 'contact_events(job_id, actor_id, event_id, target_binding, start_frame=..., contact_frame=..., release_frame=..., end_frame=..., chain="RightHand", kind="hold")：从当前链姿态平滑接触、持续跟随、释放回当前基础动作。kind是开放语义，不限定提示词动词；物品轨迹及人物根/脚步另行编排并先commit，同一物体多个握点可供多人/双手分别绑定。不创建人物、物品或相机；修改基础动画后重新编译。',
                    },
                    'agent_ground_motion': {
                        'functions': ['ground_motion_events', 'check_ground_motion'],
                        'source_candidates': [str(PROJECT / 'rokoko_staging/mixamo/KnockOut_Loser_mixamo.fbx'),
                                              str(PROJECT / 'rokoko_staging/mixamo/GetUp_WipeBloodfromMouth_MIXAMO_769.fbx')],
                        'usage': 'ground_motion_events(job_id, actor_id, event_id, source_asset=实际本地FBX路径, source_window=[起秒,止秒], start_frame=..., end_frame=..., expect_standing=False)：将单人地面动作先验按实际rest空间映射到原Beta，源Hips可为骨或Armature对象；输出普通开放事件并校正地面。起身结束用expect_standing=True并留settle_frames。supports可按实测指定短手脚支撑窗口，抬身后及时释放。登记commit后check_ground_motion量测实际全身皮肤对地、支撑及可选末尾站姿，motion_check和后台重载也默认复查。仅验证本地兼容源窗口，不等于通用动力学击倒。',
                    },
                },
                'scope': '校准Beta、平地上肢、基础前踢、交替短步、接触与本地地面动作先验；属于可选运动工具，用户提示词仍完整交给Hermes，其他动作继续自由FK/IK。几何通过不代表自然度或真实力学。',
            },
            'scene_lighting': 'bpy.wm_agent_lighting(job_id=None, force=False)：为当前EEVEE/Cycles场景补齐实际可渲染照明（World+三点光），清空场景或自建场景后必须确保有照明。EEVEE/Cycles场景若既无可见灯光、World又为空或全黑、也无自发光，成片会全黑；后台探针会把这种完全无照明的场景判为错误并要求修复，保存时框架也会兜底补默认照明并在warnings记录。也可自行用bpy.data.lights新建AREA/SUN并设置scene.world。',
            'recipe_pov': '新固定机器人任务复用双人recipe时先用camera.policy=fixed_full构建，再adopt/capture，最后用camera_pov加第一视角。旧cinematic相机可能复制拆分身体，与新人物身份不兼容。全身camera检查仅用于全景帧范围；第一视角另检查本人手臂和对方。',
            'direct_edits': '普通commit重放已登记事件；直接写Action/FK/IK后要capture=True保存实际成果。capture保存已经合成的最终通道，不再次叠加被包含的旧低优先级FK/IK。其他自行生成的全骨完整最终姿态可用final_pose_layers=True，必须实际包含所需姿态；更高优先级新层仍可追加。时间、FPS、分辨率先设置，再登记和烘焙。',
        },
        'rig_geometry_helper': {
            'module': 'agent_rig_geometry', 'function': 'attach_rest_proxy',
            'path': str(PROJECT / 'scripts/agent_rig_geometry.py'),
            'usage': 'from agent_rig_geometry import attach_rest_proxy; mesh = attach_rest_proxy(armature, bone_names=None, radii=None, material=None)',
            'purpose': '可选休止姿态局部网格+Armature modifier，保留原骨架及动画，避免world-space与bone-parent重复变换。',
            'scope': '隔离诊断的刚性分段白模代理；默认成片使用Beta原机器人。代理验证不表示解剖皮肤、动作自然度或接触物理通过。',
        },
        'mesh_geometry_helper': {
            'module': 'agent_mesh_geometry',
            'path': str(PROJECT / 'scripts/agent_mesh_geometry.py'),
            'functions': ['make_box', 'world_mesh_bounds'],
            'usage': 'from agent_mesh_geometry import make_box, world_mesh_bounds; obj = make_box(name, size, location, material=material); print(world_mesh_bounds(obj))',
            'purpose': '可选按真实尺寸创建箱形网格，读取evaluated世界顶点边界，避免primitive size和缩放引起尺寸错误。',
            'scope': '通用网格工具；任意其它形状仍可直接bpy建模。几何尺寸正确不代表动作或接触物理已验收。',
        },
        'single_example': {'version': 1, 'duration_seconds': 10,
                           'animations': ['mixamo_fbx/basic/Idle.fbx', 'mixamo_fbx/basic/Walk.fbx', 'mixamo_fbx/basic/Run.fbx'],
                           'camera': {'reference': 'object', 'target': 'window_1', 'azimuth_deg': 60,
                                      'elevation_deg': 20, 'projection': 'orthographic'}},
        'window_example': {'version': 1, 'duration_seconds': 10,
                           'room': {'enabled': True, 'size': [6, 5, 3]},
                           'objects': [{'id': 'window_1', 'type': 'window', 'size': [1.2, .12, 1.2],
                                        'position': [0, 2.5, .85], 'state': 'open'}],
                           'interactions': [{'id': 'p01', 'actor': 'A', 'object': 'window_1',
                                             'action': 'vault_window', 'start': 0, 'end': 10,
                                             'approach': 'walk_run', 'walk_fraction': .45}]},
        'duel_example': {'version': 1, 'duration_seconds': 4, 'fps': 30,
                         'events': [{'kind': 'strike', 'actor': 'A', 'motion': 'jab',
                                     'region': 'body', 'outcome': 'hit',
                                     'start': .5, 'peak': 1.2, 'end': 2.1},
                                    {'kind': 'strike', 'actor': 'B', 'motion': 'uppercut',
                                     'region': 'head', 'outcome': 'block',
                                     'start': 2.2, 'peak': 2.9, 'end': 3.8}]},
        'camera': camera_capabilities(),
        'workflow': 'wm_agent_build -> wm_agent_build_status（异步构建）-> wm_agent_load -> 检查/修改真实场景 -> wm_agent_scene。build成功本身不是最终提交。',
    }


def start_build(job_id, single_plan=None, scene_plan=None, duel_plan=None):
    folder = _folder(job_id)
    status = build_status(job_id)
    if status.get('status') == 'building':
        raise ValueError('本任务组件仍在构建，请先查看build_status')
    if (single_plan is None) == (duel_plan is None):
        raise ValueError('选择single_plan或duel_plan作为组件输入；自由场景可直接execute_code而不使用组件')
    actor_count = 1 if single_plan is not None else 2
    raw = single_plan if single_plan is not None else duel_plan
    if not isinstance(raw, dict):
        raise ValueError('组件输入需要计划对象')
    duration = raw.get('duration_seconds', 6)
    environment = validate_scene(scene_plan, job_id, actor_count, duration) if scene_plan is not None else None
    # Single duration/camera references consult the saved environment. Restore
    # that file on validation errors, before changing any build artifact.
    scene_path = folder / 'scene_plan.json'
    prior_scene = scene_path.read_bytes() if scene_path.exists() else None
    try:
        if environment is not None:
            atomic_json(scene_path, environment)
        else:
            scene_path.unlink(missing_ok=True)
        plan = validate_single(raw, job_id) if single_plan is not None else validate_plan(raw, job_id)
    finally:
        if prior_scene is None:
            scene_path.unlink(missing_ok=True)
        else:
            scene_path.write_bytes(prior_scene)

    attempts = folder / 'reference_attempts'
    attempts.mkdir(exist_ok=True)
    attempt = len([p for p in attempts.iterdir() if p.is_dir()]) + 1
    archive = attempts / f'{attempt:03d}'
    archive.mkdir()
    artifacts = ('scene.blend', 'scene.blend1', 'single_plan.json', 'plan.json',
                 'scene_plan.json', 'quality.json', 'trajectory.json',
                 'executed_scene_plan.json', 'executed_locomotion_plan.json',
                 'agent_scene.json', 'reference_build.json')
    for name in artifacts:
        path = folder / name
        if path.exists():
            shutil.move(str(path), str(archive / name))
    if environment is not None:
        atomic_json(scene_path, environment)
    plan_path = folder / ('single_plan.json' if single_plan is not None else 'plan.json')
    atomic_json(plan_path, plan)
    prefix = [str(PROJECT / 'blender'), '-b', '-t', '4', '--python-exit-code', '1', '--python']
    if duel_plan is not None:
        command = prefix + [str(PROJECT / 'scripts/duel_scene.py'), '--', '--plan', str(plan_path)]
    elif plan.get('locomotion'):
        command = prefix + [str(PROJECT / 'scripts/locomotion_scene.py'), '--', '--plan', str(plan_path)]
    elif environment and environment['interactions']:
        actions = {e['action'] for e in environment['interactions']}
        executor = 'window_vault_scene.py' if 'vault_window' in actions else (
            'door_passage_scene.py' if 'pass_door' in actions else 'prop_interaction_scene.py')
        command = prefix + [str(PROJECT / 'scripts' / executor), '--', '--plan', str(plan_path)]
    else:
        command = prefix + [str(PROJECT / 'scripts/render_white_model.py'), '--',
                            '--fbx', ','.join(plan['animations']), '--output', str(folder / 'recipe.mp4'),
                            '--frames', str(plan['frame_count']), '--fps', str(plan['fps']),
                            '--orbit_seconds', str(plan['duration_seconds']), '--camera_policy', plan['camera_policy'],
                            '--pitch_deg', str(plan['pitch_deg']), '--distance_ratio', str(plan['distance_ratio']),
                            '--fov', str(plan['fov']), '--save-scene', str(folder / 'scene.blend')]
        if environment is not None:
            command += ['--scene-plan', str(scene_path)]
        if plan.get('camera'):
            command += ['--camera-plan', str(plan_path)]
    log_path = archive / 'build.log'
    stream = log_path.open('w', encoding='utf-8')
    env = os.environ.copy()
    for key in ('DISPLAY', 'GHOST_BACKEND', 'XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY'):
        env.pop(key, None)
    try:
        process = subprocess.Popen(command, cwd=PROJECT, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    except Exception:
        stream.close()
        raise
    _RUNNING[job_id] = (process, stream)
    atomic_json(folder / 'reference_build.json', {'status': 'building', 'pid': process.pid,
                'attempt': attempt, 'log': str(log_path), 'plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest()})
    return {'ok': True, 'status': 'building', 'attempt': attempt,
            'message': '组件异步构建中；稍后查询状态。失败可修正或用bpy自由构建，不代表用户提示词被禁止。'}


def build_status(job_id):
    folder = _folder(job_id)
    path = folder / 'reference_build.json'
    if not path.exists():
        return {'ok': True, 'status': 'not_started'}
    state = json.loads(path.read_text())
    running = _RUNNING.get(job_id)
    if running is not None:
        process, stream = running
        code = process.poll()
        if code is None:
            return {**state, 'ok': True}
        stream.close()
        del _RUNNING[job_id]
        state.update(status='complete' if code == 0 else 'failed', process_exit_code=code,
                     scene_available=(folder / 'scene.blend').is_file())
        if (folder / 'quality.json').is_file():
            state['quality'] = _quality_summary(json.loads((folder / 'quality.json').read_text()))
        if code:
            state['error'] = '\n'.join(Path(state['log']).read_text(errors='replace').splitlines()[-20:])
        atomic_json(path, state)
    elif state.get('status') == 'building':
        try:
            os.kill(state['pid'], 0)
        except ProcessLookupError:
            state.update(status='interrupted', error='组件进程已结束，但MCP状态丢失；检查本次scene/log或重新构建。')
            atomic_json(path, state)
    return {**state, 'ok': state['status'] != 'failed'}


def load_scene(job_id):
    import bpy
    folder = _folder(job_id)
    state = build_status(job_id)
    if state.get('status') == 'building':
        raise ValueError('组件尚未完成，不能读取构建中的场景')
    path = folder / 'scene.blend'
    if not path.is_file():
        raise ValueError('没有已保存的组件场景；可查看组件错误后修正或自由创建')
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    bpy.context.scene['wm_agent_job_id'] = job_id
    return {'ok': True, 'message': '已载入真实组件场景，可通过MCP修改镜头、动作、材质或增添人物，再wm_agent_scene提交。',
            'frame_start': bpy.context.scene.frame_start, 'frame_end': bpy.context.scene.frame_end,
            'camera': bpy.context.scene.camera.name if bpy.context.scene.camera else None,
            'recipe_quality': state.get('quality')}
