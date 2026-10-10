#!/bin/bash
# ============================================================================
# 部署后自检：验证整套环境是否可用
# 用法：bash verify.sh
# ============================================================================
set -uo pipefail

PASS=0
FAIL=0
check() {
    local name="$1" ok="$2"
    if [ "$ok" -eq 0 ]; then
        echo -e "  \033[1;32m✓\033[0m $name"
        PASS=$((PASS+1))
    else
        echo -e "  \033[1;31m✗\033[0m $name"
        FAIL=$((FAIL+1))
    fi
}

echo "=============================================="
echo "Blender Previs 环境自检"
echo "=============================================="

# 1. Blender
command -v blender >/dev/null 2>&1; check "Blender 已安装" $?
blender --version >/dev/null 2>&1; check "Blender 可执行" $?

# 2. GPU
nvidia-smi >/dev/null 2>&1; check "GPU (nvidia-smi) 可用" $?

# 3. mcp-for-blender
[ -d /opt/mcp-for-blender ]; check "mcp-for-blender 仓库存在" $?
[ -f /opt/mcp-for-blender/.venv/bin/python ]; check "mcp venv 存在" $?
/opt/mcp-for-blender/.venv/bin/python -c "from mcp.server.fastmcp import FastMCP" 2>/dev/null; check "mcp SDK 可导入" $?

# 4. Blender MCP addon
[ -f /root/.config/blender/4.2/scripts/addons/blender_mcp.py ]; check "Blender MCP addon 已安装" $?

# 5. socket 9876
python3 -c "import socket; s=socket.socket(); s.settimeout(2); exit(0 if s.connect_ex(('127.0.0.1',9876))==0 else 1)" 2>/dev/null
check "Blender MCP socket (9876) 监听中" $?

# 6. Mixamo 资产
[ -f /mnt/animations/Reloading.fbx ]; check "角色资产 Reloading.fbx" $?
CNT=$(ls /mnt/animations/*.fbx 2>/dev/null | wc -l)
check "动作库（$CNT 个 FBX）" $([ "$CNT" -gt 0 ] && echo 0 || echo 1)

# 7. Hermes
HERMES=$(find /root/.hermes -path '*/venv/bin/hermes' 2>/dev/null | head -1)
[ -n "$HERMES" ]; check "Hermes 已安装" $?

if [ -n "$HERMES" ]; then
    $HERMES mcp test blender >/dev/null 2>&1; check "Hermes → Blender MCP 连接" $?
fi

# 8. 前端
[ -f /root/previs-project/frontend/index.html ]; check "前端 index.html 存在" $?
[ -f /root/previs-project/server.py ]; check "后端 server.py 存在" $?
curl -s -o /dev/null "http://127.0.0.1:${FRONTEND_PORT:-8899}/" 2>/dev/null
check "前端服务可访问 (端口 ${FRONTEND_PORT:-8899})" $?

echo "=============================================="
echo "结果：$PASS 通过，$FAIL 失败"
if [ "$FAIL" -eq 0 ]; then
    echo -e "\033[1;32m✓ 环境就绪，可以用 Hermes 生成白膜视频了\033[0m"
else
    echo -e "\033[1;31m✗ 有 $FAIL 项未通过，请根据上述 ✗ 项排查\033[0m"
fi
echo "=============================================="
