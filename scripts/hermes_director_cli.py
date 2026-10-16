"""Project-owned Hermes CLI with an optional send cap and bounded vision call.

Delegate startup and argument parsing to Hermes itself. An opt-in gate counts
actual model sends; the auxiliary vision wrapper leaves its native handler intact.
"""
import asyncio
from functools import wraps
import importlib
import json
from pathlib import Path
import sys
from threading import Lock

from model_request_limit import model_request_limit


AUXILIARY_VISION_TIMEOUT_SECONDS = 45.0


def _failure(code, reason):
    return json.dumps({
        'success': False,
        'error': reason,
        'error_code': code,
        'analysis': '辅助视觉检查未完成，没有图像通过的结论。请继续MCP场景数据检查和实际渲染帧，记录视觉尚未确认，不要反复调用失败的辅助接口。',
        'auxiliary_vision_disabled': True,
    }, ensure_ascii=False)


def _reported_failure(result):
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            return False
    return isinstance(result, dict) and result.get('success') is False


def guard_auxiliary_vision(original, timeout_seconds=AUXILIARY_VISION_TIMEOUT_SECONDS):
    """Keep successful results unchanged; open a process-local failure circuit.

    A thread lock protects the circuit because Hermes can dispatch tool calls
    on separate event loops. Cancellation by the caller still propagates.
    """
    lock = Lock()
    failure_reason = None

    def disable(reason):
        nonlocal failure_reason
        with lock:
            if failure_reason is None:
                failure_reason = reason

    @wraps(original)
    async def bounded(*args, **kwargs):
        with lock:
            reason = failure_reason
        if reason is not None:
            return _failure('auxiliary_vision_circuit_open', reason)
        try:
            result = await asyncio.wait_for(original(*args, **kwargs), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            reason = f'辅助视觉超过{timeout_seconds:g}秒并已请求取消；本进程后续辅助视觉调用停用。'
            disable(reason)
            return _failure('auxiliary_vision_timeout', reason)
        except Exception as error:
            # Do not echo SDK exception text: it can contain endpoint headers
            # or credentials. The exception class remains useful diagnostics.
            reason = f'辅助视觉发生{type(error).__name__}，未完成检查；本进程后续辅助视觉调用停用。'
            disable(reason)
            return _failure('auxiliary_vision_exception', reason)
        if _reported_failure(result):
            disable('辅助视觉返回success:false，未完成检查；本进程后续辅助视觉调用停用。')
        return result

    bounded._bamo_auxiliary_vision_guard = True
    return bounded


def install_auxiliary_vision_guard(vision_module=None):
    """Patch only the auxiliary callable, never the native registered handler."""
    if vision_module is None:
        vision_module = importlib.import_module('tools.vision_tools')
    original = vision_module.vision_analyze_tool
    if not getattr(original, '_bamo_auxiliary_vision_guard', False):
        vision_module.vision_analyze_tool = guard_auxiliary_vision(original)
    return vision_module.vision_analyze_tool


def main(argv=None):
    """Preserve Hermes -z and all model/provider/toolset/usage-file arguments."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    original_argv = sys.argv
    hermes_root = str(Path(__file__).resolve().parents[1] / 'hermes')
    added_path = hermes_root not in sys.path
    if added_path:
        sys.path.insert(0, hermes_root)
    try:
        # main's import performs profile/.env initialization. Import it before
        # the vision module freezes its optional runtime settings.
        sys.argv = ['hermes', *arguments]
        with model_request_limit(Path(__file__).resolve().parents[1]/'config/model-request-limit.json'):
            hermes_main = importlib.import_module('hermes_cli.main')
            install_auxiliary_vision_guard()
            # Original main owns startup, plugins, default toolsets and the public
            # oneshot runner, including process cleanup and its exit status.
            return hermes_main.main()
    finally:
        sys.argv = original_argv
        if added_path:
            sys.path.remove(hermes_root)


if __name__ == '__main__':
    sys.exit(main())
