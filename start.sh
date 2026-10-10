#!/bin/bash
# ============================================================================
# 一键启动：Blender GUI（xvfb）+ MCP addon socket
# 用法：bash start.sh
# ============================================================================
set -euo pipefail

export XDG_RUNTIME_DIR=/tmp/runtime-root
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
export GDK_BACKEND=x11
export QT_QPA_PLATFORM=xcb

log() { echo -e "\033[1;32m[start]\033[0m $*"; }

# 检查 Blender
command -v blender >/dev/null 2>&1 || { echo "错误：blender 未安装，先运行 setup.sh"; exit 1; }

# 检查 addon 是否已安装
ADDON_PATH="/root/.config/blender/4.2/scripts/addons/blender_mcp.py"
if [ ! -f "$ADDON_PATH" ]; then
    log "安装 Blender MCP addon..."
    mkdir -p "$(dirname "$ADDON_PATH")"
    cp /opt/mcp-for-blender/addon.py "$ADDON_PATH"
fi

# 检查是否已有 blender 在跑
if pgrep -f "blender.*start_blender_mcp" >/dev/null; then
    log "Blender 已在运行，跳过启动"
else
    # 写启动 python 脚本（启用 addon + 手动启动 socket server）
    cat > /root/previs-project/scripts/start_blender_mcp.py <<'EOF'
import bpy
import importlib

addon_name = "blender_mcp"
if addon_name not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module=addon_name)

mod = importlib.import_module("blender_mcp")
if not hasattr(bpy.types.Scene, "blendermcp_port"):
    mod.register()

server = mod.BlenderMCPServer(port=9876)
server.start()
print("BLENDER_MCP_STARTED")
EOF

    log "启动 Blender（xvfb GUI 模式）..."
    cd /root/previs-project/scripts
    nohup xvfb-run -a -s "-screen 0 1280x720x24" blender \
        --python /root/previs-project/scripts/start_blender_mcp.py \
        > /root/previs-project/logs/blender_mcp.log 2>&1 &
    echo "Blender 启动中，PID=$!"
fi

# 等待 socket 就绪
log "等待 socket 9876 就绪..."
for i in $(seq 1 30); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(1); exit(0 if s.connect_ex(('127.0.0.1',9876))==0 else 1)" 2>/dev/null; then
        log "Blender MCP socket 已就绪 (127.0.0.1:9876)"
        break
    fi
    sleep 1
    if [ $i -eq 30 ]; then
        echo "警告：30 秒内 socket 未就绪，请检查日志 /root/previs-project/logs/blender_mcp.log"
    fi
done

# 启动前端（Previs Console）
FRONTEND_PORT="${FRONTEND_PORT:-8899}"
if curl -s -o /dev/null "http://127.0.0.1:${FRONTEND_PORT}/" 2>/dev/null; then
    log "前端已在运行 (端口 ${FRONTEND_PORT})"
else
    log "启动前端 Previs Console（端口 ${FRONTEND_PORT}）..."
    cd /root/previs-project
    PORT="${FRONTEND_PORT}" VIDEO_DIR=/root/render_out \
        nohup python3 server.py </dev/null >/root/previs-project/logs/server.log 2>&1 &
    disown
    log "前端已启动: http://<服务器IP>:${FRONTEND_PORT}"
fi

log "启动完成！"
log "  前端: http://<服务器IP>:${FRONTEND_PORT}"
log "  Hermes 控制 Blender:"
log "    hermes chat          # 交互对话"
log "    hermes -z \"提示词\"   # 非交互执行"
