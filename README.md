# Bamo 自然语言生成动作参考视频

用户在同一个页面输入自然语言，Hermes 先识别人物数量、身份、动作与镜头需求，再选择内部生成流程。当前可生成单人动作和双人徒手打斗；用户无需选择版本、素材文件或动作菜单。三人及以上会保留识别到的人数并明确返回能力限制。

双人首版使用 Blender 手脚 IK、现有动作片段和程序化运动来源，按提示词动态编排攻防。单人沿用已有动作混合与渲染流程。旧单人版本作为回退基线保留，备份不影响当前统一入口。

## 使用

在现有环境运行 `bash start_all.sh`，打开端口 8090 的页面。服务由 tmux 与 watchdog 托管，生成任务在后台运行，页面显示进度并支持刷新后重新连接同一任务。

可直接输入：

> 两个人打斗，A连续两次直拳进攻，B先侧闪再格挡，接着B右腿踢向A腹部，A受击，A用上勾拳反击被B格挡，然后双方交换勾拳与闪避，最后拉开距离保持戒备。15秒，始终完整显示两人的全身。

“一个人连续出两次拳，再踢腿”会使用单人流程；“张三向李四进攻，李四格挡反击”会识别为两个人。人物数量由语义解析，不把动作次数或姓名中的数字作为人数。

## 架构与知识

```text
自然语言 → Flask 任务提交 → Hermes 人物解析与动作规划
                         ↓
              cast.json 与所选动作计划
                         ↓
          单人动作混合 / 双人独立骨架与 IK
                         ↓
          场景检查 → 预览 → 正式渲染 → ffmpeg 核验
                         ↓
                    同一页面显示视频
```

| 路径 | 用途 |
| --- | --- |
| web/ | 统一自然语言入口、任务 API、SSE 进度、视频库 |
| scripts/scene_cast.py | 人物数量与身份协议，不支持人数的明确反馈 |
| scripts/duel_worker.py | 后台任务、队列锁、超时、失败处理、媒体核验 |
| scripts/duel_scene.py | 两个独立角色、动态攻防、手脚 IK、全身构图 |
| scripts/render_white_model.py | 保留单人动作混合与渲染 |
| white-model-director/SKILL.md | Hermes 导演流程与领域规则 |
| knowledge/motions/ | 动作来源、骨架、帧率、阶段、资源 SHA256 |
| knowledge/interaction_rules/ | 命中、格挡、闪避、支撑与检查阈值 |
| knowledge/motion_providers/ | 提供器约定与外部模型接入状态 |
| knowledge/duel_examples/ | 已检查的参考计划，不作为生成白名单 |
| mixamo_fbx/ | 63 个现有动作素材 |
| tests/、reports/ | 回归程序与验收记录 |

Hermes 的专业能力来自语言模型的理解能力、Skill 的导演规则、随任务注入的动作目录与交互知识、Blender 工具，以及检查失败后的反馈。知识并非全部存放在一个 Skill。动作目录和交互规则 JSON 已由规划与执行代码读取；提供器约定说明扩展接口，外部文本动作模型尚未安装。

## 当前能力

双人支持直拳、勾拳、踢击、程序化上勾拳，以及格挡、侧闪、后撤、下潜、迈步和交换角色后的反击。可根据提示词改变顺序、进攻者、左右侧、目标部位、结果和节奏。攻击只播放一次，空档保持格斗防守姿势。

两名角色拥有独立骨架、网格和空间根节点。手脚使用无拉伸的双骨 IK；攻击与防守共享世界接触目标，闪避冻结出招目标，支撑脚保持锚点。固定斜侧镜头按全片人物网格范围构图，必要时调整固定角度减轻遮挡。预览与正式视频来自同一个保存场景。

已验证 15 秒、30 FPS、1280×720 双人视频。参数接受双人 2 至 30 秒、单人 1 至 15 秒；当前主要验收视频为 4、6、15 秒。武器、摔投、倒地地面缠斗、三人及以上协同、不同骨架和外部动作生成模型仍需后续实现。IK 负责执行目标，不具备凭空生成任意动作的能力。自动几何检查通过后仍需观看预览判断自然度。

单人继续使用原有 NLA 混合与等长重定时，不保证任意动作组合自然。固定镜头可独立于视频时长设置，旧直接场景 helper 继续保留。

## 验证

```bash
/opt/conda/bin/python -m unittest discover -s tests -p 'test_*plan.py' -v
/opt/conda/bin/python -m unittest discover -s tests -p 'test_scene_cast.py' -v
/opt/conda/bin/python -m unittest discover -s tests -p 'test_web_jobs.py' -v
env -u DISPLAY -u XDG_RUNTIME_DIR -u GHOST_BACKEND -u WAYLAND_DISPLAY ./blender -b --factory-startup --python-exit-code 1 --python tests/test_animation_sequence.py
env -u DISPLAY -u XDG_RUNTIME_DIR -u GHOST_BACKEND -u WAYLAND_DISPLAY ./blender -b --factory-startup --python-exit-code 1 --python tests/test_duel_blender.py -- --full
```

自然语言回归使用真实 Hermes 与 Blender，需要模型配置和运行中的 MCP 服务：

```bash
/opt/conda/bin/python tests/run_duel_natural_regression.py --limit 20 --repeats 3 --workers 4
/opt/conda/bin/python tests/recheck_duel_regression.py
```

96 组固定计划覆盖 4 种攻击、3 种结果、双方进攻、头部和躯干、左右侧。自然语言回归是 20 条提示词，每条在一次 Hermes 会话中生成 3 份计划，共 60 份，逐帧执行与保存检查；完整视频编码由独立代表任务验证。60 份计划不代表 60 次独立语言模型会话或 60 部完整视频。

## 保存与回退

GitHub 仓库为 https://github.com/hiraeth9/bamobamo 。单人基线标签 `single-character-v1-20261012` 保留；双人功能保存到 `feature/dual-character-fight` 分支。这些是开发历史，网页仍只有一个入口。

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
