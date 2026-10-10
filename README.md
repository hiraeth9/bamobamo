# Blender Previs — 白膜预演系统

Hermes Agent 通过 Blender MCP 全权控制 Blender，生成真人短剧的白膜预演视频（blockout previz），作为下游 H3 视频生成（480p → 1080p）的构图与动态参考。

## 架构

```
用户自然语言提示词
   ↓
Hermes Agent（LLM 大脑，qwen3.8-flash）
   ↓ MCP 协议（stdio）
mcp-for-blender server（blender_mcp_server.sh wrapper）
   ↓ TCP socket (localhost:9876)
Blender addon → execute_blender_code → bpy 执行 → 渲染出片
```

核心原则：**Hermes 全权控制** —— LLM 自己理解分镜意图、自己决策 MCP 工具调用、自己用 `look` 校验迭代，不依赖硬编码规则引擎。

## 目录结构

```
blender-previs/
├── frontend/           # Previs Console 前端（输入提示词 + 看进度 + 回放）
│   ├── index.html
│   ├── styles/         # tokens / base / components
│   └── scripts/main.js
├── server.py           # 轻量后端（标准库 http.server，过渡产物）
├── scene_engine.py     # scene.json → bpy 编排引擎（规则版，作为兜底）
├── scene.json          # 示例分镜 DSL
├── hermes/
│   ├── config.yaml     # Hermes MCP 配置（连 blender）
│   ├── SYSTEM_PROMPT.md # 白膜预演导演系统提示词
│   └── AGENTS.md       # Hermes 全局知识（动作库 + 角色 + 运镜 + bpy 要点）
├── scripts/
│   └── blockout_character.py  # 胶囊/方块白膜角色生成（无 skin 时兜底）
└── docs/
    └── blender-previs-architecture.md  # 完整架构设计文档
```

## 关键资产（服务器侧，不随仓库）

- **Blender**: 4.2.19 LTS（`/opt/blender-4.2.19-linux-x64`）
- **Mixamo 角色**: `/mnt/animations/Reloading.fbx`（Beta mannequin，带 skin）
- **Mixamo 动作库**: `/mnt/animations/` 下 166 个动作 FBX（拳击/踢击/闪避/被击等）
- **mcp-for-blender**: `/opt/mcp-for-blender`（本地仓库 + `.venv`）

## 环境搭建（云端 GPU 服务器）

```bash
# 1. 基础工具
apt-get update && apt-get install -y xvfb ffmpeg unzip git

# 2. Blender（南京大学镜像）
wget -O /tmp/blender.tar.xz https://mirror.nju.edu.cn/blender/release/Blender4.2/blender-4.2.19-linux-x64.tar.xz
cd /opt && tar -xf /tmp/blender.tar.xz
ln -s /opt/blender-4.2.19-linux-x64/blender /usr/local/bin/blender

# 3. OpenGL/EGL 库（headless GPU 跑 Blender GUI 必需）
apt-get install -y libegl1 libgl1 libgles2 libglvnd0 libxkbcommon0

# 4. mcp-for-blender（GitHub 镜像加速 clone + venv）
git clone --depth 1 https://gh-proxy.com/https://github.com/ahujasid/mcp-for-blender.git /opt/mcp-for-blender
/opt/conda/bin/python3.11 -m venv /opt/mcp-for-blender/.venv
/opt/mcp-for-blender/.venv/bin/pip install 'mcp>=1.26.0,<2' httpx -i https://pypi.tuna.tsinghua.edu.cn/simple

# 5. 启动 Blender + MCP addon（关键：必须 GUI 模式，addon 在 background 下不启动 socket）
#    addon 复制到 /root/.config/blender/4.2/scripts/addons/blender_mcp.py
#    用 xvfb-run 启动，手动 BlenderMCPServer(9876).start()

# 6. Hermes MCP wrapper
#    /root/blender_mcp_server.sh:
#    export PYTHONPATH=/opt/mcp-for-blender/src
#    exec /opt/mcp-for-blender/.venv/bin/python -m blender_mcp.server "$@"
```

## 关键坑（血泪教训，务必遵守）

1. **addon 在 `bpy.app.background` 下不启动 socket** → 必须 `xvfb-run` GUI 模式 + 手动 start server
2. **FBX 导入器已自动处理朝向/比例**（arm scale 0.01 + rot 90°），**不要 transform_apply**（会破坏动作）
3. **白膜角色蒙皮**：顶点用 armature 局部坐标 + Armature modifier + 顶点组 + identity 对象变换
4. **相机**：用 TrackTo 约束对准空物体，手调欧拉角极易偏
5. **多角色**：导入 Reloading 两次，重命名 armature/mesh 避免冲突，各自挂独立 Action
6. **`server.py` 相对导入**：必须 `python -m blender_mcp.server` + PYTHONPATH 指向 src

## 开箱即用（新机器 3 步部署）

```bash
# 0. clone 项目
git clone <你的仓库地址> && cd blender-previs

# 1. 一键部署环境（Blender + mcp-for-blender + Hermes + 资产）
#    先设置资产下载地址（把 Mixamo 资产打包上传到网盘/GitHub Release）
export ASSETS_URL="https://你的资产包地址/mixamo_assets.tar.gz"
bash setup.sh

# 2. 配置 Hermes LLM（填入你的阿里云百炼 API key）
bash setup_hermes.sh sk-你的API_KEY

# 3. 一键启动 Blender + MCP
bash start.sh

# 4. 自检
bash verify.sh
```

部署完成后，即可用自然语言驱动 Hermes 控制 Blender：

```bash
hermes -z "Use Blender MCP to create a two-character fight white-block video: A uses Elbow_Punch, B uses Getting_Hit..."
```

## 脚本说明

| 脚本 | 作用 |
|---|---|
| `setup.sh` | 一键装环境（Blender/mcp-for-blender/Hermes/依赖/资产），全部国内镜像，幂等可重跑 |
| `setup_hermes.sh` | 配置 Hermes LLM（阿里云百炼）+ MCP 连接 |
| `start.sh` | 一键启动 Blender GUI（xvfb）+ MCP socket + 前端 Previs Console（端口 8899） |
| `verify.sh` | 部署后自检（14 项检查） |

## Mixamo 资产打包说明

资产（166 个动作 FBX + Reloading.fbx 角色）无法通过公开 URL 直接下载（需 Mixamo 登录），需：
1. 本地打包：`tar -czf mixamo_assets.tar.gz -C /mnt animations/`
2. 上传到网盘或 GitHub Release
3. 部署时用 `ASSETS_URL` 环境变量指向它（setup.sh 会自动下载解压到 `/mnt/animations/`）

## 使用方式（进阶）

1. 配好 Hermes 的 `~/.hermes/config.yaml`（LLM provider=custom 指向阿里云百炼 qwen3.8-flash + mcp_servers.blender）
2. 把 `hermes/AGENTS.md` 放到工作目录
3. 用自然语言指令 Hermes，例如：
   ```
   hermes -z "用 Blender MCP 生成双人打斗白膜视频：A 用 Elbow_Punch，B 用 Getting_Hit..."
   ```
4. Hermes 自主通过 MCP 控制 Blender 完成建场景 → 切动作 → 渲染 → 出片
