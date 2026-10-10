#!/bin/bash
# ============================================================================
# Blender Previs 一键部署脚本（Ubuntu 22.04 云 GPU 服务器）
#
# 用法：bash setup.sh
# 作用：自动安装 Blender + mcp-for-blender + Hermes + 下载 Mixamo 资产 + 配置 MCP
#
# 全程国内镜像加速（南京大学 Blender 镜像 / 清华 PyPI / GitHub 代理）
# 支持幂等：重复运行不会重复安装已存在的组件
# ============================================================================
set -euo pipefail

# ===== 可配置项 =====
BLENDER_VERSION="4.2.19"
BLENDER_MIRROR="https://mirror.nju.edu.cn/blender/release/Blender4.2"
PYPI_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
GH_PROXY="https://gh-proxy.com/"
HERMES_INSTALL_CMD="curl -fsSL https://hermes-agent.nousresearch.com/install.sh | sh"

# Mixamo 资产包下载地址（上传到你的网盘/GitHub Release 后填写）
# 资产包应包含：Reloading.fbx（角色）+ 动作 FBX 集合，解压后放在 /mnt/animations/
ASSETS_URL="${ASSETS_URL:-}"  # 例如 https://github.com/YOUR/REPO/releases/download/v1.0/mixamo_assets.tar.gz

ANIM_DIR="/mnt/animations"
PROJECT_DIR="${PROJECT_DIR:-/opt/blender-previs}"

log() { echo -e "\033[1;32m[setup]\033[0m $*"; }
err() { echo -e "\033[1;31m[error]\033[0m $*"; exit 1; }

# ===== 1. 基础依赖 =====
log "安装基础依赖..."
apt-get update -qq
apt-get install -y -qq xvfb ffmpeg unzip git curl wget ca-certificates \
    libegl1 libgl1 libgles2 libglvnd0 libxkbcommon0 libwayland-client0 \
    libx11-6 libxrender1 libxi6 libsm6 libice6 python3-pip 2>&1 | tail -3

# ===== 2. Blender =====
if [ ! -d "/opt/blender-${BLENDER_VERSION}-linux-x64" ]; then
    log "下载 Blender ${BLENDER_VERSION}（南京大学镜像）..."
    wget -q -O /tmp/blender.tar.xz "${BLENDER_MIRROR}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
        || err "Blender 下载失败"
    cd /opt && tar -xf /tmp/blender.tar.xz
    rm -f /tmp/blender.tar.xz
    ln -sf "/opt/blender-${BLENDER_VERSION}-linux-x64/blender" /usr/local/bin/blender
    log "Blender 安装完成"
else
    log "Blender 已存在，跳过"
fi

# ===== 3. mcp-for-blender =====
if [ ! -d "/opt/mcp-for-blender" ]; then
    log "clone mcp-for-blender（GitHub 代理）..."
    git clone --depth 1 "${GH_PROXY}https://github.com/ahujasid/mcp-for-blender.git" /opt/mcp-for-blender \
        || err "clone mcp-for-blender 失败"
fi

if [ ! -d "/opt/mcp-for-blender/.venv" ]; then
    log "创建 mcp-for-blender 独立 venv + 装依赖（清华 PyPI）..."
    /opt/conda/bin/python3.11 -m venv /opt/mcp-for-blender/.venv 2>/dev/null || \
        python3 -m venv /opt/mcp-for-blender/.venv
    /opt/mcp-for-blender/.venv/bin/pip install -q 'mcp>=1.26.0,<2' httpx -i "$PYPI_MIRROR" \
        || err "mcp 依赖安装失败"
fi
log "mcp-for-blender 就绪"

# ===== 4. Mixamo 资产 =====
if [ -n "$ASSETS_URL" ]; then
    if [ ! -d "$ANIM_DIR" ] || [ -z "$(ls -A $ANIM_DIR 2>/dev/null)" ]; then
        log "下载 Mixamo 资产包..."
        mkdir -p "$ANIM_DIR"
        wget -q -O /tmp/assets.tar.gz "$ASSETS_URL" || err "资产包下载失败"
        tar -xf /tmp/assets.tar.gz -C "$ANIM_DIR"
        rm -f /tmp/assets.tar.gz
        log "Mixamo 资产就绪"
    else
        log "Mixamo 资产已存在，跳过"
    fi
else
    log "警告：ASSETS_URL 未设置，跳过资产下载（请手动上传 Mixamo 资产到 $ANIM_DIR）"
fi

# ===== 5. Hermes =====
if ! command -v hermes >/dev/null 2>&1 && [ ! -d "/root/.hermes/hermes-agent" ]; then
    log "安装 Hermes Agent..."
    bash -c "$HERMES_INSTALL_CMD" || err "Hermes 安装失败"
else
    log "Hermes 已存在，跳过"
fi

# ===== 6. 部署本项目文件 =====
log "部署项目文件到 $PROJECT_DIR..."
mkdir -p "$PROJECT_DIR"
cp -r "$(dirname "$0")"/* "$PROJECT_DIR/" 2>/dev/null || true

# ===== 7. 生成 MCP wrapper + 启动脚本 =====
log "生成 MCP wrapper 和启动脚本..."
cat > /root/blender_mcp_server.sh <<'EOF'
#!/bin/bash
export PYTHONPATH=/opt/mcp-for-blender/src:$PYTHONPATH
exec /opt/mcp-for-blender/.venv/bin/python -m blender_mcp.server "$@"
EOF
chmod +x /root/blender_mcp_server.sh

log "========================================"
log "部署完成！"
log "下一步："
log "  1. 配置 Hermes LLM：bash setup_hermes.sh <你的阿里云API_KEY>"
log "  2. 启动：bash start.sh"
log "  3. 验证：bash verify.sh"
log "========================================"
