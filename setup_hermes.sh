#!/bin/bash
# ============================================================================
# 配置 Hermes LLM（阿里云百炼 qwen3.8-flash）+ Blender MCP
# 用法：bash setup_hermes.sh <API_KEY> [BASE_URL] [MODEL]
# ============================================================================
set -euo pipefail

API_KEY="${1:-}"
BASE_URL="${2:-https://ws-jdh7rj98hbmc0nrh.cn-beijing.maas.aliyuncs.com/compatible-mode/v1}"
MODEL="${3:-qwen3.8-flash}"

if [ -z "$API_KEY" ]; then
    echo "用法：bash setup_hermes.sh <你的阿里云API_KEY> [BASE_URL] [MODEL]"
    echo "示例：bash setup_hermes.sh sk-xxx"
    exit 1
fi

# 找 hermes 可执行文件（Hermes 自带 Python 3.14 venv）
HERMES=$(find /root/.hermes -path '*/venv/bin/hermes' 2>/dev/null | head -1)
if [ -z "$HERMES" ]; then
    HERMES=$(find /root/.hermes -maxdepth 3 -name hermes -type f 2>/dev/null | head -1)
fi

if [ -z "$HERMES" ]; then
    echo "错误：找不到 hermes，请先运行 setup.sh 安装 Hermes"
    exit 1
fi

echo "使用 Hermes: $HERMES"

# 配置 LLM
$HERMES config set model.provider custom
$HERMES config set model.base_url "$BASE_URL"
$HERMES config set model.default "$MODEL"
$HERMES config set model.api_key "$API_KEY"

# 配置 MCP（追加到 config.yaml）
MCP_BLOCK='
mcp_servers:
  blender:
    command: /root/blender_mcp_server.sh
    args: []
    env:
      BLENDER_HOST: "127.0.0.1"
      BLENDER_PORT: "9876"
    timeout: 300
    enabled: true
'

CONFIG="/root/.hermes/config.yaml"
# 如果已存在 mcp_servers.blender 配置则跳过
if grep -q 'command: /root/blender_mcp_server.sh' "$CONFIG" 2>/dev/null; then
    echo "MCP 配置已存在，跳过"
else
    # 去掉旧的 mcp_servers（如果有），追加新的
    python3 - "$CONFIG" <<'PYEOF'
import sys, re
path = sys.argv[1]
s = open(path).read()
# 删除已有的 mcp_servers 块（简单处理：删除含 blender command 的段落）
s = re.sub(r'\nmcp_servers:.*?(?=\n[a-zA-Z#]|\Z)', '', s, flags=re.DOTALL)
open(path, 'w').write(s)
PYEOF
    echo "$MCP_BLOCK" >> "$CONFIG"
    echo "MCP 配置已写入"
fi

# 验证
echo "验证 MCP 连接..."
$HERMES mcp test blender

echo "========================================"
echo "Hermes 配置完成！"
echo "  LLM: $MODEL @ $BASE_URL"
echo "  MCP: blender (socket 9876)"
echo "========================================"
