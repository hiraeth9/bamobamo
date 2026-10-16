#!/usr/bin/env bash
# Start local services only. ./run.sh also prepares a fresh checkout.
set -euo pipefail
PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="${BAMO_RUNTIME_DIR:-$PROJECT/config/runtime}"
DISPLAY_NUM="${BAMO_DISPLAY:-:99}"
WEB_PORT="${PORT:-8090}"
SESSION_PREFIX="${BAMO_SESSION_PREFIX:-bamo-$(basename -- "$PROJECT")-${UID}}"
if [[ -n "${BAMO_WEB_PYTHON:-}" ]]; then
    PYTHON="$BAMO_WEB_PYTHON"
elif [[ -x "$PROJECT/.venv/bin/python" ]]; then
    PYTHON="$PROJECT/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi
for executable in tmux Xvfb; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        echo "缺少 $executable；请先运行 ./run.sh 查看依赖提示。" >&2
        exit 1
    fi
done
mkdir -p "$RUNTIME"
chmod 700 "$RUNTIME"

port_open() {
    "$PYTHON" -c 'import socket,sys; s=socket.socket(); s.settimeout(.3); sys.exit(s.connect_ex(("127.0.0.1",int(sys.argv[1]))))' "$1"
}

echo "[1/3] 准备虚拟显示 $DISPLAY_NUM"
DISPLAY_INDEX="${DISPLAY_NUM#:}"
DISPLAY_INDEX="${DISPLAY_INDEX%%.*}"
if [[ ! -S "/tmp/.X11-unix/X$DISPLAY_INDEX" ]]; then
    if tmux has-session -t "$SESSION_PREFIX-xvfb" 2>/dev/null; then
        echo "已有显示会话，等待就绪。"
    else
        printf -v display_command '%q ' Xvfb "$DISPLAY_NUM" -screen 0 1280x720x24 -nolisten tcp -ac
        tmux new-session -d -s "$SESSION_PREFIX-xvfb" -c "$PROJECT" "$display_command"
    fi
    for ((i=0; i<30; i++)); do
        [[ -S "/tmp/.X11-unix/X$DISPLAY_INDEX" ]] && break
        sleep 1
    done
    if [[ ! -S "/tmp/.X11-unix/X$DISPLAY_INDEX" ]]; then
        echo "Xvfb未就绪。查看：tmux attach -t $SESSION_PREFIX-xvfb" >&2
        exit 1
    fi
fi

echo "[2/3] 启动 Blender MCP（9876）"
if port_open 9876; then
    echo "9876 已有服务，沿用该监听端口。"
elif tmux has-session -t "$SESSION_PREFIX-blender" 2>/dev/null; then
    echo "已有本项目 Blender 会话，等待端口。"
else
    printf -v blender_command '%q ' env "DISPLAY=$DISPLAY_NUM" "XDG_RUNTIME_DIR=$RUNTIME" GHOST_BACKEND=X11 \
        "$PROJECT/blender" --factory-startup -noaudio --python "$PROJECT/scripts/start_server.py"
    tmux new-session -d -s "$SESSION_PREFIX-blender" -c "$PROJECT" "$blender_command"
fi

echo "[3/3] 启动网页（$WEB_PORT）"
if port_open "$WEB_PORT"; then
    echo "$WEB_PORT 已有服务，沿用该监听端口。"
elif tmux has-session -t "$SESSION_PREFIX-web" 2>/dev/null; then
    echo "已有本项目网页会话，等待端口。"
else
    # Read this checkout's .env in Python. API keys never enter a command line.
    web_environment=(env "PORT=$WEB_PORT")
    if [[ -n "${BAMO_HERMES_HOME:-}" ]]; then
        web_environment+=("BAMO_HERMES_HOME=$BAMO_HERMES_HOME")
    elif [[ -n "${HERMES_HOME:-}" ]]; then
        web_environment+=("HERMES_HOME=$HERMES_HOME")
    elif [[ -f "$PROJECT/config/hermes-runtime/config.yaml" ]]; then
        web_environment+=("BAMO_HERMES_HOME=$PROJECT/config/hermes-runtime")
    fi
    printf -v web_command '%q ' "${web_environment[@]}" "$PYTHON" "$PROJECT/scripts/bootstrap.py" --serve-web
    tmux new-session -d -s "$SESSION_PREFIX-web" -c "$PROJECT" "$web_command"
fi

for ((i=0; i<60; i++)); do
    if port_open 9876 && port_open "$WEB_PORT"; then
        echo "服务端口已就绪：http://localhost:$WEB_PORT；未启动任何生成任务。"
        exit 0
    fi
    sleep 1
done
echo "服务未全部就绪；查看 tmux 会话 $SESSION_PREFIX-blender / $SESSION_PREFIX-web。现有任务与进程未被清理。" >&2
exit 1
