#!/bin/bash
# ============================================================================
# 白膜视频生成系统 · 进程守护脚本（崩溃自恢复）
# 用法: bash watchdog.sh   （建议放 tmux 会话常驻，如 tmux new-session -d -s watchdog）
# 每 10 秒检查 Xvfb / Blender(9876) / 后端(8090) 健康，掉线自动拉起。
# ============================================================================
PROJECT=/root/rivermind-data/bamo
RUNTIME=/tmp/runtime-root
DISPLAY_NUM=:99
LOG=/tmp/watchdog.log

log() { echo "[$(date '+%F %T')] $1" | tee -a "$LOG"; }

log "===== watchdog 启动，开始守护 ====="

while true; do
    # ---- 1. Xvfb ----
    if ! pgrep -x Xvfb >/dev/null 2>&1; then
        log "⚠️ Xvfb 掉线，重启中..."
        rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null
        tmux kill-session -t xvfb 2>/dev/null
        tmux new-session -d -s xvfb "Xvfb $DISPLAY_NUM -screen 0 1280x720x24 -nolisten tcp -ac"
        sleep 5
        log "Xvfb 已拉起"
    fi

    # ---- 2. Blender socket server (9876) ----
    if ! ss -tln 2>/dev/null | grep -q ':9876'; then
        log "⚠️ Blender(9876) 掉线，重启中..."
        for pid in $(pgrep -f "blender-5.2.2-linux-x64/blender"); do kill -9 "$pid" 2>/dev/null; done
        tmux kill-session -t blender 2>/dev/null
        sleep 1
        tmux new-session -d -s blender \
            "cd $PROJECT && env DISPLAY=$DISPLAY_NUM XDG_RUNTIME_DIR=$RUNTIME GHOST_BACKEND=X11 ./blender --factory-startup -noaudio --python scripts/start_server.py"
        log "Blender 已拉起（约 15 秒加载）"
    fi

    # ---- 3. 网页后端 (8090) ----
    if ! ss -tln 2>/dev/null | grep -q ':8090'; then
        log "⚠️ 后端(8090) 掉线，重启中..."
        for pid in $(pgrep -f "/opt/conda/bin/python web/app.py"); do kill "$pid" 2>/dev/null; done
        tmux kill-session -t web 2>/dev/null
        sleep 1
        tmux new-session -d -s web "cd $PROJECT && /opt/conda/bin/python web/app.py"
        log "后端已拉起"
    fi

    sleep 10
done
