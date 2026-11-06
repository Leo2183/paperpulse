"""三轨 HTTP 基类(spec §3.4):限速 ≥1s、超时 30s、重试 2 次指数退避。"""

import logging
import time
from typing import Any, Callable

import requests

UA = "sm-scan/0.1 (local research tool)"
TIMEOUT = 30
RETRIES = 2
MIN_INTERVAL = 1.0

log = logging.getLogger(__name__)
_last_request_ts = 0.0


class SourceError(Exception):
    """单源失败(重试耗尽或响应不合法)。"""


def _throttle() -> None:
    global _last_request_ts
    now = time.time()
    wait = MIN_INTERVAL - (now - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.time()


def _request(fn: Callable[[], dict]) -> dict:
    """执行一次 HTTP 调用:节流 + 超时 + 重试 2 次(指数退避)。"""
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        _throttle()
        try:
            return fn()
        except (requests.RequestException, SourceError) as e:
            last = e
            if attempt < RETRIES:
                delay = 2**attempt
                log.warning("request failed (attempt %d): %s; retry in %ds",
                            attempt + 1, e, delay)
                time.sleep(delay)
    raise SourceError(str(last))


def http_get(url: str, params: dict | None = None, mailto: str | None = None) -> dict:
    p = dict(params or {})
    if mailto:
        p["mailto"] = mailto
    return _request(
        lambda: _raise_for_status(
            requests.get(url, params=p, timeout=TIMEOUT, headers={"User-Agent": UA})
        )
    )


def http_post(url: str, data: dict) -> dict:
    return _request(
        lambda: _raise_for_status(
            requests.post(url, data=data, timeout=TIMEOUT,
                          headers={"User-Agent": UA})
        )
    )


def _raise_for_status(resp: requests.Response) -> dict:
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as e:  # 维护页等非 JSON 响应
        raise SourceError(f"non-JSON response: {resp.text[:120]}") from e
