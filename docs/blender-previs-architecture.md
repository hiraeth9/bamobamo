# 通用白膜预演系统架构设计

> Hermes Agent + Blender MCP 控制 Blender 生成白膜预演视频
> 面向通用场景（打斗为高频场景之一，但不限于 2 人）
>
> **硬性约束：整个项目的运行时控制权必须由 Hermes Agent 通过 MCP 掌握。**

---

## 1. 目标与边界

**目标**：Hermes 通过 Blender MCP 编排 N 个角色 + 相机运镜，输出白膜预演视频，作为下游 H3 视频生成的构图/动态参考。

**硬性约束**：运行时编排（摆场景/切动作/设相机/设灯光）必须全程由 Hermes 通过 MCP 下发，Agent 是「导演」，MCP 是「手」。

**边界**（三类操作性质区分）：
| 操作 | 控制方 | 说明 |
|---|---|---|
| 摆场景/切动作/设相机/设灯光 | **Hermes 通过 MCP** ✅ | 运行时编排，必须 Agent 决策 |
| 一次性资产导入（Mixamo→Action 库） | 离线脚本 | 属「建资产」，非「编排场景」 |
| 渲染出片 | headless 进程 | MCP socket 在 `-b` 模式不稳，渲染是耗时批处理 |

- 渲染走 headless `blender -b` → 帧序列 → ffmpeg 合成
- 动作由 Mixamo 预置动作库提供，**不做关键帧手搓，不做骨骼重定向**

---

## 2. 核心设计原则

1. **角色、动作、编排三者彻底解耦** —— 不写死人数，不写死动作序列
2. **Hermes 负责「想」，MCP 负责「做」** —— 运行时 Hermes 通过 MCP 逐步执行并迭代修正
3. **动作库一次建好，永久复用** —— 所有动作沉淀为命名 Action 资产
4. **对齐 H3 参数** —— 分辨率 864×480、帧率 24fps（打斗可 30fps）

---

## 2.5 MCP 选型（关键决策）

**选定 `mcp-for-blender`**（GitHub: ahujasid/mcp-for-blender，Blender 3.0+）。

核心原因：它提供 **`execute_blender_code` 万能工具**，Hermes 可下发任意 `bpy` 代码，天然满足「全程 MCP 控制」且无需改 addon 源码。

**工具清单**（9 个，来自官方 Capabilities）：
| 工具 | 用途 |
|---|---|
| `execute_blender_code` | ⭐ 执行任意 Blender Python（导入 FBX/切 Action/编排全靠它） |
| `look` | 从视口/相机/多角度/帧查看结果 |
| `get_scene_info` | 获取场景摘要（位置/材质/拓扑） |
| `generate_3d` | Tripo/Hunyuan3D/Rodin 生成 3D 模型 |
| `search_assets` | 搜 Poly Haven/Sketchfab/Poly Pizza 资产 |
| `import_asset` | 导入搜索到的资产 |
| `get_addon_status` | 版本/库/生成器状态 |
| `disable_telemetry` | 遥测开关 |
| `record_trajectory_feedback` | 轨迹反馈 |

**通信**：addon 在 Blender 内起 TCP socket 服务（默认 `localhost:9876`），MCP server 作为客户端连入，JSON 协议（`{type, params}` / `{status, result}`）。无鉴权，仅限 localhost。

**Hermes 控制闭环**（感知→决策→执行→校验→修正）：
```
Hermes（导演，全程在线）
 ① get_scene_info          —— 感知当前场景
 ② execute_blender_code    —— 导入角色/切 Action/设相机（决策后执行）
 ③ look / get_scene_info   —— 校验结果
 ④ 不符合预期 → 回到 ② 迭代修正
 ⑤ 场景就绪 → 触发 headless 渲染
```

---

## 2.6 Hermes 接入方式（关键决策）

**已确认：Hermes 直连 MCP** —— Hermes 作为 MCP **client**，直接连接 mcp-for-blender server，通过 MCP 协议调用工具（`execute_blender_code` / `get_scene_info` / `look` 等），全权决策每一步操作。

**Hermes 在架构中的位置**：
```
前端工作台（用户提示词）
   ↓
Hermes Agent（LLM 大脑）← 中间层，理解意图 + 生成 scene.json + 决策 MCP 调用
   ↓ MCP 协议（stdin/stdout 或 SSE）
mcp-for-blender server（MCP 工具实现）
   ↓ TCP socket (localhost:9876)
Blender addon → 执行 bpy → 渲染出片
```

**关键澄清**：
- Hermes 不是"某一层"，而是贯穿链路的**大脑**，位于前端和 Blender 之间
- Hermes 通过 MCP 协议连接 `mcp-for-blender`（`uvx mcp-for-blender`），后者再走 socket 连 addon
- 前端（Previs Console）是 Hermes 的**输入入口和结果展示**，Hermes 是真正的执行决策者
- 当前 `server.py` 是过渡产物：它用硬编码逻辑模拟了"Hermes 生成分镜"这一步，最终应由真实 Hermes 取代

**Hermes 具体实现**：Nous Research 的 [hermes-agent](https://github.com/nousresearch/hermes-agent)（MIT，25万 star），CLI + gateway 双形态，支持 MCP、多个 LLM 后端（Nous Portal/OpenRouter/OpenAI/自建端点）。

**Hermes 侧 MCP 配置**（`~/.hermes/config.yaml`，YAML 格式）：
```yaml
mcp_servers:
  blender:
    command: "uvx"
    args: ["mcp-for-blender"]
    env:
      BLENDER_HOST: "127.0.0.1"
      BLENDER_PORT: "9876"
    enabled: true
```

**验证方式**：
- `hermes mcp test blender` —— 查看 server 实际响应
- 启动日志 `agent.log`：`MCP: registered N tool(s) from M server(s)`
- 对话中 `/reload-mcp` 重载工具列表

**Hermes 触发方式**（前端 → Hermes 的桥）：
- Hermes 有 `hermes gateway start`（消息网关，长驻进程，对接 Telegram/Discord/Slack 等）
- 前端可通过 gateway 的消息通道触发 Hermes，或直接 CLI 交互
- 前端（Previs Console）负责展示进度 + 回放，Hermes 负责编排决策

---

## 3. 三层架构

```
【资产层】离线准备，一次性
├─ 角色库  assets/characters/
│    ├─ char_A.blend   （Mixamo 人形，白模材质）
│    └─ ...N 个角色
└─ 动作库  assets/actions/
     ├─ punch.fbx / kick.fbx / dodge.fbx / walk.fbx / idle.fbx ...
     └─ 每个动作 = 一个命名 Action，可复用到任意 Mixamo 人形

【编排层】Hermes + Blender MCP，运行时
├─ 输入：场景描述 JSON（scene.json）
├─ 解析：N 个角色轨道 + 相机轨道
└─ 执行：加载角色 → 编排动作 clip → 设相机 → 设灯光

【出片层】headless 渲染
└─ blender -b scene.blend -E BLENDER_WORKBENCH -a → PNG → ffmpeg → MP4
```

---

## 4. 场景描述 DSL（scene.json）

这是 Hermes 与 MCP 之间的**唯一接口契约**。通用性体现在：
- `characters` 数组长度 = 角色数（1 到 N 任意）
- 每个角色一条 `track`，动作 clip 可任意组合
- `camera` 是独立轨道

```jsonc
{
  "meta": {
    "name": "fight_scene_01",
    "fps": 24,
    "resolution": [864, 480],
    "duration": 3.2
  },
  "characters": [
    {"id": "char_0", "asset": "char_A", "position": [0, 0, 0], "rotation": [0, 0, 0]},
    {"id": "char_1", "asset": "char_B", "position": [2, 0, 0], "rotation": [0, 180, 0]}
  ],
  "tracks": [
    {
      "char": "char_0",
      "clips": [
        {"action": "walk",  "start": 0.0, "end": 2.0},
        {"action": "punch", "start": 2.0, "end": 3.2}
      ]
    },
    {
      "char": "char_1",
      "clips": [
        {"action": "idle",  "start": 0.0, "end": 2.0},
        {"action": "dodge", "start": 2.0, "end": 3.0}
      ]
    }
  ],
  "camera": {
    "clips": [
      {"type": "tracking", "target": "char_0", "start": 0.0, "end": 2.0},
      {"type": "shake",    "target": "char_0", "start": 2.0, "end": 3.2, "intensity": 0.6}
    ]
  }
}
```

### DSL 字段说明

| 字段 | 说明 |
|---|---|
| `characters[]` | 场景中的角色实例（数量不限），含资产名、初始位姿 |
| `tracks[]` | 每个角色的动作轨道，由多个 `clip` 按时间轴拼接 |
| `clip.action` | 动作库中的 Action 名（walk/punch/dodge...） |
| `camera.clips[]` | 相机轨道，支持 tracking/shake/static/pan 等类型 |

---

## 5. 相机运镜类型（打斗场景重点）

| 类型 | 用途 | 参数 |
|---|---|---|
| `static` | 固定机位 | 位置/朝向 |
| `tracking` | 跟随目标 | target + 跟随偏移 |
| `pan` | 摇移 | 起止角度 |
| `shake` | 手持震动 | intensity 0~1 |
| `cut` | 硬切（多机位） | 切换点 |

打斗场景运镜原则：**快切 + 摇移 + 手持震动** 增强临场感。

---

## 6. 技术栈与兼容性

| 组件 | 选型 | 注意点 |
|---|---|---|
| Blender | 5.2.1 LTS | 已安装 |
| MCP addon | mcp-for-blender（addon.py） | 支持 Blender 3.0+，无需改源码 |
| MCP server | mcp-for-blender（src/blender_mcp/server.py） | 通过 `uvx mcp-for-blender` 安装 |
| 角色/动作 | Mixamo | 同一套骨骼，免 retarget |
| 渲染 | Workbench 白膜 | 快速出参考 |

**导入 FBX / 切 Action 的实现方式**：通过 `execute_blender_code` 下发 bpy 代码完成，无需扩展 addon。示例（导入 Mixamo FBX）：
```python
# 通过 execute_blender_code 下发
import bpy
bpy.ops.import_scene.fbx(filepath="C:/assets/actions/punch.fbx")
# 导入后：动作会作为 Action 挂在骨骼上，可重命名复用
for a in bpy.data.actions:
    if a.name not in ["punch"]:
        a.name = "punch"  # 规范命名，供 scene.json 引用
```

**关键原则**：资产导入脚本与运行时编排代码都用 `execute_blender_code` 走 MCP 下发，区别只是前者是一次性、后者是 Hermes 决策循环。

---

## 7. 最小闭环验证（先跑通再扩展）

1. Blender 启用 addon → Start MCP Server（默认 `localhost:9876`）
2. Hermes 通过 `execute_blender_code` 建 3×3 方块阵列 + 环绕相机
3. `get_scene_info` / `look` 校验结果
4. `blender -b` 渲染 10 帧 → ffmpeg 合成 1s MP4

**通过后**，再进入：导入 2 个 Mixamo 人形 + 拳击/闪避动作（`execute_blender_code`）→ 编排 → 渲染 2s 打斗片段。

---

## 8. 后续待办

- [ ] 编写 Mixamo 动作批量导入脚本（通过 execute_blender_code 下发，存成 Action 资产库）
- [ ] 设计 `scene.json` 完整 schema（含相机类型、灯光）
- [ ] 编写「scene.json → bpy 编排代码」的生成逻辑（Hermes 侧）
- [ ] 与 KB 的 shots.md / conventions.md 对齐
- [ ] 验证 Blender 5.2.1 与 mcp-for-blender 的 socket 通信
