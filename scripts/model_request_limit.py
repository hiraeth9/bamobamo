"""Optional per-run model-send gate, separate from all usage/cost records."""
from contextlib import contextmanager
from datetime import date, datetime
import fcntl
from functools import wraps
import json
from pathlib import Path
from zoneinfo import ZoneInfo


class ModelRequestLimitReached(SystemExit):
    """Stop before transport; SDK retry handlers must not swallow this limit."""


class ModelRequestGate:
    def __init__(self, state_path, max_requests):
        self.state_path = Path(state_path)
        self.max_requests = max_requests
        self.denied = False

    def _deny(self):
        self.denied = True
        raise ModelRequestLimitReached(f'本轮模型请求已达到上限（{self.max_requests}次），停止后续模型发送。')

    def raise_if_denied(self):
        """Preserve the actual cause if a Hermes thread swallowed SystemExit."""
        if self.denied:
            self._deny()

    def check_available(self):
        """Read the shared count without reserving or creating a state file."""
        used = 0
        if self.state_path.is_file():
            with self.state_path.open('r', encoding='utf-8') as stream:
                fcntl.flock(stream, fcntl.LOCK_SH)
                raw = stream.read().strip()
                used = json.loads(raw) if raw else 0
        if self.max_requests is not None and used >= self.max_requests:
            self._deny()
        return None if self.max_requests is None else self.max_requests-used

    def reserve(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the same inode: this file is both the integer counter and lock.
        with self.state_path.open('a+', encoding='utf-8') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            raw = stream.read().strip()
            used = json.loads(raw) if raw else 0
            if self.max_requests is not None and used >= self.max_requests:
                self._deny()
            stream.seek(0)
            stream.truncate()
            stream.write(str(used+1)+'\n')
            stream.flush()


def load_gate(config_path, today=None):
    path = Path(config_path)
    if not path.is_file():
        return None
    rule = json.loads(path.read_text(encoding='utf-8'))
    try:
        configured_date = date.fromisoformat(rule['date'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('model-request-limit.json 的 date 请填写 YYYY-MM-DD 日期') from None
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if configured_date != today:
        return None
    maximum = rule.get('max_requests')
    if maximum is not None and (type(maximum) is not int or maximum < 1):
        raise ValueError('model-request-limit.json 的 max_requests 请填正整数，或用 null 只计数不限制')
    state = rule.get('state_path')
    if not isinstance(state, str) or not state.strip():
        raise ValueError('model-request-limit.json 需要填写本轮独立的 state_path')
    state_path = Path(state).expanduser()
    if not state_path.is_absolute():
        state_path = path.parent/state_path
    return ModelRequestGate(state_path, maximum)


def is_model_request(request):
    return request.method.upper() == 'POST' and request.url.path.rstrip('/').endswith(('/chat/completions', '/responses'))


@contextmanager
def model_request_limit(config_path, today=None):
    """Wrap actual HTTPX sends only while an active-day rule is configured."""
    gate = load_gate(config_path, today=today)
    if gate is None:
        yield None
        return
    import httpx
    original_sync, original_async = httpx.Client.send, httpx.AsyncClient.send

    @wraps(original_sync)
    def sync_send(client, request, *args, **kwargs):
        if is_model_request(request):
            gate.reserve()
        return original_sync(client, request, *args, **kwargs)

    @wraps(original_async)
    async def async_send(client, request, *args, **kwargs):
        if is_model_request(request):
            gate.reserve()
        return await original_async(client, request, *args, **kwargs)

    httpx.Client.send, httpx.AsyncClient.send = sync_send, async_send
    try:
        yield gate
    finally:
        httpx.Client.send, httpx.AsyncClient.send = original_sync, original_async
