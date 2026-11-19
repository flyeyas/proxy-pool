"""Stable JSON event vocabulary; external text and credentials are not logged."""
import json
import re
from datetime import datetime, timezone

from handler.logHandler import LogHandler


def redact(value):
    forbidden = {'password', 'username', 'token', 'proxy_url', 'body', 'headers', 'credentials'}
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if str(k).lower() not in forbidden}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@', r'\1***@', value)
        return re.sub(r'(?i)([?&](?:token|password|api_key)=)[^&\s]+', r'\1***', value)[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def emit(event, **fields):
    # Only structured fields chosen by our code are accepted; never an exception object.
    safe = redact(fields)
    payload = {'event': event, 'time': datetime.now(timezone.utc).isoformat(), **safe}
    LogHandler('validation').info(json.dumps(payload, ensure_ascii=False, default=str))
