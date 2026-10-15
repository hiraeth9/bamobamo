import sys
import bpy

# 调试日志直接写文件，绕过 stdout 缓冲
_logf = open("/tmp/ss_debug.log", "w", buffering=1)

def _log(msg):
    _logf.write(msg + "\n")
    _logf.flush()

_log("step1: bpy imported, importing addon")
sys.path.insert(0, "/root/rivermind-data/bamo/blender-mcp")
import addon
_log("step2: addon imported, calling register()")
addon.register()

# 预置白膜渲染 helper 函数（挂到 bpy，供 Hermes 通过 execute_code 调用）
_log("step2.5: loading preload_helpers")
sys.path.insert(0, "/root/rivermind-data/bamo/scripts")
try:
    import preload_helpers
    _log("step2.5b: preload_helpers loaded OK")
except Exception as e:
    _log(f"step2.5b: preload_helpers load FAILED: {e}")

_log("step3: register() done, starting server")
server = addon.BlenderMCPServer(host="localhost", port=9876)
server.start()
bpy.types.blendermcp_server = server
_log(f"step4: server.running={server.running}")
print("BLENDERMCP_READY" if server.running else "BLENDERMCP_FAILED", flush=True)
