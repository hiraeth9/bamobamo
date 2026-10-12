#!/bin/bash
# ============================================================================
# 白膜视频生成系统 · 一键启动脚本
# 用法: bash start_all.sh
# 幂等：可重复执行，已在运行的服务会跳过，缺失的会拉起。
# 三个服务统一用 tmux 托管：xvfb / blender / web
# ============================================================================
set -u

PROJECT=/root/rivermind-data/bamo
RUNTIME=/tmp/runtime-root
DISPLAY_NUM=:99

echo "===== [1/4] 准备运行环境 ====="
mkdir -p "$RUNTIME" && chmod 700 "$RUNTIME"

echo "===== [2/4] 启动 Xvfb (虚拟显示) ====="
if pgrep -x Xvfb >/dev/null 2>&1; then
    echo "Xvfb 已在运行，跳过"
else
    # 清理残留锁文件（残留会导致 Xvfb 启动失败）
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null
    tmux kill-session -t xvfb 2>/dev/null
    tmux new-session -d -s xvfb "Xvfb $DISPLAY_NUM -screen 0 1280x720x24 -nolisten tcp -ac"
    sleep 4
    pgrep -x Xvfb >/dev/null 2>&1 && echo "Xvfb 启动成功" || echo "⚠️ Xvfb 启动失败"
fi

echo "===== [3/4] 启动 Blender socket server (9876) ====="
if ss -tln 2>/dev/null | grep -q ':9876'; then
    echo "Blender(9876) 已在监听，跳过"
else
    # 清掉残留 blender 进程（可能活着但 socket 挂了）
    for pid in $(pgrep -f "blender-5.2.2-linux-x64/blender"); do kill -9 "$pid" 2>/dev/null; done
    tmux kill-session -t blender 2>/dev/null
    sleep 1
    tmux new-session -d -s blender \
        "cd $PROJECT && env DISPLAY=$DISPLAY_NUM XDG_RUNTIME_DIR=$RUNTIME GHOST_BACKEND=X11 ./blender --factory-startup -noaudio --python scripts/start_server.py"
    echo "Blender 启动中（约 10-15 秒加载 addon + helper）..."
fi

echo "===== [4/4] 启动网页后端 (8090) ====="
if ss -tln 2>/dev/null | grep -q ':8090'; then
    echo "后端(8090) 已在监听，跳过"
else
    for pid in $(pgrep -f "/opt/conda/bin/python web/app.py"); do kill "$pid" 2>/dev/null; done
    tmux kill-session -t web 2>/dev/null
    sleep 1
    tmux new-session -d -s web "cd $PROJECT && /opt/conda/bin/python web/app.py"
    echo "后端启动中..."
fi

echo ""
echo "===== 启动完成，等待服务就绪 ====="
# 等待 Blender（最慢，最多 40 秒）
for i in $(seq 1 8); do
    sleep 5
    B=$(ss -tln 2>/dev/null | grep -q ':9876' && echo OK || echo waiting)
    if [ "$B" = "OK" ]; then break; fi
done

echo ""
echo "===== 最终状态 ====="
echo "--- 端口 ---"
ss -tln 2>/dev/null | grep -E '9876|8090' || echo "⚠️ 有端口未就绪！"
echo "--- tmux 会话 ---"
tmux ls 2>/dev/null || echo "无 tmux 会话"
echo "--- 关键进程 ---"
ps -eo pid,etime,cmd | grep -E '[b]lender-5.2.2|[X]vfb|[w]eb/app.py' | head -5
