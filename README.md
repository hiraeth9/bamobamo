# Bamo 自然语言生成动作参考视频

## Linux 首次运行

使用 Linux x86_64、Python 3 和自己的模型账号。Ubuntu/Debian 首次准备系统依赖：

```bash
sudo apt-get update
sudo apt-get install git python3 tmux xvfb libsm6 libice6 libx11-6 libxrender1 libxi6 libxfixes3 libxkbcommon0 libgl1 libegl1 libgl1-mesa-dri
git clone https://github.com/hiraeth9/bamobamo.git
cd bamobamo
cp .env.example .env
```

编辑 `.env`，填自己的 `BAMO_API_KEY`、`BAMO_API_BASE` 和 `BAMO_MODEL`，然后运行：

```bash
./run.sh
```

首次启动会联网获取 Blender、项目指定版本的 Hermes、Python 依赖及必要的工具，安装项目 Skill，并准备仓库随附的核心素材；之后打开 `http://localhost:8090`。启动过程不提交视频任务、不调用付费模型。环境与模型配置保存在当前项目目录，API 密钥不随 Git 提交；更换电脑后填写自己的模型账号即可。默认本地监听，已有远程访问需求可设置 `HOST=0.0.0.0`。

`./run.sh --setup-only` 只准备环境，`./run.sh --check` 只读检查，`bash start_all.sh` 启动已准备的环境。现有服务和任务不会因启动器运行而被清理。首次下载速度取决于网络；已有官方 Blender Linux 压缩包可通过 `.env` 的 `BAMO_BLENDER_ARCHIVE` 指定。

安装条件、下载来源和已验证范围见 [Linux 一键启动与依赖](docs/Linux一键启动与依赖.md)。

## 素材与当前实施路线

按[六阶段实施计划](素材优先动作生成框架六阶段实施计划.md)推进：素材 → 单人基础 → 单人打斗 → 双人基础 → 双人打斗 → 多人。主运动优先复用合适的全身素材，保留原速、准备和收招，再平滑连接；IK 主要修正局部接触与支撑。Hermes 根据自然语言选择素材，合理补全未说明的必要细节，并在 `analysis.assumptions` 中记录影响画面的默认决定。

`bundled_assets/` 保存允许公开再分发的 Quaternius CC0 核心素材和真实蒙皮人物，来源与安装目标见 `knowledge/assets/distribution.json`。这些提供可运行的公开起点，不代表全部动作都已满足真人自然度。现有本机已安装 Beta 时继续使用 Beta；新安装使用独立的 CC0 人物身份，已有任务的人物保持原样。

本机的 Mixamo 和 Rokoko 扩展不会因本次整理删除。它们的原始文件不直接随公开仓库分发：[Mixamo 许可说明](https://community.adobe.com/questions-696/mixamo-faq-licensing-royalties-ownership-eula-and-tos-589400?lang=en)和 [Rokoko 条款](https://www.rokoko.com/terms-of-use)约束原始资产的转发。扩展目录保留检索信息，但运行时按实际文件和适配情况报告可用性，缺文件不会冒称已安装。若自行获取并放回对应路径，仍可作为本机扩展使用。

历史任务、视频、费用记录与模型凭据不包含在公开启动包中。下面保留此前功能和验证记录；脚本测试通过不等于任意自然语言视频或打斗自然度已通过。

## 相机可见性与恢复记录 2026年10月15日

“搬椅子堵门”20 秒案例已重出固定斜侧正交机位视频，完整 600 帧主体入画与遮挡检查通过；原动作、人物和物品保持。新场景保存镜头契约，后台独立重载检查失败时反馈 Hermes 修复。相机检查不等同于动作接触、物理或自然度验收。

当前交接：[项目交接文档_20261015.md](项目交接文档_20261015.md)。验收记录：`reports/robot_camera_release_20261015.json`；视频：`output/agent_be08e914bd8d4726.mp4`。


用户在同一个页面输入自然语言。完整提示词交给Hermes分析，再通过MCP构建、检查和修正真正的Blender场景。新任务默认使用动作库Beta机器人的原始蒙皮和骨架：适合的库动作复用，缺失动作在同一骨架上通过FK/IK和关键帧生成。任何非空提示词先进入分析与构建尝试，不按旧动作白名单、人数、时长或串联限制提前拒绝整条提示词。

已有受限验收的构建器包含单人动作、单人平地路线走跑与转身停步、双人徒手打斗、带桌椅门窗箱子的房间、单人扶桌和开关门窗，以及单人翻窗、开门→穿门→另一侧关门流程。下文的动作、人数和时间范围描述这些已校准构建器，旧v1/v2任务仍兼容；它们不限制自由场景可尝试的需求。自由构建能尝试新动作，不表示任意动作自然度或质量已验证。

双人使用 Blender 手脚 IK、现有动作片段和程序化运动来源，按提示词动态编排攻防。现已支持重拳击退、最后一次交锋击倒，以及按自然语言切换出拳者第一视角。普通单人动作沿用已有混合与渲染流程；单人物品交互使用独立的身体 FK、手部接触 IK 和脚部支撑流程。旧单人版本作为回退基线保留，备份不影响当前统一入口。

单人“开门→穿门→另一侧关门”已完成受限验收：支持平地、同一扇门、一次穿越及近铰链真实拉杆接触，完整双向链已验证左手。门链采用半透明白模参考展示；其它手位、尺寸、角度和时间组合仍以实际检查为准，不等于任意门或不透明电影镜头都已验证。

本次门链的源码、原提示词、视频及验收证据绑定见[交付记录](reports/door_passage_release_20261013.json)；[20秒示例视频](output/single_84a52428866b4000.mp4)可直接查看。

## 自由导演入口（受限验收）

新auto任务使用`generation_contract_version=3`、`execution_strategy=hermes_mcp`。[导演提示](scripts/agent_director.py)将完整原文交给Hermes，分析人物、全部明确需求、时间线、镜头和实现方式；近似、未完成内容与警告保留在analysis中，不作为动作白名单拒绝条件。用户无需提供JSON或动作文件名。

Hermes通过`wm_agent_reference_info`发现已有资源，通过`wm_agent_analyze`保存宽松分析。可直接MCP execute_code构建场景，也可用`wm_agent_build`异步调用已校准recipe。recipe构建成功必须用`wm_agent_load`加载真实场景，再检查、修改；最终`wm_agent_scene`保存真正当前blend，后台从它渲染。自由入口不调用会重建旧计划的`wm_render`；临时driver/handler须bake，保存文件不能依赖当前MCP会话。

相机机位、人物、关键姿态、帧数和可见性根据实际场景核对，工具或预览报错反馈给Hermes修复。新`camera.elevation_deg=+20`表示高机位向下俯视20度，`-20`表示低机位向上仰视；旧`pitch_deg`的朝向角符号不同，不可混用。单人翻窗与三人招手这两例已通过独立检查；能保存、重载和解码仍不能独立证明动作或人物确实出现在画面中。[201项普通回归](reports/hermes_mcp_unit_regression_20261013.json)通过，涵盖20个显式模块，源码和Skill哈希前后一致；本次守护76份代码源及Skill，检查前后哈希一致。这些检查不替代成片验收，早期184项失败与188/193/201项各阶段报告保留各自源码证据。

每次MCP代码自行import并重新查询bpy.data，读取已保存分析，避免依赖上次调用的局部变量。首次构建清理旧场景；反馈修复先加载真实场景并保留正确部分。人物需要可渲染身体几何，骨架和视口overlay不直接参与常规渲染；已出现一个三人任务媒体通过但只有骨架、缺照明而近黑的反例，记录见[独立失败审查](reports/hermes_mcp_visual_19c276a7754646ca_review_20261013.json)，该历史产物保留。

`agent_rig_geometry.attach_rest_proxy`保留作隔离诊断；其[120帧验证](reports/agent_rig_geometry_validation_20261013.json)仅认证分段绑定映射和FK数据保留。新任务成片保持原Beta机器人，不使用该代理替代身体；旧三人proxy视频作为历史证据保留。

另有可选`agent_mesh_geometry.make_box`按给定尺寸与中心创建长方体，使用真实半尺寸顶点及平移，避免把默认primitive大小和scale重复计算；`world_mesh_bounds`测量当前帧evaluated世界网格边界。通过wm_agent_reference_info发现这两个工具，也可直接bpy构建其它形状。实际尺寸正确不表示接触、动作或自然度已验收，不能用metadata中的尺寸替代真实顶点范围。[隔离几何验证](reports/agent_mesh_geometry_validation_20261013.json)已检查实际球网格全120帧位于精确桌面范围及evaluated边界计算；该验证未修改生产场景；最新正式修复成片另有下述独立证据。

[最终验收汇总](reports/hermes_mcp_visual_validation_20261013.json)确认两个原提示词结果：单人先走、再跑、翻窗、落地站稳的[10秒视频](output/jobs/f5afb90d9ff445ea/final.mp4)完成300帧解码，实际固定右前方60度、高机位俯视20度；三人依次用右手在头侧招呼、球在桌面上移动的[4秒视频](output/agent_51fbcbc422364bab.mp4)完成120帧解码。三人最终[独立审查](reports/hermes_mcp_visual_51fbcbc422364bab_review_20261013.json)核对真实身体网格、A→B→C顺序、固定镜头和实际桌面：桌面X为1.6至3.4米、Y为±0.3米，全120帧球体没有离开桌面范围，最小边缘余量约0.02米。球体底部与桌面间隙为0至1.527毫米，属于多面球网格近似，未认证刚体接触或无滑动滚动；白球自转虽有关键帧，画面中不易辨认。三人为分段proxy与FK姿态，未认证多人接触物理、解剖皮肤、任意动作自然度或每个像素无遮挡。

原19c黑画面、51两次桌面失败和辅助视觉超时均保留，详见[桌面出画记录](reports/hermes_mcp_visual_51fbcbc422364bab_before_table_fix_review_20261013.json)与[桌面半尺寸记录](reports/hermes_mcp_visual_51fbcbc422364bab_before_exact_mesh_fix_review_20261013.json)。最终51修复确实以Skill9.2.1及新尺寸工具启动，十项记录的执行源码直到检查结束一致；此前任务各自保留旧来源哈希，不据此改写历史。analysis/result中少量旧TableZ数值仍保留，真实最终桌面Z为0.73至0.77米，以独立evaluated网格检查为准，未认证全部分析字段一致。

项目启动器`hermes_director_cli.py`沿用原Hermes CLI初始化与完整model/provider/toolsets/usage参数。辅助视觉调用最多等待45秒，超时或失败后本进程后续辅助调用立即诚实失败；原生视觉handler保持，未据启动测试声称实际原生提供器调用通过。辅助失败后Hermes继续检查MCP真实数据和实际渲染帧，不反复等待同一接口，也不宣称视觉通过。analysis里的timeline与人物数量是规划自述；实际FPS、帧范围、分辨率和机位来自scene/重载probe，动作顺序和人物可见性仍需实际帧证据。

## 动作库机器人与混合动作（2026-10-14）

固定人物、动作索引、同骨架控制、混合时间线与原recipe接入已完成本地验证。走→跑→停与双人命中→退步→反击→倒地的真实自然语言任务已生成正式视频；走路→指向→走路、三人独立动作这两个案例按用户要求暂停，待GPU可用并收到恢复指令后再执行，阶段5尚未全部验收。实际生成暴露的未登记关键帧被普通commit覆盖、局部捕获清除前段动画和镜头方位误算已修复，并通过真实场景检查。后台代表帧预览已验证；当前运行环境为1核额度、llvmpipe软件绘图，白模可选择Workbench，EEVEE实测仍很慢。Max配置已通过小型调用测试，原提示词、分析与失败尝试均保留。

[走跑停4秒视频](output/agent_e65e6000ec1e44da.mp4)已完成原Beta身份、120帧步态与停止支撑、固定侧视相机及正式媒体检查，见[有限范围验收](reports/robot_actual_intent_20261014/e65_c05cc899eb1f_acceptance.json)。[双人打斗8秒视频](output/agent_5636794fd44a4037.mp4)为1280×720、30FPS、240帧，实际保存重载与完整解码通过；[全240帧独立几何检查](reports/robot_nlp_combat_563_repair_audit_20261014/f6f6cb065165/scene_audit_summary.json)确认原机器人、接触位置、后退、追近、倒地停留与分镜，[正式视频抽帧](reports/robot_actual_intent_20261014/563_f6f6cb065165_final/decoded_frames.json)与实际观看另核对人物和第一视角手臂。仍有第一拳接触处部分遮挡、第一视角拳头靠近下边缘、全景人物偏小和地面接触感弱的问题；不据此宣称任意打斗自然、完整动力学或皮肤无穿插。两项暂停记录见[记录](reports/robot_hybrid_user_pause_waiting_gpu_20261014.json)，全部案例状态见[清单](reports/robot_hybrid_natural_cases_20261014.json)。

`knowledge/characters/catalog.json`与`assets/characters/beta/character.blend`固定原机器人；63个FBX的65骨休止标准、原始网格与蒙皮一致，源FPS为30。`knowledge/motions/catalog.json`包含63个FBX及3个已经接入的GLB片段；目录用于检索候选，未知动作仍交Hermes构建。已有双人知识继续作为专项标注，通用采样器不借用旧IK执行器的接触或自然度通过结论。

通过`wm_agent_actor_ensure/info/adopt`定位或登记真正的人物，`wm_agent_motion_search/info/apply`复用运动，`wm_agent_timeline_event`登记库/程序/混合段，`wm_agent_motion_commit`烘焙全通道；`wm_agent_actor_controls`与`wm_agent_controls_match`提供无拉伸手脚IK。新任务`character_policy=library_beta`在场景保存与独立重载时检查真实资产、绑定与运动证据。人物数量差异给出警告，人物身份与每帧像素可见性分别判断。

`wm_agent_actor_list/remove`用于查看真实登记和清理错误重复人物，保留其他人物、共享数据和物品。真实[人物清理测试](reports/robot_character_remove_20261014/validation.json)确认旧人物及其控制器移除后，另两人的逐帧骨骼、原皮肤及动画不变；物品当前世界位置和保存重载也保持。借用依赖或归属不一致明确返回保留原因，不把隐藏人物当作清理成功。

`wm_agent_locomotion_apply`在现有 Beta 上结合原 Idle/Walk/Run 与双足 IK，连续处理走、跑、减速和落足，保全窗口外运动。[实际步态测试](reports/agent_locomotion_validation_20261014.json)通过120帧轨迹、末三帧双足稳定、其他人物和保存重载检查；首轮收脚失败及修复证据保留。可选工具当前针对平地校准，其他动作仍由 Hermes 自由构建。工具通过不代表自然语言成片或任意运动自然度通过。

已完成的本地证据包括[角色与多人独立性](reports/robot_character_20261014/validation.json)、[动作与重定向采样](reports/agent_motion_validation_20261014.json)、[翻窗/双人原场景捕获并追加动作](reports/robot_recipe_adapters_final_20261014.json)、[249项普通回归](reports/robot_hybrid_unit_regression_final_20261014.json)及[独立有限范围画面审查](reports/robot_hybrid_independent_review_20261014.json)。这些证明原模型、动作接入、衔接数据、IK与保存重载机制，不认证任意复杂动作自然度、击打力度或完整物理。

[局部捕获实测](reports/robot_hybrid_20261014/local_capture_92dec42f2b6a40a2/validation.json)确认修改101—110帧时，其余170帧骨骼、根位置及皮肤保持不变，保存重载误差为零；错误的轨迹与源窗口字段在修改前给出诊断。[公开人物登记接口](reports/robot_helper_adopt_signature_validation_20261014.json)已验证文档中的位置参数及关键字参数调用。[异步预览](reports/agent_preview_validation_20261014.json)检查独立快照、实际PNG及进程状态，预览完成仍需实际画面审查。

新工具只操作指定人物和事件，修复保持其他正确动作。完整`wm_agent_build/load`用于整场recipe构建；已有角色追加运动时先登记、捕获原动画，再增补事件，避免覆盖原场景和时间线。手持物设置`prop_id`或`wm_object_role='prop'`，身体仍保留原蒙皮。用户继续输入自然语言，无需选择文件或填写内部参数。

工具参数与同骨架操作见[机器人混合动作工具说明](docs/机器人混合动作工具说明.md)。性能回归检查全部180帧、约445万变形顶点，投影计算由约50.6秒降至2.4秒，见[性能及转身继承](reports/robot_hybrid_20261014/performance_54a4ea0e3f0f499c.json)。[最新原recipe适配](reports/robot_recipe_adapters_final_20261014.json)确认原翻窗300帧、双人90帧在捕获并追加后保持骨骼和皮肤，重载误差为零。[相机窗口与固定方位](reports/robot_camera_protocol_validation_20261014.json)限定在实际测试范围。

改造前备份位于 `/root/rivermind-data/bamo-backups/robot-hybrid-start-20261014-004337`。预览回退操作可运行 `python scripts/rollback_robot_hybrid.py --backup 该目录`；确认需要回退时加 `--restore`。脚本先归档当前改造文件，恢复改造前的统一自由导演基线，保留任务和视频历史；生产回退需要等待生成结束并在之后重启服务。当前管理41条文件路径，最新实际演练范围与来源见[隔离回退记录](reports/robot_hybrid_rollback_drill_final_20261014.json)，历史38路径报告继续归档保留。备份包含源码、动作/物品资产和既有任务报告，外部二进制、虚拟环境及私密账户配置另行保存，不能视为完整离线系统镜像。

## 使用

在现有环境运行 `bash start_all.sh`，打开端口 8090 的页面。服务由 tmux 与 watchdog 托管，生成任务在后台运行，页面显示进度并支持刷新后重新连接同一任务。

可直接输入：

> 两个人激烈对打15秒，快速交换拳脚，穿插格挡和闪避，但要有有效命中。B用重拳将A打得后退并迈步站稳，最后追近再用重拳把A打倒。最后一拳前切到B第一视角，看到B自己的拳头击中A，随后切回全景看清A后退倒地。全景完整显示双方全身。

“一个人连续出两次拳，再踢腿”识别为一人，“张三向李四进攻，李四格挡反击”识别为两人；Hermes据完整需求选择复用已有recipe或直接构建，不强制分成两个入口。人物数量由语义解析，不把动作次数或姓名中的数字作为人数。

物品交互可输入：

> 生成15秒白模视频：房间里有一张桌子、一把椅子、一扇窗户、一扇门和一个箱子。一个人走近桌子，用右手扶住桌面，然后走到窗前握住把手打开窗户，最后把窗户关上。扶桌稍短，走到窗户并开窗的时间稍长。固定斜侧全景，始终看清人物全身和操作的物品，动作平稳连贯。

翻窗仍使用同一个输入框，例如：

> 一个人先走路，然后跑向窗户，双手撑住窗台，收腿翻到窗外，落地后站稳。生成9秒视频，固定斜侧全景，始终看清人物全身、窗户和落地位置。

这段动作由专用翻窗执行器控制空间路径与接触。它不是普通Walk、Run、Jump素材的轮播；适用范围见下文“常用物品与场景”。

平地移动也使用同一个输入框，例如：

> 一个人先向前走路，再加速跑步，然后减速停住，转身后走回来。生成12秒视频，固定全景，始终完整显示人物全身。

Hermes把描述转换为目的地、路线和时间段；用户无需填写坐标或素材名。执行器根据实际移动距离持续推进步态，控制加减速与朝向，以脚部IK锁定支撑脚。当前支持单人、平地、走/跑/原地转身/停步以及静态家具避障，路线覆盖整段视频的固定全身全景。移动结束的站稳和走跑切换沿用连续足相位，不在每段重新播放起始姿势。普通拳脚、跳跃、开门、翻窗和打斗仍使用各自执行流程，与新路线控制器的任意串联尚未实现。

### 门洞穿越

该通道继续使用同一个自然语言输入框，计划按实际操作顺序保留开门、身体到另一侧与关门三个要求，例如：

> 一个人从室内打开门，走过门洞到门外，转身用左手把门关上，最后站稳。生成20秒视频，固定全景，始终完整显示人物和门。

旧门链recipe未指定时长推荐20秒。该构建器中只有已保存、通过协议验证且包含`pass_door`的单人场景允许人物计划1至30秒；旧普通单人、翻窗和独立路线构建器仍为1至15秒；此上限不约束自由MCP场景。明确要求15秒时保留15秒，安全时间不足直接拒绝，不延长或省略关键操作。未指定动作时刻可先分配开门0至6秒、穿门6至14秒、关门14至20秒，由auto在原总长内按安全时间调整；明确时刻使用exact策略并原样保持。

已验收范围为平地、单人、同一扇门、最多一次穿越；完整双向链默认与正式验收使用左手，其它明确手位须保留并由实际QA判断。门必须初始打开或在穿越前确实执行开门；不会像翻窗一样默默准备打开闭门。inside/outside按固定门框两侧判定，穿越后才可在另一侧关门；无pass的旧开关门协议保持原有行为。专用链自身控制接近与穿越，使用Idle/Walk身体来源，不提交独立locomotion路线。

开关接触门扇近铰链的真实双侧横拉杆，手部IK跟随contact_front/contact_back锚点，脚部与步态沿用连续移动底座。穿门事件引用的门缺省size时，helper填入[1.3,0.14,2.1]米；缺省open_angle_deg时填入80，两字段独立处理。明确尺寸、角度和手侧保持原值，普通门和目录资产默认0.9米/70度不变。门链展示方式为固定正侧剖视的半透明白模参考图：门扇保留轮廓，仍显示的门框和后墙采用半透明材质，局部墙面仅在渲染中剖切；真实门扇、门框和墙体碰撞实体仍参与净空检查。它用于观察人物两侧的动作，不保证真实不透明室内电影镜头也能全程无遮挡。旧普通物品交互保留固定全景，旧翻窗沿窗面固定侧视；指定斜角的新camera通道已完成下述受限验收。专项验收已检查保存重载后的真实皮肤净空、门洞通过、开关侧、手接触、脚部支撑、门状态与全身构图；20秒自然语言任务完成独立语义复核和正式视频完整解码。手工审查配对预览中的每0.5秒抽帧及重点画面，动作顺序和人物全身可辨。原地短步转身、偶发交叉步轮廓和关门前较大前倾仍显机械，尚未认证所有动作自然度。跑步穿门、多人、重复往返、楼梯坡面、跨房间导航和其它动作混编仍未接入。

## 空间镜头与需求证据（受限验收）

固定相机的空间参数已完成受限验收，源码与证据绑定见[交付记录](reports/prompt_camera_release_20261013.json)。自然语言中的“窗户左前方斜看”“俯视15度”或指定相机位置，会保存到单人`single_plan.camera`或双人`camera.placement`，包含参考物品/人物、水平角、俯仰、世界位置与look_at、正交/透视、全身构图及建筑遮挡策略。新角度0表示参考前方-Y、90表示右侧+X，与旧镜头0为+X的约定不同；用户无需填写内部参数。合法参数仍需逐帧构图与遮挡检查，地底相机等不可行设置拒绝。新placement是固定镜头，不增加环绕、动态跟拍或特写；普通单人无新camera时原有orbit与双人原有full/pov分镜继续保留。

旧翻窗镜头实际上沿窗面固定侧视，不能把旧`camera_policy=fixed`称为已满足任意斜角。全身与窗户投影入框也不证明墙体不会挡脸。`transparent_architecture`采用半透明白模建筑且保留碰撞实体；`strict`保留不透明约束，无法满足可见性时拒绝，不能偷偷换角、后移明确相机位置或透明化。新camera与非默认旧pitch/fov/distance/orbit参数混用会报错。

旧auto v2请求采用`generation_contract_version=2`。cast.requirements逐项记录明确动作、顺序、时间、镜头、场景、人物、风格和音频，每项保留原提示词逐字子串；未实现要求也不省略。独立语义复核重新提取原文要求，用requirement_ids覆盖完整账本，covered必须引用实际计划的document/path/value，后端检查字段存在且值完全一致。意图描述、角色名字和能力文案不能单独证明服装、音效、动作或镜头已执行。旧请求与复核记录保持兼容。新规范人物计划固定记录render_style=white_model，标明已有clay白模流程，不扩展RGB、服装、背景或音频。旧自然语言任务原文件不补写该字段，其旧源码与验收证据继续保留。明确人数与身份即使已有actors也需单列，独立复核重新检查原文以减少遗漏；这不是全文理解的数学保证。这轮通过[166项普通测试](reports/camera_contract_unit_regression_20261013.json)，包括25项新相机、需求证据与现有白模元数据边界测试。[实际镜头验收](reports/reference_camera_validation_summary_20261013.json)覆盖翻窗两侧、门链和路线4个保存重载场景共1560人物帧，另有[物品操作150帧](reports/reference_camera_props_probe_20261013.json)、[无场景普通单人120帧构建](reports/prompt_camera_plain_probe_20261013.json)与双人120帧构建。参数探针检查明确位置/look_at保留、过近相机拒绝、strict不透明建筑遮挡拒绝及透明建筑碰撞实体保留；[双人90度侧视](reports/reference_camera_duel_side_rejection_20261013.json)因既有屏幕分离检查拒绝，退出1为预期，不能视为任意人物遮挡的完整认证。

同一原提示词重新生成的[9秒透视视频](output/single_5b2b9148671e4740.mp4)与更详细要求的[10秒正交视频](output/single_4bac9dbc2d7c4496.mp4)均完成独立语义复核、真实几何/镜头检查、正式1280×720/30FPS完整解码，分别270帧与300帧；实际窗户参考方位为-45度、俯视15度。视觉审查分别覆盖18/20张每0.5秒预览抽帧、各19张动作阶段图及各4张正式视频关键帧，报告见[9秒任务](reports/reference_camera_visual_5b2b9148671e4740_review_20261013.json)和[10秒任务](reports/reference_camera_visual_4bac9dbc2d7c4496_review_20261013.json)。走→跑→撑窗穿越→落地站稳与人物/窗户同框可辨；半透明建筑颗粒、浅色窗框、程序化撑窗和团身仍有限制，未认证动作自然度、任意机位或每个像素全程无遮挡。

最后新增render_style仅补现有clay的元数据契约，人数/身份重新提取仅加强提示；没有据此重建两次视频或改写旧计划。[兼容记录](reports/prompt_camera_metadata_compatibility_20261013.json)证明旧计划只在内存规范化增加白模字段，相机、时长与动作保持。各阶段报告绑定各自真实源码哈希，不能声称最新元数据源码与旧视频生成源码完全相同。

## 架构与知识

默认自由入口已完成两个实际任务的受限验收，其场景从MCP构建后直接保存；已校准recipe可作为底座继续编辑。

```text
完整自然语言 → Hermes宽松需求分析 → MCP直接bpy构建 / 可选recipe构建后load
                                         ↓
                  真正场景检查、预览与反馈修复 → wm_agent_scene保存blend
                                         ↓
                            保存场景重载 → 渲染 → ffmpeg核验
```

旧v1/v2任务及专用构建器的流程保留：

```text
自然语言 → Flask 任务提交 → Hermes 人物解析与动作规划
                         ↓
         cast.json（人物、完整需求与物品操作意图）、可选 scene_plan.json 与人物动作计划
                         ↓
          操作覆盖校验 → 独立提示词/计划语义复核与真实字段证据校验
                         ↓
 单人路线与足步 IK / 单人动作混合 / 物品交互 IK / 翻窗 IK / 双人攻防 IK
                         + 门洞链 IK
                         ↓
          场景检查 → 预览 → 正式渲染 → ffmpeg 核验
                         ↓
                    同一页面显示视频
```

| 路径 | 用途 |
| --- | --- |
| web/ | 统一自然语言入口、任务 API、SSE 进度、视频库 |
| scripts/agent_director.py | 完整提示词与实际反馈，指导自由MCP构建、修正和真正场景提交 |
| scripts/hermes_director_cli.py | 保留原Hermes初始化和CLI参数；辅助视觉45秒超时与进程内熔断 |
| scripts/agent_reference.py | 发现已有资源、异步recipe构建、状态查询与真实场景加载 |
| scripts/agent_scene.py、scripts/agent_scene_probe.py | 保存真正场景、需求/分析/文件哈希绑定、重载事实与可复现采样 |
| scripts/render_saved_scene.py | 从提交blend渲染，保留自由编辑内容，不按旧动作计划重建 |
| scripts/agent_rig_geometry.py | 可选rest-space分段身体几何绑定，不改变原FK关键帧 |
| scripts/agent_mesh_geometry.py | 可选精确尺寸长方体和evaluated世界顶点边界测量，不按声明尺寸推断实际几何 |
| scripts/scene_cast.py | 旧v1/v2人物数量、原文需求账本和物品意图；范围不作为自由入口门禁 |
| scripts/scene_intents.py | 保留支持与未支持操作，核对对象类型、动作数量和顺序 |
| scripts/semantic_review.py | 旧v1/v2独立比较原文与计划、核对字段证据；自由场景另按实际数据与画面检查 |
| scripts/scene_plan.py | 共用物品协议、状态顺序、尺寸与位置校验、自动或精确交互时间 |
| scripts/camera_plan.py | 固定相机空间协议校验；方向、位置、投影与遮挡策略 |
| scripts/reference_camera.py | 实际固定机位、全身/关注物品构图、代表部位遮挡与半透明建筑参考展示 |
| scripts/scene_objects.py | 参数化桌椅门窗箱子、独立铰链和锚点、后墙洞口、避障路径与构图检查 |
| scripts/prop_interaction_scene.py | 单人走近、扶桌、握把手开关门窗，身体 FK 与手脚 IK |
| scripts/window_vault_scene.py、scripts/vault_pose.py | 窗户相对的走跑路径、双手支撑、穿洞姿态、落地与变形皮肤净空检查 |
| scripts/door_passage_scene.py | 受限单人开/过/关门链；连续路径、持久门角度、原请求与执行时间分离、质量失败停止 |
| scripts/door_passage_geometry.py、scripts/door_interaction_pose.py | 固定门框内外侧、实际门洞通道及近铰链横拉杆接触、非拉伸手部IK与身体FK |
| scripts/locomotion_plan.py | 平地路线协议、人物意图与实际移动计划的覆盖核对 |
| scripts/locomotion_path.py | 静态避障、折点平滑、距离速度采样、加减速及朝向时间约束 |
| scripts/locomotion_gait.py、scripts/locomotion_scene.py | 持续走跑足相位、步幅匹配、脚尖支撑IK、转身停步、皮肤碰撞与保存场景 |
| scripts/duel_worker.py | 后台任务、队列锁、超时、失败处理、媒体核验 |
| scripts/duel_scene.py | 两个独立角色、动态攻防、实际表面接触、手脚 IK、倒地 |
| scripts/duel_motion_sources.py | 全身 FK 动作采样、GLB 骨架重定向、左右镜像与根位移分离 |
| scripts/duel_impacts.py | 接触帧对齐、加速出拳、短促受击、强弱区分与击退迈步 |
| scripts/duel_cameras.py | 全景与人物第一视角、相机切换与自身头部遮挡处理 |
| scripts/render_white_model.py | 保留单人动作混合与渲染 |
| white-model-director/SKILL.md | Hermes 导演流程与领域规则 |
| knowledge/motions/ | 动作来源、骨架、帧率、阶段、资源 SHA256 |
| knowledge/interaction_rules/ | 命中、格挡、闪避、支撑与检查阈值 |
| knowledge/motion_providers/ | 提供器约定与外部模型接入状态 |
| knowledge/objects/catalog.json | 物品别名、尺寸范围、部件、锚点、交互协议与能力边界 |
| assets/props/generated/ | 5件默认参数化资产的GLB、可编辑Blend与SHA256清单 |
| knowledge/duel_examples/ | 已检查的参考计划，不作为生成白名单 |
| mixamo_fbx/ | 63 个现有动作素材 |
| tests/、reports/ | 回归程序与验收记录 |

Hermes 的专业能力来自语言模型的理解能力、Skill 的导演规则、随任务注入的动作目录与交互知识、Blender 工具，以及检查失败后的反馈。知识并非全部存放在一个 Skill。动作目录和交互规则 JSON 已由规划与执行代码读取；提供器约定说明扩展接口，外部文本动作模型尚未安装。

## 已校准recipe的当前能力

双人支持直拳、勾拳、踢击、程序化上勾拳，以及格挡、侧闪、后撤、下潜、迈步和交换角色后的反击。可根据提示词改变顺序、进攻者、左右侧、目标部位、结果和节奏。攻击只播放一次，空档保持格斗防守姿势。持续激烈交锋会校验攻击间隔，避免长待机填充；只要求少量动作时保留原意。

两名角色拥有独立骨架、网格和空间根节点。手脚使用无拉伸的双骨 IK；命中使用变形后的头部或躯干网格表面，攻击与防守共享世界接触目标，闪避冻结出招目标，支撑脚保持锚点。出拳、接触检查和受击对齐到同一采样帧。轻击产生短促身体反应，重击可后退约0.4至0.6米并迈步站稳；后续攻击自动迈步追近。明确要求最后击倒时，混合兼容的仰面倒地动作，逐步释放站立 IK，落地后保持姿态。未提交新placement时，旧固定斜侧镜头按全片人物网格范围构图，必要时调整默认角度减轻遮挡；新placement保持用户明确方向或位置，不通过换角掩盖不可行要求。明确要求第一视角时，按计划切到该人物眼部相机，保留其出拳手臂并隐藏自身头部的渲染副本，接触后切回全景展示后退或倒地。默认仍使用全身镜头，第一视角段不同时保证双方全身。预览与正式视频来自同一个保存场景。

已有recipe验证了15秒、30FPS、1280×720双人视频。对应构建器参数接受双人 2 至 30 秒、普通单人 1 至 15 秒；已保存且验证通过的单人pass_door专用场景条件上限为30秒。当前主要验收视频为 4、6、15 秒。起身再战、武器、摔投、地面缠斗、三人及以上协同、不同骨架和外部动作生成模型仍需后续实现。IK负责执行约束；自由bpy可构造姿态和运动，但任意动作的接触、支撑与自然度仍需实际校准。自动几何检查通过后仍需观看预览判断自然度。

单人继续使用原有 NLA 混合与等长重定时，不保证任意动作组合自然。固定镜头可独立于视频时长设置，旧直接场景 helper 继续保留。

## 已校准物品与场景recipe

首批已实现桌子、椅子、平开窗、房门、箱子，共用单人/双人入口。物品尺寸以米计，受目录范围约束；位置与朝向可由提示词规划。门窗的框架、活动扇、把手、铰链和接触锚点分离；房间为开放正面的展示空间，后墙按门窗位置生成实际洞口。现阶段房间门窗必须安装于后墙，不能任意换墙或旋转。

普通无pass物品流程可连续扶桌、开窗、关窗、开门、关门，左右手均可；新穿门完整链的左右手验收范围见前文。身体来自Idle/Walk，手部IK追踪活动把手或桌面锚点，脚部IK支持站立与下蹲，走近路径避开家具；身体姿态根据低桌面调整。默认auto时间策略在原总时长、动作顺序与空档内分配走近和操作时间，实际时间表另存 `executed_scene_plan.json`。用户明确指定每个操作时刻时使用exact策略，时间不足直接报错。这些普通物品操作的旧镜头为固定全景，人物与重要物品共同参与逐帧构图检查；新camera可明确方位、俯仰、位置和投影，物品操作已有150帧专项验收。新placement的环绕、特写和动态跟拍仍未接入。

单人翻窗由 `scene_plan.interactions` 中唯一的 `vault_window` 事件执行，包含走近或助跑、双手撑窗、收腿穿过窗洞、窗外落地与站稳。`approach=walk_run` 表示先走后跑，`run` 表示直接跑近，`walk` 表示走近后翻越；先走后跑时可用 `walk_fraction=0.2..0.7` 调整接近阶段的步行时间比例，默认0.45。上述字段由Hermes根据自然语言填写，用户无需选择。人物计划中的Idle/Walk/Run只是身体来源，接近顺序与目标关系由翻窗事件决定。

第一版翻窗仅支持一名标准角色、一次翻窗，并将翻窗作为最后动作；必须是唯一物品交互，不能同时编排显式开窗、扶桌、连续翻窗或打斗。事件至少6秒，未指定时长建议8至10秒；用户指定的总时长和操作时刻保持原值，不足则报错。窗口受原物品尺寸范围约束，并要求宽和高各至少1.0米、窗底高0.55至1.05米；窗前路径与窗外落地区域须可通行。窗口位置和尺寸可按提示词规划，执行器逐帧检查变形皮肤穿洞净空、支撑接触、轨迹与落地，不可行时拒绝，不缩放人物或修改用户明确尺寸来通过。

尺寸门槛是初步筛选，最终须通过实际净空检查；实测1.0×1.0米窗洞上沿不足，任务会被拒绝。默认1.1×1.2米窗户已用于专项验收。

用户未指定窗状态时，默认闭窗在视频开始前设为打开，作为初始净空准备记录到 `executed_scene_plan.preconditions_applied`，其中 `effective_initial_state=open`；原 `scene_plan.json` 保留提交状态以绑定语义复核。视频不表演无人开窗，也不宣称支持远距离自动开窗。明确要求窗户保持关闭、撞碎玻璃，或先表演开窗再翻越时，当前流程不能覆盖，必须保留并反馈对应需求。全景使用建筑剖视显示窗外落地，相关墙段仅隐藏渲染，仍保留净空与碰撞检查。执行器的几何检查不等于完整连续碰撞、刚体物理或动作自然度认证，实际效果仍需观看成片。

静态物品可作为双人打斗的布景，人物在留出的活动区交锋。双人物品操作、打斗与开关门窗混编、坐下、搬拿、拍击、破坏、其它障碍物的攀爬翻越及完整刚体物理仍待实现；家具不会自动具备这些行为。新增类型需要同时补充物品目录、几何提供器、交互锚点与执行规则，以及相应验收，不能只添加别名或一个Skill说明。

旧v1/v2物品recipe要求操作完整记录到cast.object_intents，未支持的操作也保留并由后端判断支持状态。后台核对操作类型、数量与顺序，并在渲染前另用一次Hermes调用复核原提示词与计划，结果保存为semantic_review.json；任一要求缺失或不支持时停止生成。该复核依赖语言模型的语义判断，不等于成片语义认证。普通Walk→Run→Jump配上静态窗户无法实现“先走路，再跑步翻窗”；此要求须对应 `vault_window` 事件及 `approach=walk_run`，缺少事件或接近顺序不符时停止生成，不能用普通跳跃替代。

资产由项目自身生成，此次没有下载第三方家具模型。重建可移植资产及执行物品回归：

```bash
./blender -b -t 8 --python-exit-code 1 --python scripts/build_prop_library.py
./blender -b -t 8 --python-exit-code 1 --python tests/test_props_blender.py
```

物品验收报告为 `reports/props_blender_regression.json`：14项覆盖左右手扶桌、0.6/1.0米桌高、连续开关门窗、五物品连续交互、单人/双人布景与5件GLB重新导入。44项协议和后台测试见 `reports/props_unit_regression.json`。手部检查测量手指根部接触标记与锚点的误差，身体碰撞为骨骼点到实体的粗略检查；这些检查与视频解码核验不能代替观看动作自然度，也不代表完整皮肤或自碰撞物理。

## 尚待校准和完善的基础能力

现有素材、规划规则和交互执行器应分开判断。普通走跑跳姿态可以播放，单人物品走近也有局部二维避障；这不等于已经实现任意场景目标、障碍物和动作组合。翻窗专项流程同样不自动覆盖其它障碍。

| 能力 | 当前缺口 |
| --- | --- |
| 通用移动与动作衔接的扩展 | 已补单人平地目标路线走跑、加减速、转身停步、速度与足相位同步及静态避障；尚未统一跳跃、拳脚、物品交互、翻窗和打斗之间的任意衔接 |
| 多层与跨房间导航 | 单人一次门洞专用链已验收；跨房间、楼梯、台阶、坡面、梯子、动态障碍及重复往返导航仍未实现 |
| 通用跳跃与攀爬 | 按目标落点或障碍尺寸跳过箱子、登上桌面、跨沟、翻围栏、攀墙；单次受限窗户翻越之外尚无相应执行器 |
| 拿放、搬运、推拉 | 单/双手抓握、物品随手附着、承重、指定位置放置、交接、推动和拉动，以及物品状态转换 |
| 坐起与倒地恢复 | 座高和朝向适配、臀脚支撑、坐下再站起、倒地后起身再战；现有末次击倒保持落地 |
| 多人及能力串联 | 自由MCP可尝试多人和动作组合；协同、抓抱、摔投、地面格斗、武器接触等尚无统一校准和质量保障 |
| 完整物理与镜头扩展 | 皮肤级连续碰撞、自碰撞、刚体重量摩擦、通用动力学倒地和破坏；物品场景分镜/第一视角/环绕/特写及动态遮挡控制 |
| 任意新动作生成 | Hermes可直接用骨架与关键帧程序化构建；外部文本动作模型未接入，任意复杂运动的接触、支撑和自然度不保证 |

已下载的Quaternius素材中有 `PickUp_Table`、`Walk_Carry_Loop`、`Sitting_Enter/Exit`、`LayToIdle`、`ClimbUp_1m` 和分段跳跃/落地等，它们可作为后续来源，尚不能当作相应物品关系能力已经完成。单人受限门洞链已补，后续优先补抓取搬运放置、坐起与恢复，以及不同尺寸障碍的穿越。带代码与素材证据的盘点见 `reports/basic_capabilities_inventory_20261013.json`。

## 验证

门洞链的交付记录（2026-10-13）：[普通测试138/138](reports/door_passage_final_unit_20261013.json)、[门几何13项](reports/door_passage_geometry_regression_20261013.json)、[门链Blender回归10/10](reports/door_passage_blender_regression_20261013.json)。6个正例在保存重载后共检查2520帧，覆盖双向完整链、初始开门只穿/穿后关门及旋转独立门；4个拒绝例保留明确窄门、小开角、封堵门洞和不足的exact时刻。最终门链回归期间源码未变，报告记录对应源码哈希。旧[物品14项](reports/door_passage_legacy_props_regression_20261013.json)与[移动11项、翻窗9项](reports/door_phase_locomotion_vault_legacy_recheck_20261013.json)回归仍通过，各阶段报告保留各自来源记录。

真实自然语言任务的[正式视频](output/single_84a52428866b4000.mp4)为20秒、1280×720、30 FPS、600帧；[配对预览](output/jobs/84a52428866b4000/preview.mp4)用于人工抽帧审查。[视觉记录](reports/door_passage_visual_review_84a52428866b4000_20261013.json)包含40张间隔0.5秒画面及9个重点时间，确认开门→实际过门→另一侧转身关门→站稳的顺序和人物可见性；完整正式视频另做全600帧解码。该审查限于半透明参考展示与所列左手场景，不认证任意手位、所有参数或完整动作自然度。失败阶段保留在任务的attempts目录，提交计划未被执行时间分配覆盖。

平地移动专项检查保存重载后的真实轨迹、足步支撑、转向、静态物品避障和不可行路线拒绝：

```bash
env -u DISPLAY -u XDG_RUNTIME_DIR -u GHOST_BACKEND -u WAYLAND_DISPLAY ./blender -b -t 2 --python-exit-code 1 --python tests/test_locomotion_blender.py
/opt/conda/bin/python -m unittest discover -s tests -p 'test_locomotion_plan.py'
/opt/conda/bin/python -m unittest discover -s tests -p 'test_locomotion_path.py'
```

协议保存原始时间和分段终点；中间路线点允许在0.35米以内平滑转弯，必须准确到达的位置应设为分段终点。实际路径、时间分配与必要提醒另存 `executed_locomotion_plan.json`，不覆盖提交计划。该旧locomotion构建器总时长为1至15秒；每段至少0.5秒，转向速度最多120度/秒，步行和跑步速度上限分别为1.65和3.5米/秒。时间或净空不足时拒绝，不延长用户指定的时长。动作观感仍须查看预览；几何与媒体检查不自动认证全部自然度。

路线以最终站稳结束；尾部可见静止不足0.4秒时，从最后活动原时段内部预留不足部分收脚，并记录实际到点或转身完成时刻。已有充分停步时间则保留原移动安排，同向走跑交界保持连续速度。持续移动直到最后一帧的结束状态尚未实现。

2026-10-13 平地移动阶段的验收包含117项普通测试、11项移动Blender回归、9项原翻窗与14项原物品回归，以及自然语言生成的12秒走→跑→停→转身→走回视频 `output/single_add98168ac5e4d22.mp4`。移动专项在保存重载后独立测量真实根轨迹、脚部支撑/滑移、变形皮肤地面、关节跨帧旋转及全身镜头，末段贴视频边界也要求双脚落稳。汇总见 `reports/locomotion_release_20261013.json`。原地转身目前由短步Walk改造，仍有较明显抬膝和少量交叉步；这项验收确认执行、连续性和几何约束，动作观感须结合成片判断。

翻窗专项检查实际变形皮肤、双手支撑、保存重载后的穿越和落地，以及不可行请求的拒绝：

```bash
env -u DISPLAY -u XDG_RUNTIME_DIR -u GHOST_BACKEND -u WAYLAND_DISPLAY ./blender -b -t 8 --python-exit-code 1 --python tests/test_window_vault_blender.py
/opt/conda/bin/python -m unittest discover -s tests -p 'test_window_vault_plan.py'
```

```bash
/opt/conda/bin/python -m unittest discover -s tests -p 'test_*plan.py' -v
/opt/conda/bin/python -m unittest discover -s tests -p 'test_scene_cast.py' -v
/opt/conda/bin/python -m unittest discover -s tests -p 'test_web_jobs.py' -v
/opt/conda/bin/python -m unittest discover -s tests -p 'test_duel_impacts.py' -v
env -u DISPLAY -u XDG_RUNTIME_DIR -u GHOST_BACKEND -u WAYLAND_DISPLAY ./blender -b --factory-startup --python-exit-code 1 --python tests/test_animation_sequence.py
env -u DISPLAY -u XDG_RUNTIME_DIR -u GHOST_BACKEND -u WAYLAND_DISPLAY ./blender -b --factory-startup --python-exit-code 1 --python tests/test_duel_blender.py -- --full
```

重击与镜头的 Blender 集成检查额外覆盖双方视角、击倒保持、追击、非命中不击退，以及保存重载后的相机和自身头部可见性：

```bash
env -u DISPLAY -u XDG_RUNTIME_DIR -u GHOST_BACKEND -u WAYLAND_DISPLAY ./blender -b --python-exit-code 1 --python tests/test_duel_impact_blender.py
```

自然语言回归使用真实 Hermes 与 Blender，需要模型配置和运行中的 MCP 服务：

```bash
/opt/conda/bin/python tests/run_duel_natural_regression.py --limit 20 --repeats 3 --workers 4
/opt/conda/bin/python tests/recheck_duel_regression.py
```

96 组固定计划覆盖 4 种攻击、3 种结果、双方进攻、头部和躯干、左右侧。自然语言回归是 20 条提示词，每条在一次 Hermes 会话中生成 3 份计划，共 60 份，逐帧执行与保存检查；完整视频编码由独立代表任务验证。60 份计划不代表 60 次独立语言模型会话或 60 部完整视频。

增强版另通过8组重击与镜头检查、60份既有自然语言计划复检，并使用Max验证了12次交锋、B第一视角、A最终倒地的15秒完整任务。输入、计划和质量见 `reports/duel_impact_natural_result.json`；首版历史结果保存在 `reports/v1/`。

2026-10-13 的全身 FK 版本修复了连续受击后反击的距离预测，以及后退步态切换踢击时的支撑脚跳变。刺拳、后手直拳和倒地使用已下载的 Quaternius CC0 动作，接触窗口保留手部 IK；原生踢击姿态在重定时前进行对称平滑。此次检查覆盖 12 组基础攻防、6 组后手直拳、8 组冲击与镜头，以及 3 份真实失败的 15 秒计划。连续动作回归执行 `./blender -b -t 8 --python-exit-code 1 --python tests/test_duel_sequences_blender.py`，报告为 `reports/duel_sequences_regression.json`。

## 保存与回退

GitHub 仓库为 https://github.com/hiraeth9/bamobamo 。单人基线标签 `single-character-v1-20261012` 和双人首版标签 `dual-character-ik-v1-20261012` 保留；双人功能保存到 `feature/dual-character-fight` 分支，重击与镜头增强版标签为 `dual-character-impact-v2-20261012`。这些是开发历史，网页仍只有一个入口。

当前机器的完整私有快照在同级 `bamo-backups/`，包含项目运行环境、动作、视频、文档和选定的外部配置。旧单人快照目录为 `20261012-101959-single-character-v1`。核验与回退使用新版保护脚本：

```bash
# 默认只核验，不修改当前项目
/opt/conda/bin/python scripts/rollback_to_single.py
# 明确需要回退时执行；会拒绝正在运行的生成任务，并保留回退前目录
/opt/conda/bin/python scripts/rollback_to_single.py --restore
```

公开 GitHub 源码包包含 FBX、框架源码、工具、规则、测试和文档；不包含凭据、生成视频、Blender 二进制、ffmpeg 环境和 Hermes 虚拟环境。仅克隆仓库不能在新电脑直接运行。现有启动脚本及部分旧 helper 仍使用 `/root/rivermind-data/bamo`，运行依赖 Blender 5.2.2、Python 3.12、Flask、ffmpeg、Hermes、uv、tmux、Xvfb 与显卡驱动。

## 第三方来源

Hermes 与 Blender MCP 保留原源码及许可，来源与版本见 SOURCE.json。Mixamo 资产适用原素材的使用条件，本项目不重新授予第三方代码或素材许可。实现细节、验收范围与后续工作见《双人打斗实现与验收记录》。
