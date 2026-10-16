# Linux 一键启动与依赖

自动准备入口支持 Linux x86_64，CPU 可运行；已有 NVIDIA 驱动和兼容 GPU 时可以使用 GPU。初次安装需要网络和足够磁盘空间，下载 Blender、Python、Hermes、FFmpeg 及 Python 包。脚本把运行时和配置放在当前项目内，不执行 sudo，不安装显卡驱动。

系统需要仍受维护、已安装最新补丁的 Python 3.10 或更高版本，以及 Git、tmux、Xvfb 和 Blender 的共享库。Ubuntu/Debian 可先安装：

```bash
sudo apt-get update
sudo apt-get install python3 git tmux xvfb libsm6 libice6 libx11-6 libxrender1 libxi6 libxfixes3 libxkbcommon0 libgl1 libegl1 libgl1-mesa-dri
```

在克隆后的目录执行：

```bash
cp .env.example .env
# 编辑 .env，填写自己的 BAMO_API_KEY、BAMO_API_BASE、BAMO_MODEL。
./run.sh
```

这三个设置对应用户自备的 OpenAI 兼容 Chat Completions 模型服务。启动本身不调用模型、不生成视频；在网页提交任务之后才会使用该账号。生成速度取决于 CPU/GPU、片长和渲染设置。

首次执行会准备网页虚拟环境 `.venv`，恢复项目指定的 Hermes，安装 Blender 内置 Python 的依赖，准备 FFmpeg，安装项目 Skill，并调用 `scripts/bootstrap_assets.py --profile core` 恢复随仓库提供的素材。核心包含分别遵守自身许可的 Quaternius CC0 人物/动作及 CMU 真人动捕，详见 `knowledge/assets/distribution.json`。缺少必须素材时会报告具体路径，保留已完成安装并停止启动服务；处理后可再次执行。Mixamo、Rokoko 等扩展素材的账号和许可要求以素材清单为准。

网页地址通常为 `http://localhost:8090`。需要改端口时在 `.env` 设置 `PORT`；Blender MCP 使用本机 9876，虚拟显示默认 `:99`，可用 `BAMO_DISPLAY` 修改。已运行的监听端口会被沿用，启动脚本不会终止其他进程。

其他入口：

```bash
./run.sh --setup-only  # 只准备依赖、配置与素材，不启动服务。
./run.sh --check       # 只读检查本地状态，不下载、不写配置、不调用模型。
bash start_all.sh      # 已准备好的目录直接启动服务，不负责安装。
```

`--check` 检查本地依赖、配置文件和 core 素材，不验证模型账号认证、额度，也不表示动画效果已验收。执行结果非零时，应按输出补齐缺项。

模型配置默认生成在忽略提交的 `config/hermes-runtime/`，只使用当前用户显式提供的设置。启动器不会复制其他账号的 `~/.hermes` 密钥。重新填写 `.env` 后运行入口会更新该项目管理的模型配置。已有完整 Hermes 配置可用 `BAMO_HERMES_HOME` 指向，该方式保留用户的配置与密钥文件，仅同步项目 Skill。

需要限制一次验证的剩余请求数时，可在本机创建不提交的 `config/model-request-limit.json`：

```json
{"date":"2026-10-16","max_requests":10,"state_path":"/tmp/bamo-model-requests-20261016.json"}
```

规则只在对应上海日期生效，计数文件由各个导演进程共享。项目启动器在发出 OpenAI 兼容的 `chat/completions` 或 `responses` 请求前占用一次，主调用、标题、辅助调用、失败及 SDK 重试都计入；到上限会直接停止模型发送。这里的数字是本轮剩余发送上限，须先扣除已有调用，不是视频任务数。计数仅存整数，与历史费用记录及预算账本独立；同一轮不要重置计数文件。没有当日规则时保持原行为，此设置不限制其他程序直接调用模型。

Hermes 的获取版本记录在 `config/hermes-source.json`。目录中已带完整 Hermes 源码时，安装沿用其源码和锁文件；没有源码时才从指定提交获取，使用仓库保存的依赖锁。后续如主动更换 Hermes 版本，需要同时确认代码与依赖兼容。

可在 `.env` 用 `BAMO_BLENDER_ARCHIVE` 指定已经下载的 Linux x64 官方 Blender 压缩包。其他运行时下载失败会显示入口与可据此处理的错误；联网或系统依赖修复后重新运行即可。自动下载来源：[Blender 官方发行目录](https://download.blender.org/release/Blender5.2/)、[uv 官方发行](https://github.com/astral-sh/uv/releases/tag/0.12.19)、[FFmpeg 静态包](https://github.com/BtbN/FFmpeg-Builds/releases)。

当前验证覆盖临时新目录、带空格/中文路径、小型下载与解压、现有 Hermes 保留、模型配置的离线解析、素材缺项阻止启动等场景。尚未在空白 Linux 系统完整下载安装并启动全部服务，也没有通过模型认证或付费生成验证。
