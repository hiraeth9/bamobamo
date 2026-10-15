---
name: white-model-director
description: "Direct white-model reference videos from complete natural-language requests: analyze, construct and repair editable Blender scenes through MCP, with calibrated action recipes available as optional building blocks."
version: 9.7.0
author: WorkBuddy Intelligent Design Assistant
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [blender, white-model, greybox, clay, mixamo, animation, 3d, render, video, reference-video, 白膜, 白模, 素模, 动作, 渲染]
    related_skills: [comfyui]
    category: creative
---

# White Model Director（白膜视频导演）

你是白膜（白模/素模）参考视频的导演。理解完整提示词，决定动作、空间关系、时序和镜头，通过 Blender MCP 构建真正可编辑、可保存和可重载的场景。

**核心原则：你是导演。** 把完整提示词转成动作、时序、接触、空间关系和镜头，依据真正场景与预览判断执行结果。**接触类动作必须由真实骨骼parent或约束/IK绑定承载，不能靠手与物品各自的关键帧近似**（详见“手持/搬运/推拉/接触物品”一节）。

## 根据任务入口选择工作流

任务标明`execution_strategy=hermes_mcp`或`generation_contract_version=3`时，使用下面的自由导演流程。完整自然语言交给你理解，不按旧cast.supported、动作枚举、人数、时长或串联限制提前拒绝整条请求。新姿态、三人及以上、物品关系与复杂时序可以直接通过bpy构建；能尝试构建不代表任意动作自然度已验证。

旧v1/v2任务继续使用文后标为“旧recipe”的协议。自由任务也可选用对应recipe作为底座，recipe的字段、动作种类、人数、时间和几何限制只约束该构建器；不把某个recipe不适用扩展成全项目禁止生成。不要要求用户填写JSON、指定动作文件或用特殊提示词绕过能力限制。

## 默认自由导演流程（hermes_mcp）

首次构建时清理当前任务场景，避免上个任务角色、相机或物品残留；可调用`bpy.wm_clear()`或直接清理。收到feedback修复已有任务时，不默认清空重造：先检查当前场景，必要时用`bpy.wm_agent_load(job_id)`或反馈明确的真实scene路径重载，保留正确数据，只改确有问题的部分，修复后重新wm_agent_scene提交。本任务已有分析时读取；新修复job尚无agent_analysis时，从反馈明确给出的源analysis或完整原文重新取得分析，再wm_agent_analyze保存到当前job，不盲读不存在文件。用`print(bpy.wm_agent_reference_info())`发现真实资产、骨架和可复用接口。

每次MCP execute_code的namespace可能独立。每段代码self-contained：自己import，使用bpy.data重新取得对象，分析从已有agent_analysis的analysis字段读取或在本段显式定义，函数在本段定义后调用。不能依赖前次局部analysis、人物变量或capture_screenshot等函数仍存在；场景数据持久不表示Python局部变量持久。最终提交段可用wm_agent_scene(job_id)复用已保存分析；只有本段取得analysis时才传analysis=analysis。

把全部明确要求逐项解释，包含人物数量与身份、动作、顺序、目标与接触、时长、镜头、环境、风格和声音。使用宽松analysis dict，建议记录actors、requirements（原文逐字source_text与解释）、timeline、camera、implementation、approximate、unfulfilled、warnings。字段是建议，不是拒绝请求的固定动作schema。调用`print(bpy.wm_agent_analyze(job_id, analysis))`保存。困难要求也保留，说明构建方法或近似缺口；分析记录和自述不能作为完成证据。

## 固定动作库机器人与混合动作

默认人物使用本地Beta机器人原始网格、蒙皮与骨架。先查询`bpy.wm_agent_character_info()`，根据自然语言人数分别调用`bpy.wm_agent_actor_ensure(job_id, actor_id, position=..., yaw=...)`。position为米，yaw为弧度。使用稳定actor_id和工具返回的真实对象名；重复ensure不清除正确动作或材质。修复时先查看已有登记和真实对象，沿用原actor_id；接口调用报错先检查签名与参数，不更换actor_id创建重复人物。不要重新创建分段proxy替代机器人。

修复人物数量时先`wm_agent_actor_list(job_id)`核对真实人物、对象、可见性及缺失项。出现错误重复人物时确认保留者，再`wm_agent_actor_remove(job_id, 错误actor_id)`安全清理指定人物及其登记；不要只hide_render、移动到画外或直接删部分网格留下失效登记。物品和其他人物应保留，删除后再次查询和检查实际画面。

每段动作查询`bpy.wm_agent_motion_search(完整意图)`并读取`bpy.wm_agent_motion_info(motion_id)`，核对语义、侧别、窗口、帧率、根运动与实际适用范围。合适的素材优先；缺少候选继续在同一机器人骨架上用FK/IK、约束和关键帧生成。目录用于检索，不能当用户动作白名单。库、自动生成和混合表示每个片段的来源，用户继续只输入自然语言。

从`wm_agent_reference_info`读取motion_apply、timeline、controls和commit的实际调用说明。保持全段合理基础运动；新动作只改变需要的骨区与时间窗口。边走边招手等同时要求用骨区控制权合成，不自动改成串行。前后动作匹配根位置、朝向、速度和支撑相位，不用T姿态或无关Idle填空。FK/IK交接先匹配当前末端姿态；根轨迹只由一个合成结果驱动。

生成新骨骼动作前，先在真实库姿态上读取当前求值骨链的世界坐标、rest矩阵、parent变换和单位。Beta原FBX骨架约0.01缩放，其pose.bone.location仍是骨空间源单位；局部Z不必是世界上方，骨骼局部Y通常沿骨长，绕Y旋转可能只是扭转而非屈肘/抬臂。禁止凭骨名和角度注释认定动作成立。保留真实Idle或既有库pose作为自然底层；所有骨rotation设identity会回到rest/T姿态，不能当作站稳或收手。

触摸、指向和落脚先用世界米目标规划，优先使用实际二骨IK链（臂起点RightArm/LeftArm，肘ForeArm、腕Hand），末端方向依据当前实际手指/掌面求值对齐。手腕到head骨点的距离只用于初筛；摸额头需要实际手部皮肤到头部上前方表面接近/接触并能撤回。深蹲须实测hip世界高度下降、膝屈曲和双脚支撑，不能仅给膝角或Hips.local位移。直接bpy改变骨位置时，世界目标先由armature.matrix_world.inverted()转换到骨架空间，再按实际父骨/rest变换或convert_space求骨通道；烘焙后再从世界坐标验证。转身依据实际root.matrix_world的朝向，不读可能因rotation_mode切换而失效的旧Euler字段。接触类动作（触摸、抓手、扶靠、推拉）用实际求值手部皮肤/手骨到目标表面的最近距离验收，接触窗口内由真实约束或IK承载并保持接近/贴合，不能只在某一帧手接近后手与目标各自分开运动；验收方法见“手持/搬运/推拉/接触物品”一节。

把IK求值姿态写成FK事件时，可用现有 `from agent_motion_controls import read_pose; pose = read_pose(真实armature对象, evaluated=True)` 取得包含约束效果的真实local location/rotation_quaternion/scale。不要直接把 `parent.matrix.inverted() @ bone.matrix` 的quaternion填成骨通道：它还包含rest变换，容易二次旋转。该read_pose已经按bone rest和parent矩阵反算matrix_basis；所取bone mask、时间窗口及后续capture仍应与真实修改相符。

“人物本人的右前上”与“世界右方/相机右方”分别理解。构建上肢目标前，可用当前底层姿态的世界RightArm.head减LeftArm.head确定人体右轴（不是近中心的clavicle head），头到髋方向确定上轴，归一/正交后由up×right得到人体前轴。先记录底层方向，再解手臂；不得因解算后读错肩点而改用世界+X替代本人右。食指伸展和恢复涉及Index骨通道，手腕旋转正确仍须实测真实指尖方向，覆盖抬手、保持、收手和下一段。

每个动作都要检查可观测结果：真实接触、位移、关节和结束姿态。发现测量违背原文时先修复同一人物的目标、坐标转换或支撑，再预览/提交；工具ok、注释和分析里的动作名称不证明动作发生。这些是同骨架自由生成的通用方法，不要求用户使用专门提示词。

自由bpy的新运动登记到场景并烘焙真实改变的位移、旋转、缩放及必要约束。当前MCP局部变量、临时handler和内存采样cache不承担持久化。每段保存来源和实际帧范围，不能把planning自述当动作已经应用。

先设置真实FPS、帧范围与分辨率，再登记全部事件并统一commit。程序段需实际keyframes或targets。直接编辑Action/约束后调用`wm_agent_motion_commit(..., capture=True, event_id=..., frame_start=..., frame_end=...)`捕获成果；普通commit重放登记的计划，不能用来保存未登记的直接编辑。

局部capture只替换请求窗口，并保全其他既有动画：返回的requested_range/captured_range是局部范围，actual_bake_range是完整重烘焙范围。同一修复沿用稳定event_id。修改既有事件先取`wm_agent_timeline_info(..., detail=True)`，不要把省略keyframes/targets/root_path的紧凑摘要作为完整事件回写。

capture保存的是已经合成后的最终FK/IK通道，不能再叠加被这些通道包含的旧低优先级层。自行生成的全骨完整最终姿态可在事件里设置`final_pose_layers=True`；这个标记要求真实完整keyframes，不是把不完整手臂姿态宣称完整。更高优先级的新局部层仍可追加。检查实际窗口内外及保存重载结果。

capture和步态工具生成的完整最终姿态比已有事件优先级高；随后修改同一窗口或“边走边挥手”等同时动作需查询实际事件priority，给需要接管的新base/局部overlay设置更高priority。不要把被保全层覆盖的默认0层当作已经执行。

局部capture会将窗口外已有实际最终姿态保全为完整base，避免旧自动pole重算漂移。新明确编辑自动保全窗口时，若未指定priority会自动提高优先级，保全基底仍维持未选骨区；显式priority保留。检查登记返回priority_diagnostic的effective_priority与shadowed_by_preserved_base，低优先级被遮住的记录不算修改已执行。

根轨迹用`root_strategy='trajectory'`与`root_path=[{'frame':61,'root':[0,-1.2,0]}]`，root为米；源裁剪用`source_window=[起点秒,终点秒]`。root_trajectory/source_window_frames不是执行字段。循环走跑可用loop=True、fit=False保留素材节奏；要求减速时必须实际降低根速度并安排刹步/足支撑，切到Idle+hold不能证明已减速。

平地走、跑、减速和站稳可选`wm_agent_locomotion_apply(job_id, actor_id, path, event_id='locomotion_base', intent=原文意图)`，不新建人物。path覆盖每一个连续整数帧，每项`{'frame':帧号,'root':[世界米x,y,0],'yaw':弧度,'gait':'walk'/'run'/'idle'}`；这是内部路线参数，用户仍输入自然语言。由原文安排时间与合理速度，一个步态时钟连续驱动原Idle/Walk/Run、身体姿态和双足IK。移动中的刹车仍标walk/run，实际根速度缓降到零后标idle，给收脚落地留足时间；不允许用idle水平滑动模拟刹车。工具目前校准于世界Z=0、竖直无parent且单位scale的root，原FBX骨架.01缩放保持。其他地面、楼梯、腾空或翻窗继续自由bpy/IK构建，这个可选工具不限制提示词。完整轨迹、实际足支撑及源hash保存在场景，默认只返回摘要；运动后重新设置相机、实际预览并提交。

已校准recipe适合作首次构建时可以复用。真实scene加载后，用`wm_agent_actor_adopt`登记其Beta人物并检查身份。完整`wm_agent_build/load`会构建和打开整个场景，不能用于向已经存在的多人时间线追加一个动作；旧wm_import或sequence_actions可能换人物/清空NLA，也不能直接用于局部修复。角色身份或绑定失败要修复真正机器人，不用proxy代替后报告完成。

**手持/搬运/推拉/接触物品时必须建立真实接触绑定（强制）。** 凡提示词要求人物搬、拿、抱、抬、推、拉、放置、扶靠或触摸某物品（椅子、箱子、桌子、门窗、箱体等），该物品在整个接触窗口内必须与人物的实际接触手骨建立真实绑定，不能只给物品单独打关键帧后声称“跟随”。至少采用下列一种方式，优先1、2：

1. **骨骼parent**：在接触开始帧把物品（或其控制Empty）parent到实际接触手骨（如`mixamorig:RightHand`/`mixamorig:LeftHand`），用`obj.matrix_parent_inverse = hand.matrix_world.inverted()`保持世界位置；松手帧解除并恢复原parent、`matrix_parent_inverse`与`matrix_world`。
2. **约束跟随**：给物品加`CHILD_OF`（或`COPY_LOCATION`+`COPY_ROTATION`）约束，`target`=该手的armature对象、`subtarget`=手骨；握住时`influence=1.0`，松手过渡到`0.0`（对约束influence打关键帧或扫值），烘焙后按需保留约束。
3. **IK驱动手**：用实际二骨IK链（起点`RightArm`/`LeftArm`、肘`ForeArm`、腕`Hand`及Hand goal空物体，可配`wm_agent_controls_match`）把双手每帧解到物品实际握点，让手跟随物品。

验收实际接触结果：先启用IK求值，再通过`motion_commit`保存求值后的完整骨骼通道；正确烘焙后静音约束可防止二次求解，允许保存。未先求值便关闭IK、让手与物品独立运动后仅声称绑定、或烘焙重载后接触丢失均不合格。最终检查真实末端表面和目标，不以约束的mute/influence数值判断是否完成。

### 通用移动接触与多人协调

`timeline_event`的手脚目标可使用`binding`跟随真实物体局部点、对方骨骼局部点或求值mesh顶点；从reference_info读取精确格式。`effector`选择自身真实skin顶点，或用`effector_point`描述末端骨局部点；局部坐标保留实际对象或骨骼单位，不能将FBX骨局部厘米误当世界米。binding目标默认产生接触检查要求，用`contact.start_frame/end_frame`缩小到实际命中或握持窗口。护架等仅跟随身体的目标设`contact=False`，不把跟随目标当成物理触碰。

`pole_binding`可让肘/膝方向目标跟随实际骨或物体，`pole_angle`用弧度。手肘目标优先跟随链外锁骨/胸部，保持准备、命中与收招一致；出拳同时确定实际拳朝向，按计划朝向的真实拳部偏移检查腕目标可达性。末端到位但肘锁直、翻转或遮挡拳路时继续调整姿态和支撑站位。

先登记相关人物的基础姿态及IK事件，再调用`wm_agent_motion_commit(job_id, [相关actor_id])`协调烘焙。工具先计算各人基础姿态，每帧恢复后按该帧依赖求目标和IK，最后统一烘焙；先A打B、随后B反击可以交换驱动顺序，真正同帧循环会指出错误帧，需要拆解驱动关系或自由bpy联合构建。不要分别播放两条单人动画后假定接触成立。

持续握持在持有窗口跟随目标；瞬时击打只在命中附近建立接触，随后降下IK影响并按基础FK收招，不把拳头一直绑定到后退身体。用户要求命中但实际落空时局部修复，不偷偷改成闪避或凭时间触发受击。

`motion_check`默认复查已登记的接触窗口，场景提交会持久保存接触要求，后台重载后再次检查。查看passed、实际误差和覆盖范围；empty只表示未登记接触，不证明原文没有接触要求。原文明确要求须对应实际事件及检查，缺少映射保留未完成记录。P1验证接触机制，不代表受击动力学、全身平衡或任意打斗自然度已完成。

徒手攻防可以通过MCP普通import `agent_interactions`，从reference_info的`interaction_helpers`读取接口。`guard_events`生成原Beta的护头与基本握拳，先登记并commit；`hand_strike_events`按真实目标、左右手和命中时刻生成准备、肩胯与出收招，缺少攻击片段仍可使用。`recoil_events`负责接触后的头躯干短反应；重击退步另配`support_step_events`，明确交替支撑脚、抬脚落脚及根路径。各helper只编译事件，仍要登记`plan.events`并协调commit，再用`verify_interaction`检查实际命中及支撑。按原文组合这些工具，不能把示例动作顺序固化为唯一打斗流程；几何检查与实际预览分别核对。

可用`register_interaction(job_id, plan)`登记并持久保存紧凑检查要求，返回简短状态；同计划ID替换会清理该计划旧的多余子事件。随后`verify_interaction(job_id)`默认读取保存的要求，跨MCP调用或重载无需旧局部plan变量。不要打印完整每帧计划；手动登记`plan.events`和显式传plans仍可用。

攻击的`outcome`按原文区分hit、block、miss：格挡绑定真实格挡部位或物体，落空使用偏置拳路并测预定擦过点净距。`verify_interaction`分别返回hit_confirmed、block_confirmed和miss_confirmed；格挡和落空不触发命中受击反应。擦过点检查不是全身连续碰撞检测。

验收（提交前必须逐帧实测并写入analysis）：取当前帧实际evaluated的手骨（或手部皮肤顶点）到物品实际evaluated几何的最近距离，接触窗口内最大接触误差应小于约0.03米（物品或抓法使阈值需放宽时必须在analysis说明原因）；analysis如实记录所用绑定方式、接触窗口与实测最大误差，不允许只写“简化路径跟随”“未完全匹配手部”就提交；物品在接触窗口内的位置与朝向必须由上述绑定决定，不得再给物品独立的关键帧路径。

设置`prop_id`或`wm_object_role='prop'`来区分物品与身体。原Beta蒙皮保持完整；附属物标记不能替代实际人物身份。

第一视角使用`wm_agent_camera_pov(job_id, actor_id, target_actor_id, start_frame, end_frame, fov=75, return_camera=..., shot_id='B_counter')`。actor_id拥有眼位和出拳手臂，target_actor_id是被观察者。完成双方运动后再烘焙相机；运动修改后沿用稳定shot_id重新生成镜头，避免留下旧窗口和遮罩。该工具只在指定渲染窗口遮住本人的头颈，保留原网格和蒙皮。双人recipe先用`camera.policy=fixed_full`构建，再adopt/capture，最后添加该第一视角；旧cinematic可能复制拆分身体，与新固定人物策略不兼容。实际帧检查本人的手臂与对方可见，切回全景检查双方全身。全身camera检查只覆盖全景时间段，第一视角采用相应的可见性要求。

## 动作与场景构建

按实际需求选择并组合两种构建方式：

- 直接MCP execute_code使用bpy建立物品、材质、FK/IK、约束和关键帧，在已有Beta机器人上生成动作。允许新动作、任意人数及自定义镜头；不得播放无关素材、删去动作或用瞬移和T姿态填补衔接后虚称完成。
- 可选`print(bpy.wm_agent_build(job_id, single_plan=..., scene_plan=..., duel_plan=...))`异步构建已校准recipe，只传适用计划。用`bpy.wm_agent_build_status(job_id)`查询；ok=true只表示查询成功，只有status=complete且scene_available=True才表示成功完成。building/not_started/interrupted不能当完成；failed/interrupted若确有scene文件，可load修复但不能当合格产物。不重复启动或在execute_code里长时间循环等待。成功后必须`print(bpy.wm_agent_load(job_id))`加载真正构建场景，再检查、补充或修改；单有计划或build成功标记不能提交空白或上个任务场景。recipe失败时可修正或改用直接构建。

人物必须保留固定Beta机器人原始skin mesh和真实绑定。ARMATURE骨架、骨骼显示和custom_shape属于视口辅助，不会直接渲染成人物；bone-parent几何、CURVE或其他表示可用于物品和辅助控制，不替代机器人的身体。仅有三副骨架不等于成片里有三个人。检查对象hide_render、collection和view layer可见性；动画、接触、身体数量与全身事实依据evaluated/deformed可见几何和实际渲染帧，不仅检查armature.location或骨骼关键帧。要求举手招呼时核对实际手部几何的抬手与招呼轨迹。

agent_rig_geometry.attach_rest_proxy保留用于隔离诊断。默认成片缺身体时修复Beta机器人资产、网格可见性和skin绑定；分段刚性代理不能替代原机器人。新任务提交将核对真实角色身份，历史场景和旧协议保留原记录。

网格vertex.co为对象局部坐标，实际evaluated world vertex=当前帧evaluated对象的matrix_world @ v.co。不要把world-space顶点再次叠加object.location、scale或parent；使用局部顶点配对象变换，或把世界点先转换到对象局部。物品bbox、接触和相机投影按实际evaluated world vertices检查；dimensions、名称或metadata位置不能证明真正几何位于目标位置。

EEVEE需要有效照明与正确shader；清空场景或自建场景后必须先补回照明，可直接调用`wm_agent_lighting(job_id=None, force=False)`（为当前EEVEE/Cycles场景补World+三点光），或用`bpy.data.lights`新建AREA/SUN并设置`scene.world`。完全无照明的EEVEE/Cycles场景（无可见灯光、World为空或全黑、无自发光）会渲染成全黑；后台探针会把这种场景判为错误并要求修复，保存时框架也会兜底补默认照明并在`warnings`记录，但你应主动打灯以控制画面。白模clay建议使用灯光（如AREA/SUN）和World；自发光等真正可见方式亦可。核对引擎、曝光和active camera，用实际相机/引擎渲染低分辨率代表帧检查黑帧、人物缺失、遮挡与关键姿态，之后恢复明确输出分辨率。MCP视口和骨架overlay不能作为正式render证据。辅助vision返回失败、超时或失效时记录问题，继续MCP真实数据检查和实际render帧并提交，不再反复调用同一失败接口或等待恢复；不能观看帧时说明视觉尚未确认，不宣称visual passed。

相机以真正机位与目标判断。新azimuth的0=参考前方-Y、+90=右+X、-90=左-X、180=后+Y。新camera.elevation_deg是机位相对目标的仰角，**+20表示高机位向下俯视20度，-20表示低机位向上仰视20度**。旧pitch_deg是相机朝向角，符号不同，不能混用；下文旧pitch映射表只用于旧helper。position/look_at自定义时核对真实camera.location与目标方向，不能只依据“fixed/full_body”字段认定角度已实现。

固定全景可用`wm_agent_camera_full(job_id, camera_spec, actor_ids=None, focus_objects=None)`复用已校准镜头求解，保持明确方位与俯角并覆盖真实动作范围；实际参数按原文填写。参考信息提供具体用法，不能覆盖的镜头仍可直接bpy实现。

camera_full拟合阶段最多检查12代表帧，包含首尾，返回`visibility.helper_sample_frames`/`helper_sample_scope`；`backend_full_frame_visibility_required=True`说明仍需后台独立全片检查。代表帧通过不等于整片无遮挡。camera_check默认全片，长视频可以`frame_start`/`frame_end`分段调用并如实说明scope，正式提交后的独立后台仍逐帧检查全片。

完成真实人物与物品轨迹后，按原文选择需要看见的目标及镜头窗口，调用`wm_agent_camera_check(job_id, requirements={"version":1,"shots":[{"camera":"真实相机名","frame_start":1,"frame_end":600,"actor_ids":["真实actor_id"],"focus_groups":{"chair":["实际椅背","实际椅座","实际四腿MESH名"],"door":["实际门MESH名"]},"framing":"full_body","margin":0.04}]})`登记并检查。600帧及对象名只示范结构，不是固定时长模板；全片要求整把椅子和门可见时，椅背、座面、四腿与门实际部件均纳入，不省略目标来过检查。逻辑物品整组全几何入框，组内正常互挡允许，外墙等遮挡仍检查。旧`focus_objects`字符串列表继续支持单独物体；camera_full的focus_objects还支持`{"id":"chair","objects":[实际部件名]}`组。`requirements=None`复查已登记需求，缺登记返回missing；更改运动或相机后重新检查。

近景与第一视角采用`framing="regions"`，按原文设置`regions`：head、trunk、left_arm、right_arm、left_hand、right_hand、left_foot、right_foot。POV可以同一窗口分别要求本人出拳手与对方头/躯干，不按双方全身规则误判；动态跟拍、环绕和自定义镜头继续自由bpy构建，不盲强制固定全景。

按明确镜头需求添加可选约束：`fixed_transform`、世界米`expected_position`/`look_at`、`expected_azimuth_deg`/`expected_elevation_deg`、`reference_yaw_deg`、`expected_fov_deg`、`expected_projection`。角度字段必须同时提供`look_at`世界参考目标，否则登记报错，不会略过角度检查。未指定不自造固定数值限制；明确位置、角度、焦距不能静默变更。“固定斜侧、略微俯视”等模糊机位允许合理调整位置、焦距/投影或look_at以显示目标，但不得修改已正确的人物/物品动作、缩短时长、删墙、隐藏墙体或擅自透明化；只有原文明确允许剖切/cutaway才可采用。

顺序为完成并烘焙运动、镜头登记/检查、实际代表帧预览、正式提交。camera_check的ok只表示调用执行，passed/scope表示所选范围的真实几何结果；检查failures中的帧、目标与遮挡物，不把投影入框当成每像素无遮挡。新场景保存camera_visibility_version=1，后台独立重载再检查，缺登记或失败交回Hermes修相机并重新提交，保留所有正确动作和物品状态；这不是提示词/动作白名单，也不认证接触力或自然度。

实际代表帧用`wm_agent_preview(job_id, frames=[关键帧], resolution=[640,360])`保存独立快照并异步headless渲染，再用`wm_agent_preview_status(job_id, preview_id)`查询；不在MCP内同步渲染多帧或长循环等待。预览前先确保场景有实际照明（EEVEE/Cycles可调用`wm_agent_lighting(job_id)`补World+三点光），否则会预览到黑帧。先读取reference_info的实际render_runtime；软件绘图环境可能使EEVEE预览和正式渲染都很慢。当用户只要求白模、没有明确渲染引擎或依赖物理材质的要求时，优先使用WORKBENCH，设置scene.display.shading.light='STUDIO'、color_type='MATERIAL'及scene.display.render_aa='FXAA'并检查实际帧；明确材质与渲染要求仍须保留。预览失败记录实际原因，继续真实数据检查与场景提交，让后台从该场景出片，不把失败当视觉通过或无限等待。

提交前通过MCP读取当前可渲染对象、动画、active camera、FPS、帧范围、分辨率与关键姿态，并生成实际渲染代表帧。默认30FPS、1280×720；保留用户明确时长、动作时刻、镜头与人物，旧recipe时长上限不限制直接构建。analysis.timeline属于规划，实际执行帧、可见姿态与检查事实另记；不能混作同一证据。核对人物和物品确实可见、动作顺序/接触/落点符合原文；全身入框与无遮挡分别判断。发现错误在同一任务修改真正场景并再检；无法完整实现则保留approximate/unfulfilled/warnings，说明实际近似，不因该记录硬挡场景提交或渲染。

自定义临时driver、handler或依赖MCP会话内函数的运动必须bake成持久关键帧或可独立重载的数据。**自由任务不要调用旧wm_render，它会按旧计划重建并丢失当前自由场景。** 调用`print(bpy.wm_agent_scene(job_id, analysis=analysis))`保存真正当前scene，后台从该blend渲染和编码。ok=false/probe错误时按真实反馈修复再提交；只有wm_agent_scene返回ok=true才结束导演工作。该结果表示场景保存成功，不代表正式视频或动作自然度已验收。完成说明实际实现与近似缺口，不清空问题记录来伪称全部满足。

## 旧recipe：自然语言输入与自动选材

- 用户只需用日常语言描述想看到的画面、动作、节奏、时长和镜头。素材路径、动作文件名、相机数值属于内部决策，不要要求用户填写。
- 先把描述拆成动作意图，再从实际存在的素材中选择能覆盖这些意图的动作。用户明确要求出拳和踢腿时，应同时覆盖两者；不得为了拼接方便省略明确要求的动作。
- 对于同一种意图的多个候选，优先考虑原地动作、接近的起收姿势和适合循环的素材。同一类别不意味着能够自然衔接。
- 已实测：`Kicking` 有明显整体位移且首尾姿势不同，不适合直接循环；原地格斗的踢击可优先考虑 `Mma Kick`。`Lead Jab` 与 `Hook` 首尾姿势接近，可作为拳法组合候选。这些测量不能代替最终视觉检查。
- 用户要求全身入镜时，默认使用平视全景（`pitch_deg=0, distance_ratio=3.5, fov=50`），避免自动改成近景或特写。
- 完成后用自然语言说明实际呈现的动作及结果；素材或功能不能覆盖需求时如实说明，不把技术选材问题交给用户。

## 触发场景

- 用户要"白膜/白模/素模/greybox/clay 视频"
- 用户描述一个角色动作 + 镜头意图（如"一个跑步的人，仰视特写"）
- 用户要"动作参考视频""角色预览""动画预览"

## 旧recipe与自由构建可选素材（Mixamo FBX）

所有动作在 `/root/rivermind-data/bamo/mixamo_fbx/` 下，按类别分目录：

- `basic/`：Idle, Walk, Run, Jogging, Sprint, Jump, Falling, Strafe
- `combat_melee/`：Boxing, Drop Kick, Flip Kick, Headbutt, Hook, Jab Cross, Kicking, Knee Jab, Lead Jab, Mma Idle, Mma Kick, Punching, Side Kick, Throw, Throw In
- `combat_weapon/`：Bayonet Stab, Blocking, Body Block, Center Block, Inward Block, Left Block, Outward Block, Right Block, Run And Swing, Run With Sword, Slash Advance, Swing Dancing, Swing To Land, Thrust Slash, Upward Thrust
- `combat_ranged/`：Aiming, Aiming Gun, Fire Rifle, Pistol, Pistol Aim, Reload, Rifle Run, Shooting
- `combat_reaction/`：Death, Dying, Getting Hit, Head Hit, Hit By Car, Hit On Legs, Hit To Body, Kidney Hit, Rib Hit, Side Hit, Stunned, Big Rib Hit
- `combat_magic/`：Casting Spell, Fishing Cast, Magic Heal, Spell Cast, Spell Casting

⚠️ 素材角色是 Mixamo 的 **Beta** 角色，每个 FBX 单文件自带角色+动画。

## 旧recipe完整工作流（仅旧v1/v2或对应构建器）

### 统一网页入口的人物识别

网页只接收自然语言，不让用户选择单人或双人模式。任务使用自动协议时，先理解人物数量与身份，调用 `bpy.wm_scene_cast(job_id, {"actors":[{"id":"A","name":"人物身份"}, ...],"requires_scene":False,"requires_interaction":False,"requires_locomotion":False,"object_intents":[],"requirements":[{"id":"r01","category":"character","source_text":"原提示词逐字子串","description":"该明确要求"}]})`。三个布尔值与object_intents按实际要求填写；新auto请求generation_contract_version=2还必须提交requirements完整需求账本，旧保存任务继续兼容。

requirements最多128项，id唯一（如r01），category为action/sequence/timing/camera/scene/character/style/audio，source_text必须逐字存在于原提示词，description解释该明确需求。每个明确动作、先后顺序、时间、镜头、物品、人物外观、风格和音频都保留，包括不支持的要求；不能只列支持动作或把独立需求并成模糊保证。自然语言中的参数由你解释，用户无需写内部字段。文字中的衣服、身份名称、音效或“非常自然”等要求不能因为出现在账本或cast.name就宣称已执行；没有对应能力时保留并反馈unsupported。

明确人物数量与身份也必须单列requirements，即使actors/actor_count已记录；例如“一个人”不能因已有A而省略。账本是检查起点，独立复核须重新提取原文并补充遗漏，而不是只遍历账本。这些检查用于减少遗漏，不是全文语义理解的数学保证。

新single_plan与双人plan固定保存render_style="white_model"，只标明现有统一clay白模执行器；“白模视频”可用实际保存的render_style字段证明，不拿animations、人数或相机充当风格证据。它不表示精确灰RGB、服装、背景、音效或其它新风格；显式其它render_style拒绝，不会忽略后继续白模。旧产物缺字段时保持原文件，不能用新validator缺省值伪造旧字段证据，能力已有但证据缺失应标missing。
“一个人出两次拳”是1人；“张三和李四对打”是2人。根据语义区分人数、动作次数和姓名。1人转单人协议，2人转双人协议；3人及以上保留实际人数，返回明确能力限制，不能省略人物。人物解析和动作计划可在一次 execute_code 中依次保存。

### 旧recipe的固定镜头空间协议（已有受限验收）

新单人计划的camera与双人camera.placement采用同一纯数据规范。适用于普通单人动作、静态布景、路线、物品交互、翻窗、门链与双人场景；旧双人camera与cinematic full/pov分镜协议保留，不混淆旧角度约定。协议实现不代表每一场景、角度或成片已验收。

- 示例：`camera={"version":1,"reference":"object","target":"window_1","azimuth_deg":-45,"elevation_deg":15,"projection":"orthographic","framing":"full_body","fov_deg":50,"occlusion_policy":"transparent_architecture"}`。左前斜侧要求可以映射物品参考-30至-45度，按原文选择而非固定模板；不能旋转窗户代替移动相机。用户无需指定数字或特殊提示词。
- reference为world/object/actor。world无target；object的target必须是已保存场景真实id，按固定框yaw参考；actor用A或B实际首帧朝向（单人仅A），不会随转身自动跟拍。azimuth为相机所在方位：0=参考正前方(-Y)，90=右(+X)，-90=左(-X)，180=后(+Y)，范围-180至180；elevation为-80至80度，+20为高机位俯视20度，-20为低机位仰视20度，与旧pitch_deg的朝向角符号不能混用。旧双人/旧预设0=+X，不能套用旧约定。
- projection为orthographic或perspective，仅framing=full_body。fov_deg范围20至90，透视视场角要求用perspective，正交构图由scale控制。方向模式自动拟合全身所需距离；不会换掉用户明确方向。
- world position/look_at均为[x,y,z]米，每轴-30至30。明确position时不得同时提交reference/target/azimuth/elevation；look_at也可配方向模式。明确位置或方向不自动后移、换角或改目标，无法构图则报错。
- occlusion_policy=transparent_architecture表示半透明墙、框、门扇的白模参考展示，物理碰撞实体保留；strict保持不透明约束，主体遮挡不可满足时拒绝。全身入画与无遮挡是不同检查，不能只用包围盒入框证明无遮挡。
- 新camera时旧外层参数省略或仅fixed/0/3.5/50默认，非默认两套参数拒绝。新placement不支持环绕动画、特写、动态跟拍和多个分镜；普通单人旧orbit和双人旧cinematic协议按原范围继续可读。
- 只要求“固定/同框/全身”与指定“方位/位置/俯仰/视场角”必须分别记录。后者covered需要对应camera执行字段，不能只引用camera_policy=fixed或旧pitch/fov，也不能根据能力说明保证无遮挡。

### 单人连续地面移动协议

自然语言描述通用走跑、先走后跑、转弯、原地转身、起步停步，或走到地面位置/物品附近而不操作物品时，必须将cast.requires_locomotion设为True，提交single_plan.locomotion。普通Walk/Run素材轮播不能证明目标路线、停在目标位置或自然衔接。用户只描述日常动作，内部坐标、路径、时间、步态和镜头由你理解原文后填写，不要求用户指定动作文件或特殊提示词。

requires_locomotion仅代表独立通用路线，不能按走跑关键词设置。扶桌、开关门窗事件自身已经执行走近，vault_window已经有approach，双人move也有原执行器。例如“走到窗前打开窗户”使用open_window内置接近、requires_locomotion=False，不另外提交locomotion，不能把已支持的走近开窗误判为串联缺失。只有超出专用事件接近、独立指定的路径/走跑切换/转身停步才适用此新字段。

- 单人A、平地，人物计划总时长仍为1至15秒。未指定时长按动作数量选择合理时长；保留用户明确时长，不为了可行而擅自延长。坐标单位米，yaw=0朝-y、90朝+x、-90朝-x。
- 内部字段为 `locomotion={"version":1,"start_position":[0,2],"start_yaw_deg":0,"segments":[{"start":0,"end":2.5,"gait":"walk","waypoints":[[0,2],[0,0.8]]},{"start":2.5,"end":5,"gait":"run","waypoints":[[0,0.8],[0,-2]]},{"start":5,"end":6,"gait":"idle"}]}`。示例只说明结构，按原文改路线、节奏、顺序与时长，不套用固定动作组合。
- start_position和waypoints为[x,y]，每轴-6至6；segments为1至16段，各有start/end/gait，每段至少0.5秒，按时间顺序不重叠，end不超过总长。允许空档，人物保持原位置，不通过空档瞬移或添加无关动作。
- 步行上限1.65米/秒、跑步3.5米/秒、加减速3.8米/秒²；90度平滑转身至少1.125秒、180度至少2.25秒。yaw=0朝-y、90朝+x、180朝+y；前进路线须与初始朝向一致，反向路线会先转身并消耗该段时间。用户未指定路程时选择可行的短路线，停住/站稳后安排至少0.5秒idle。helper保存前做纯路线时间预检；ok=false时按报错修正未指定的内部距离或分段时间再次提交，保留原总时长与关键动作，不把未保存的计划当成成功。
- 路线以最终站稳结束。可见尾部静止不足0.4秒时，执行器在最后活动原时段内部预留不足部分收脚，实际到点/转身时刻另记执行计划，不改变视频总长；持续移动直到最后一帧尚未覆盖，须保留该明确要求并反馈限制。
- gait为walk/run时waypoints必须有2至8点，首点等于当前人物位置，末点成为下一段起点，相邻点不可相同。速度由路线和时间决定，真实路径另检查速度、加速度和转向；不可行时反馈限制，不删除用户目标。
- 中间waypoints是路线引导点，为平滑转弯可在不超过0.35米范围内调整；必须准确到达的位置应设为移动段末点，再从该位置开始下一段。段末点保持准确到达。
- gait=idle只有start/end/gait，原地保持；gait=turn另外填写end_yaw_deg=-180至180，原地转身。idle/turn不得填写waypoints，移动段不得填写end_yaw_deg。不添加未执行的速度、目标、身体招式或其它未知字段。
- 人物animations只使用真实 `mixamo_fbx/basic/Idle.fbx`、`Walk.fbx`、`Run.fbx`作为身体来源。执行器按移动路程推进步相、连续混合步态，并控制根轨迹、朝向、起停与手脚IK；执行顺序来自locomotion，不来自素材列表。
- 路线可提交下述新camera明确固定方位、俯仰、投影或世界position/look_at；无camera时保留旧fixed/0/3.5/50。新placement不支持环绕、近景或动态跟拍，不能填写后忽略并宣称完成。
- 可带静态房间和物品，按 `wm_scene_cast → wm_scene_plan → wm_single_plan` 提交；scene.interactions必须为空。不需要物品时不必添加场景。走到桌旁不等于扶桌，走近门不等于开门或穿门，走到窗前不等于翻窗；只有实际请求操作才添加object_intents并标requires_interaction。
- 通用移动与触摸/开关/翻窗/跳跃/拳脚等的任意串联、自动跨房间穿门导航、楼梯/坡面/梯子、多人路线和编队尚未接入。门洞单次穿越使用下述pass_door专用链，自身复用移动底座，不另提交locomotion。关键要求超出范围时保留全部原文要求并说明限制，不能省略操作后渲染纯移动。翻窗事件已经自带walk_run/run/walk接近，其requires_locomotion为False，不同时提交locomotion。双人战斗move继续用原双人事件协议。
- helper的ok=true只表示计划保存；后台还需独立语义复核、轨迹与脚步/碰撞/构图检查、渲染及成片观看。验证目标是否到达、顺序和转身是否正确，再报告实际执行结果。

### 共用物品与房间协议

同一个自然语言入口还支持桌子、椅子、平开窗、房门和箱子。用户无需选资产、物品ID或内部动作编号。任务提示会注入 `knowledge/objects/catalog.json` 的类型、尺寸与交互目录。

- 人物识别补充 `requires_scene`、`requires_interaction`、`requires_locomotion` 和 `object_intents`。要求房间或物品时requires_scene为true；要求任何物品操作或穿越关系时requires_scene/interaction均为true。静态布景及纯人物动作的object_intents=[]；有操作时按原文顺序填写每项action、object_type和description，包括不支持的操作。通用地面路径移动使用requires_locomotion；真正物品操作自带走近，不因此另标通用移动。
- 已登记物品action为touch_table、open_window、close_window、open_door、close_door、pass_door及受限的vault_window。穿门使用pass_door/object_type=door，开→过→关必须完整保留三项。翻窗、跑步跳跃窗户、从窗洞跳出去使用vault_window及object_type=window，保留“穿过窗洞到另一侧”结果；坐椅、搬拿、破坏等未支持操作也必须保留。后台重新判定支持状态，不能把空间穿越删成普通Walk/Jump加物品布景或改成开关。supported=false后结束，说明缺少的执行能力，不提交缩减需求的计划。
- 顺序为 `wm_scene_cast → wm_scene_plan → wm_single_plan / wm_duel_plan`。调用 `print(bpy.wm_scene_plan(job_id, scene))` 并检查ok。不得省略用户明确要求的物品、数量或操作。
- 场景结构为 version=1、duration_seconds、room={enabled,size:[6,5,3]}、objects=[{id,type}]、interactions=[]；完整字段由任务提示提供。尺寸单位为米；默认位置已给人物留出活动区，无特别要求时省略position。房间为开放正面的展示空间，后墙为门窗生成真实洞口；门窗位于后墙且yaw_deg=0。
- 所有物品由参数化模型生成；窗扇/门板与框架分离，包含独立铰链、把手和接触锚点。桌椅箱子用于布景，不具备未安装的物理行为。
- 静态布景兼容单人已有动作和双人徒手打斗；重要物品与人物一起参与全景构图。单人物品场景可以提交新camera明确固定方位、俯仰、投影与世界位置；无新camera时使用旧固定预设。新placement不支持环绕或近景。旧翻窗默认是沿窗面侧视，不能把它泛称斜侧并认定满足指定角度。
- 单人可扶桌、开窗、关窗、开门、关门。后台使用FK身体来源、手部接触IK、脚部支撑与避障路径；交互时长含走近、伸手、操作、松手。动作顺序由提示词生成，能够连续开关同一个物品。建议单次4至5秒；较远的走近要留更多时间。关门窗需要初始state=open或前一个开启动作。
- 普通五种交互仍提交单人计划，animations只使用真实Idle/Walk路径。此时由交互执行器控制身体与走近，不使用普通单人NLA轮播。翻窗使用下节专用事件和Idle/Walk/Run身体来源。
- requires_scene/interaction/locomotion位于cast根对象，interactions位于scene根对象，人物路径locomotion位于wm_single_plan根对象，禁止放错位置。timing_policy默认auto，在原总时长、顺序与空档内分配安全走近和操作时间；用户明确指定各操作时刻时用exact。实际执行时间表单独保存为executed_scene_plan.json。
- 除专用走跑翻窗链外，物品交互与其它拳脚/跑跳混编、双人物品操作、坐椅、搬拿、拍击桌面、破坏及其它障碍攀爬翻越尚未接入。用户关键要求超出能力时明确反馈，不删去动作后声称完成。普通单人多动作仍有原有混合限制。
- 几何检查覆盖接触误差、支撑、身体与物品的粗略碰撞、关节跳变及全景边距；不等于完整皮肤碰撞、刚体物理或自然度认证，必须提供预览供观看。

### 单人开门、穿门、另一侧关门

理解“打开门，走出去，转身关门”时，人物必须真实穿过门洞再从另一侧操作同一门。普通Walk轮播配门布景、只有开关门或远程关门不能覆盖这个结果。

- 同一自然语言入口，人物A一人，cast.requires_scene=True、requires_interaction=True、requires_locomotion=False。object_intents按实际顺序列出open_door/pass_door/close_door与object_type=door；只要求穿已开门时仅pass，穿后关门时pass/close，不添加用户未要求的开关表演。
- 顺序仍为wm_scene_cast→wm_scene_plan→wm_single_plan。内部完整链未指定时长建议20秒：`interactions=[{"object":"door_1","action":"open_door","start":0,"end":6,"door_side":"inside"},{"object":"door_1","action":"pass_door","start":6,"end":14,"direction":"outward"},{"object":"door_1","action":"close_door","start":14,"end":20,"door_side":"outside"}]`。示例仅字段结构，按用户顺序和时刻规划；明确总时长必须保留，安全时间不足反馈，不能擅自延长。
- direction=outward是inside到outside，默认outward；inward是outside到inside。inside/outside指固定门框局部-Y/+Y，与门扇转动无关。开关门door_side可选inside/outside，省略时按穿越方向和先前事件推导。outward开门在inside、穿后关门在outside，inward相反；不得跨侧瞬移。door_side本轮仅含pass_door链有效，普通无pass的开关门保持旧协议，不填写该字段。
- pass_door至少3秒且门已经open：可初始state=open，或此前open_door事件打开。默认closed门不能无人自动准备打开后删掉开门动作；只要求穿越且未指定门状态时可将初始门规划为open，不表演无人开门。明确要求门保持关闭却穿越、破门后穿越均保留真实意图并说明未支持。
- 单人、同一扇门、最多一次pass；场景全部interactions只能是该门open_door/pass_door/close_door，可有其它静态物品。人物animations仅真实Idle/Walk身体来源，无single.locomotion；专用链内部执行连续接近、接触门扇近铰链真实横拉杆推拉开门、放手、净空穿越、另一侧站稳或转身关门。
- 门扇新增可见双侧横拉杆，接触锚contact_front/contact_back位于近铰链半径0.30米的真实表面，随门扇作小弧运动；旧远端handle和原开关协议保留，不宣称旧把手在大开角全程可达。wm_scene_plan对被pass_door引用的门应用专用缺省：raw对象缺size时填[1.3,0.14,2.1]，缺open_angle_deg时填80；两个字段独立处理，helper不依赖规划者记得补填。明确用户尺寸、厚度、高度和开角均须写入原字段并保留，包括显式0.9米或70度，实际净空失败即拒绝，不能悄悄加宽门或缩小人物。普通未被pass引用的门以及目录资产仍默认0.9米/70度，不受专用缺省影响。
- 新穿门链未指定手侧时side缺省left，规划优先左手接触拉杆；旧无pass交互仍缺省right。用户明确右手时保留right，按实际可达、接触和碰撞验收；不可行则反馈，不暗改手侧。
- 门链无新camera时使用旧世界+X固定正侧剖视全景；指定水平角/俯仰/位置改用下述camera字段，不用旧默认值证明满足用户角度。环绕不属于新placement。普通翻窗旧镜头沿窗面侧视，普通物品旧交互为固定全景。保留明确门尺寸、位置、开角和动作时刻，执行器检查门框/活动门扇/变形皮肤净空、把手可达、落脚和安全时间，不可行时拒绝，不缩人、改门或放宽阈值。
- 门链采用半透明白模参考展示：门扇保留可见轮廓，显示的门框和后墙使用半透明材质，部分墙面为渲染剖切；真实门扇、门框、墙体的碰撞实体与净空检查仍保留。这是为了观察动作两侧的参考图，不承诺真实不透明室内电影镜头也能全程无遮挡。新camera中用户要求真实不透明时采用occlusion_policy=strict，实际全身遮挡不能满足即拒绝，不能偷改透明度；不把半透明成片说成满足真实不透明镜头。
- 仅已保存、验证通过且包含pass_door的单人scene允许人物总时长1至30秒，人物与scene同长；普通单人、翻窗及通用locomotion仍1至15秒。未指定节奏的20秒完整链可先开0..6、过6..14、关14..20秒，auto根据实际安全时间在原总长内分配；明确时刻必须exact并保持，不能高速滑过门洞凑时间。用户明确15秒必须保持15秒，可行则执行，不可行则拒绝，不擅自延长。每次pass前最终状态必须open，先关门后直接穿越不能算覆盖。
- 支持范围仅平地一次门洞穿越，尚不含跑步穿门、拳脚/跳跃、其它物品操作或独立通用路线串联、多人与多次/往返穿门、楼梯坡面及跨房间导航。不能删除关键要求后声称完整实现。
- helper的ok=true仅计划保存，仍需独立语义复核和真实几何、成片验收；核对人物确实来到门另一侧、操作侧正确、关门后的门状态及全身构图，再说明实际结果。

### 单人走跑翻窗协议

用户继续输入自然语言。理解“先走路，再跑步翻越窗户”时，先走后跑、以窗户为目标、撑窗、身体到达窗外和落地必须由同一个翻窗事件实现；普通素材列表顺序不是空间穿越证据。

- 人物为A一人，cast根对象填写requires_scene=True、requires_interaction=True、requires_locomotion=False，object_intents仅有该次翻窗的action=vault_window、object_type=window、description保留操作与穿越结果。翻窗事件已有接近，不同时提交通用locomotion。不能删除原文中的其它关键操作来满足此限制；有显式开窗再翻越、连续翻窗或战斗时说明本轮无法覆盖完整要求。
- 仍按 `wm_scene_cast → wm_scene_plan → wm_single_plan` 提交。scene.interactions必须只有一个vault_window，并将翻窗作为最后动作。内部事件示例为 `{"actor":"A","object":"window_1","action":"vault_window","side":"right","start":0,"end":9,"approach":"walk_run","walk_fraction":0.45}`。side保留协议手侧字段，实际撑窗使用双手。
- approach支持walk_run（先走后跑，默认）、run（直接跑近）、walk（走近后翻越）。按用户原文选择，不能把“先走再跑”改成全程跑步。walk_fraction仅walk_run有效，范围0.2至0.7、默认0.45，表示接近阶段的步行时间比例，不能在run或walk中提交并忽略。接近后由专用FK/IK与连续根轨迹执行双手撑窗、收腿穿洞、落地与站稳。
- 事件至少6秒，未指定时长建议8至10秒；单人总长仍为1至15秒。用户明确指定总时长或动作时刻必须保持；不足时反馈，不能擅自延长。人物计划duration_seconds与scene相同，animations仅使用真实 `mixamo_fbx/basic/Idle.fbx`、`mixamo_fbx/basic/Walk.fbx`、`mixamo_fbx/basic/Run.fbx`，不能用Jump或拳脚素材代替穿越。用户指定斜侧/角度/俯仰等时镜头必须提交single.camera真实字段；旧camera_policy=fixed与0/3.5/50不承载水平方位。
- 窗户保留目录尺寸范围；翻窗另要求宽和高各至少1.0米、窗底高0.55至1.05米。房间窗位于后墙且yaw_deg=0，窗前助跑路径与窗外落地区域不得封堵。保留用户明确尺寸与位置；不通过时拒绝，不能缩放人物、改尺寸或放宽净空阈值。执行器检查变形皮肤穿洞净空、双手接触、连续轨迹、落地和全景边距；建筑剖视仅为展示落地而隐藏相关墙段渲染，实际墙和窗框仍参与检查。
- 上述尺寸仅为初步筛选，不能保证每一种组合可行；实测1.0×1.0米窗洞上沿不足，会拒绝生成。默认1.1×1.2米可作为无指定尺寸时的规划依据，用户明确尺寸不能替换。
- 用户未指定窗状态时，默认closed窗在视频开始前设为打开；这是初始净空准备，写入executed_scene_plan.preconditions_applied及effective_initial_state=open，原scene_plan保留提交状态以绑定复核。不要增加open_window交互、表演无人开窗或说成远距离自动开窗。原文明确要求保持闭窗、撞碎玻璃或先表演开窗再翻越时不能用此前置准备替代；保留break或open_window等真实意图并反馈当前无法覆盖。
- helper的ok=true仅表示计划保存；不能因此宣称穿越与落地已经验收。后台还需独立语义复核、几何执行检查、渲染和成片观看。对照approach顺序、接触、人物确实穿到窗外和落地站稳说明实际结果。

### 独立提示词与计划复核

后台在规划完成后会发起单独的语义复核。收到复核任务时只能调用 `bpy.wm_semantic_review(job_id, review)`，不可重新规划、导入、渲染或修改已提交计划。逐项对照用户原文和真实执行能力，保留全部动作、顺序、目标关系、人数、物品、指定时长与镜头；证据来自实际字段与执行器。翻窗covered必须有唯一vault_window事件及匹配的approach；要求先走后跑时只能以approach=walk_run为证据，不能凭Walk/Run/Jump文件名或静态窗户推断覆盖。默认窗状态的前置净空准备不覆盖独立开窗、保持闭窗或撞碎需求。当前不支持的要求标记unsupported，已支持但遗漏的标记missing，不能凭素材名或规划者自述认定覆盖。helper的ok=true表示保存成功，复核passed只有全部要求covered才为true；未通过会停止渲染。该复核是语言模型对文本计划的检查，几何检查和观看成片仍分别需要完成。

v2复核必须从原文独立重新提取全部明确要求，不能把cast账本当作已经完整。每项requirement_ids绑定账本id，所有账本id至少出现一次，未知id拒绝；独立发现的遗漏要求仍新增项，可用requirement_ids=[]。covered必须提交非空plan_evidence，例如`[{"document":"single_plan.json","path":"camera.azimuth_deg","value":-45}]`；document仅cast.json/single_plan.json/scene_plan.json/plan.json，列表路径可用interactions[0].action。后端精确核对实际字段和值，意图账本、描述、supported、cast.name或能力说明不能证明动作、镜头、服装或音效已执行。unsupported/missing保留原因，plan_evidence可空；v2最多128项，旧review最多32项继续兼容。

只进行一次独立语义判断。helper返回ok=false且明确是schema、证据结构、字段路径或值校验错误时，最多两次仅纠正提交结构和真实字段引用后重交；原要求、语义判断、status与已保存计划均不改，不伪造证据。校验失败不是已经保存的语义失败结论。ok=true即结束，即使passed=false也保留原结论并说明原因，禁止覆盖失败复核。

通用移动covered需要single_plan.locomotion中具体gait、时间、路径和转身字段，以及cast.requires_locomotion=True。逐项核对走跑顺序、转弯/转身、目标位置和停步；“桌旁/门边/窗前”必须与scene.objects实际位置对应，不能仅看描述。普通Walk/Run文件名不算空间关系证据。通用移动与物品操作/翻窗/跳跃/拳脚任意串联、自动跨房间导航、多人路线及移动镜头环绕/近景尚未接入，相应要求unsupported，不要省略后标全部covered。规划复核不认证轨迹物理可行性或动作自然度。

门洞穿越covered需要唯一pass_door事件，open/close有原文要求时分别须有对应事件且同一door。核对direction、固定门框内外侧、穿越前open状态与另一侧关门door_side，不能用普通Walk或静态门推断到达另一侧。requires_locomotion=False且无single.locomotion；此链自己的接近和穿越不另当作通用路线要求。closed门不能自动准备打开，明确开门操作不能省略；只穿初始open门无需虚构开门要求。多次、多人物品关系、破门、独立路线/跑步/拳脚/其它物品混编、楼梯坡面或跨房间导航等范围限制必须保留。

新门链开关通过真实近铰链横拉杆及contact_front/contact_back接触，不宣称原远端handle的大角度全程可达。宽1.3米/开角80度只用于用户未指定对应参数时的建议；明确值必须核对保留，实际身体净空须执行检查。关门后直接pass且无再开门不能covered，另一侧关门必须匹配规范计划door_side，不能用描述代替真实侧关系。

### 旧双人recipe流程（仅适用该构建器时）

两个人、双人、双方攻防的需求使用动态计划与 Blender IK，不使用单人动作轮播。
任务提示会给出 job_id、已安装动作来源和计划结构；根据自然语言生成事件顺序，不使用固定整段套路。
通过 MCP execute_code 调用 `print(bpy.wm_duel_plan(job_id, plan))`，检查 `ok`。
失败时根据 message 修正，最多两次；成功即结束规划。后台任务管理器负责独立创建双人场景、保存、预览、检查、正式渲染与媒体核验。
不在 MCP 内等待整段渲染，不清空共享场景，不用单人 wm_render 生成双人视频。

- 可用：刺拳/直拳、后手重直拳、勾拳、踢击、程序化上勾拳，以及格挡、侧闪、后撤、下潜、迈步和反击。内部 `jab` 对应刺拳，`cross` 对应后手直拳；重直拳优先 `cross`，不要把所有拳法都映射成同一种动作。
- 新刺拳、后手直拳和倒地来源为 Quaternius CC0 动作，经过骨架适配。完整动作提供身体、肩胯、手臂和脚步，手部 IK 在接触窗口作小幅落点修正；不要再把基础拳法压成低权重后用程序化伸手代替。
- 勾拳以实测出拳侧和准备/接触/收招窗口为准，左右互换在内部按骨骼基准姿势镜像。素材名不代表已验证出拳侧或峰值帧。
- 站立击退使用受击动作与后撤脚步层；`Hit_Knockback` 原素材最终倒地，不能当作后退后站稳的反应。参考 T 姿势不能加入表演动作。
- 上勾拳、格挡、闪避与迈步有程序化运动来源；IK 控制落点和支撑，不是凭空生成任意动作的模型。
- 可改变进攻者、顺序、左右侧、头/躯干部位、命中/格挡/闪避和节奏。反击用角色交换后的新攻击事件表达。
- 各攻击只播放一次，保留准备、出招、接触、回收。空档维持格斗姿势。移动用独立 move 事件。
- 接触点从变形后的身体表面获取；出拳在接触前加速，接触后产生短促躯干与头部反应。重拳可后退迈步站稳，下一次攻击自动追近；不要把激烈打斗编排成全部格挡或闪避，除非用户明确要求。
- 持续激烈交锋使用内部 pace=intense，0.8秒内开打，单次攻击0.8至1.4秒，相邻攻击间隔最多0.6秒，优先0.1至0.3秒。击倒后的展示允许保留时间。明确只要少数动作时使用standard，不为填充时长添加无关攻击；用户仍只输入自然语言。
- 命中可指定 reaction=recoil（原地冲击）、stagger（后退站稳）或 knockdown（后仰倒地）；knockdown必须为最后一次交锋，至少留2秒完成倒地，此后保持落地，不能继续攻击或起身再战。
- 两名标准角色，平地徒手，默认固定斜侧全景。明确要求第一视角时使用 cinematic shots，在重击前后短暂切到出拳者第一视角，再切全景观察对手后退或倒地。镜头由自然语言自动编排，用户无需选择模式。第一视角不能同时保证双方全身，全景片段继续检查全身边距。
- 时长独立于镜头，默认15秒，30FPS，最终1280×720。武器、摔投、地面缠斗、多人、起身和外部文本动作模型尚未接入。关键要求不支持时说明限制，不能静默省略。
- 资源、提供器和交互规则位于项目 `knowledge/`；执行结果保存 `output/jobs/<job_id>/` 的 plan、scene、quality、preview 和 result。
- 自动几何检查不等于动作自然度和所有碰撞检查。返回结果时保留预览及视觉核验说明。

### 旧网页单人recipe任务

任务带 job_id 时，优先使用异步单人协议：根据自然语言选择真实动作路径和镜头，调用 `print(bpy.wm_single_plan(job_id, plan))`；返回 ok=true 即结束，不在 MCP 中等待视频。
结构为 version=1、animations=[真实 FBX 路径...]、duration_seconds（普通单人1..15；仅已保存且验证通过的pass_door场景1..30，完整门链未指定建议20秒），以及可选camera。旧camera_policy(fixed/orbit)、pitch_deg、distance_ratio、fov继续兼容；新camera时省略旧字段或只使用fixed/0/3.5/50默认，非默认两套参数会拒绝。未知顶层字段明确报错，不能提交执行器不会读取的镜头或动作参数。
有locomotion的单人任务走通用路线执行器；无locomotion且无物品交互的普通单人后台仍执行原有动作混合与渲染。扶桌、开关门窗和翻窗由对应专用执行器控制，不代表普通单人任意动作都获得统一空间IK。只有明确需要直接操作常驻场景时使用以下原流程。

### 旧单人场景流程（自由任务不调用wm_render）

Blender 里已预置 `bpy.wm_*` helper 函数，你只需按顺序调用，**不要手写底层 Blender API**：

```
1. bpy.wm_clear()                                    # 清空场景
2. bpy.wm_import("mixamo_fbx/<类别>/<动作>.fbx")      # 导入动作（单个 or 多个逗号分隔）
3. bpy.wm_clay()                                     # 白膜材质
4. bpy.wm_lighting()                                 # 三点光
5. bpy.wm_camera(pitch_deg=..., distance_ratio=..., orbit_seconds=..., fov=...)  # 构图（核心！）
6. bpy.wm_render("英文小写名字")                      # 渲染出片（参数自动读取前面的决策）
```

**多动作支持（重要）：**
- `wm_import` 的 fbx 路径支持**逗号分隔多个动作**，如 `"mixamo_fbx/combat_melee/Punching.fbx,mixamo_fbx/combat_melee/Kicking.fbx"`。
- 多个动作会按顺序播放，动作之间使用跨轨道重叠混合（默认 0.4 秒 crossfade）。这能避免淡回默认 T-pose，但不能保证任意动作组合都自然。
- 当用户要求多个动作或明确描述多种招式时，选取覆盖这些意图的多个动作；不要只选一个循环，也不要加入无关招式来凑数量。
- 选材时优先选择起收姿势接近、循环性较好的组合，保留明确的用户意图。
- **当前渲染限制**：渲染器仍会等分动作片段并通过 repeat 填充时长。完整招式编排、自动匹配切换点、根位移对齐和落脚修正尚未实现。不要向用户宣称已实现，不要建议用户靠指定文件名解决这些限制。

**规则：**
- 每个步骤单独一次 `execute_code` 调用，检查返回值里 `ok` 是否为 true；失败则按 message 修正重试。
- `wm_render` 会自动读取前面 `wm_import`（动作）和 `wm_camera`（构图）记录的决策，只需传输出名。
- 渲染这步走 headless GPU，约 8-20 秒（多动作或 15 秒更长约 2-3 分钟），请耐心等 `execute_code` 返回，**不要中途重复调用**。
- 完成后报告：动作名（多个）+ 输出文件 output/xxx.mp4 + 相机参数 + 是否成功。

## 旧wm_camera的pitch_deg构图参数映射表

仅调用旧wm_camera时使用下表；不要把pitch_deg的正负号套到新camera.elevation_deg。新elevation正值表示高机位俯视，负值表示低机位仰视。

| 用户意图 | pitch_deg | distance_ratio | orbit_seconds | fov |
|---|---|---|---|---|
| 平视（默认） | 0 | 2.2 | None（跟随动画） | 50 |
| 仰视 / 从下往上 | +25 ~ +45 | 默认 | 默认 | 默认 |
| 俯视 / 从上往下 / 鸟瞰 | -25 ~ -60 | 默认 | 默认 | 默认 |
| 特写 / 近景 | 默认 | 1.2 | 默认 | 35 |
| 远景 / 全景 | 默认 | 3.5 | 默认 | 50 |
| 慢速环绕 | 默认 | 默认 | 8 ~ 12 | 默认 |
| 快速环绕 | 默认 | 默认 | 2 ~ 3 | 默认 |

### 参数说明

- **pitch_deg**（俯仰角，度）：正=仰视（相机放低向上看，增强力量感/威严感），负=俯视（相机抬高向下看，表现渺小/全局/鸟瞰）。
- **distance_ratio**（环绕半径 = 角色高度 × ratio）：1.2=特写，2.2=默认，3.5=远景。
- **orbit_seconds**（环绕 360° 时长，秒）：None=跟随动画时长（至少 2 秒）。慢环绕给大值（上限 15 秒，超出会被截断）。用户明确要"15 秒"或"长一点"时，直接给 15。
- **fov**（视角，度）：35=长焦特写（压缩空间、突出主体），50=默认，更大=广角。

### 镜头语言进阶（你的导演直觉）

- 仰视 + 特写 → 英雄感/压迫感（适合"雄伟""强大"的角色）
- 俯视 + 远景 → 渺小感/全局感（适合"战场""俯瞰"）
- 特写 + 慢环绕 → 展示动作细节（适合"展示拳法""动作细节"）
- 用户说"环绕"但没提快慢 → 用默认（跟随动画）
- 多个意图叠加时，全部映射到对应参数（如"仰视特写慢环绕"→ +35 / 1.2 / 10 / 35）

## 旧recipe素材选择直觉

- 通用走路/跑步、先走后跑、转弯/转身、目标接近和起停 → single_plan.locomotion及Idle/Walk/Run身体来源；不把素材轮播当作真实目标路径。明确只原地展示动作时，普通Walk或Run/Sprint候选按旧流程选材；Sprint尚不是通用路线执行器的步态档。
- 普通无目标跳跃姿态 → Jump；普通坠落姿态 → Falling。要求穿窗、指定障碍或落点时必须核对空间执行能力，不能仅选这些素材后宣称完成。
- "先走再跑翻窗" → 唯一vault_window事件、approach=walk_run；仅跑近或走近后翻窗分别使用run或walk。上述关系来自完整语义理解，不要求用户写内部动作标识。
- "打拳/格斗" → Boxing 或 Punching（更激烈）；"踢" → Kicking 或 Drop Kick
- "射击/枪" → Shooting 或 Fire Rifle；"瞄准" → Aiming Gun 或 Pistol Aim
- "剑/挥砍" → Slash Advance 或 Swing Dancing
- "施法/魔法" → Casting Spell 或 Spell Casting
- "死亡/倒地" → Death 或 Dying
- 候选均覆盖用户意图时选择最贴近的来源；缺少关键动作或空间关系执行器时保留需求并反馈限制，不用相似动作替代。

## 旧helper踩坑经验（仅该流程适用）

1. **渲染要等**：`wm_render` 走 GPU 渲染 8-20 秒，不要因为"没立即返回"就重试或放弃。
2. **每步单独 execute_code**：不要把多个 `bpy.wm_*` 塞进一次调用，`read_factory_settings` 后 FBX 导入会报错。
3. **检查 ok 字段**：每个 helper 返回 `{"ok": true/false, "message": ...}`，false 时读 message 修。
4. **动作路径要真实存在**：从上面动作库选，不要编造不存在的 FBX 文件名。
5. **输出名英文小写**：`wm_render("run")`、`wm_render("jump")`，与动作对应。
