# Bamo · 自然语言生成白膜参考视频

当前单角色版本：用户输入自然语言，Flask 调用 Hermes；Hermes 加载白膜导演技能，通过 Blender MCP 选择动作与镜头，最终由 headless Blender 和 ffmpeg 生成 MP4。

本目录是从当前运行项目整理的源码与动作资产版本，包含 63 个 FBX、Hermes 框架源码、导演 Skill、Blender helper、网页、测试和汇报文档。具体版本与范围见 SOURCE.json。

## 目录

| 路径 | 用途 |
| --- | --- |
| web/ | 网页和 Flask 后端，端口 8090 |
| scripts/ | MCP helper、动作轨道编排、headless 渲染 |
| white-model-director/ | 白膜导演领域规则 |
| mixamo_fbx/ | 63 个动作素材 |
| blender-mcp/ | 当前 addon 和原始许可 |
| hermes/ | 当前框架源码与原始许可，不含其虚拟环境 |
| tests/ | 动作编排回归测试 |
| 汇报配图/、项目汇报文档.* | 架构、流程、知识系统说明 |
| config/、.env.example | 供自行填写的配置示例 |

## 运行与恢复

当前代码的项目路径固定为 /root/rivermind-data/bamo。首次克隆到其他路径时，需要调整启动脚本、helper、后端和 Skill 中的项目路径。

运行依赖 Python 3.12、Flask 3.1.3、Blender 5.2.2、ffmpeg、uv/uvx、tmux、Xvfb 及显卡驱动。Hermes 自带源码需要安装其独立环境；本仓库不包含 Blender 二进制、ffmpeg-env、Hermes .venv、生成视频和真实模型凭据。仅克隆源码不等于完整环境恢复。

在当前机器恢复整套环境，使用现有完整快照及其回退脚本；在新机器部署时，另行准备上述运行环境并填写自己的模型配置。可用源码测试：

```bash
./blender -b --factory-startup --python tests/test_animation_sequence.py
```

环境准备后，部署导演技能至 ~/.hermes/skills/creative/white-model-director/，配置 Blender MCP，再执行：

```bash
bash start_all.sh
tmux new-session -d -s watchdog 'cd /root/rivermind-data/bamo && bash watchdog.sh'
```

## 当前能力与限制

- 自然语言自动选材、多个动作顺序播放、全身镜头默认构图、最长 15 秒环绕与出片。
- 动作跨轨道混合已避免切换时淡回默认 T 姿势，仍不能保证任意动作组合自然。
- 当前保留一个角色，多人交互、自动姿势匹配、根位移对齐和落脚修正尚未实现。
- 渲染器仍等分动作片段并循环填充；时长与环绕参数尚未独立。

完整解释见项目汇报文档。上传步骤见 GitHub上传说明.txt；Gitee上传说明.txt 保留作为可选镜像说明。当前包已剔除原始私密交接、真实配置与凭据；运行配置由本机私有备份保存。

## 第三方来源

Hermes 源码保留 hermes/LICENSE 和各组件原许可。Blender MCP addon 来自 ahujasid/blender-mcp，版本与许可见 SOURCE.json 和 blender-mcp/LICENSE。动作素材来自现有 Mixamo FBX 资产，使用条件随原素材来源适用。此上传快照不重新授予第三方代码或素材许可。
