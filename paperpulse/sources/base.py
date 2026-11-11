"""三轨 HTTP 基类(spec §3.4):限速 ≥1s、超时 30s、重试 4 次指数退避。

重试从 2 次加到 4 次(退避 1/2/4/8s):实测国际链路对 EBI/Crossref/bioRxiv
存在间歇性 RST / read-timeout(2026-11 连续数日 09:00 partial),浅重试在
抖动窗口内全部耗尽;连接复用(模块级 Session)减少握手面。
"""

import logging
import threading
import time
from typing import Callable

import requests

UA = "paperpulse/0.1 (local research tool)"
TIMEOUT = 30
RETRIES = 4
MIN_INTERVAL = 1.0

log = logging.getLogger(__name__)
_last_request_ts = 0.0

# 连接复用:每次裸 requests.get/post 都要完整 TLS 握手,而间歇性 RST
# 恰好高发在握手/读取阶段。Session 由锁保护(GUI 多 worker 并发);
# _throttle 本就全局串行,锁不改变实际请求节奏。
_session = requests.Session()
_session_lock = threading.Lock()

# 熔断(第13轮 P2-3):连续 N 次重试耗尽后,冷却期内直接快速失败,
# 避免 32 刊订阅轨在断网时逐刊空转 5×30s+15s(最坏 2.9 小时)。
# 任一请求成功即复位;冷却结束自动恢复(半开)。
_CIRCUIT_THRESHOLD = 3
_CIRCUIT_COOLDOWN = 120.0
_fail_streak = 0
_circuit_until = 0.0


class SourceError(Exception):
    """单源失败(重试耗尽或响应不合法)。

    permanent=True 表示重试无意义(4xx 参数/权限错),调用方应立即失败。
    """

    def __init__(self, *args, permanent: bool = False):
        super().__init__(*args)
        self.permanent = permanent


def _throttle() -> None:
    global _last_request_ts
    now = time.time()
    wait = MIN_INTERVAL - (now - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.time()


def _request(fn: Callable[[], dict]) -> dict:
    """执行一次 HTTP 调用:节流 + 超时 + 重试 4 次(指数退避 1/2/4/8s)。

    4xx(permanent)不重试;连续 _CIRCUIT_THRESHOLD 次重试耗尽后熔断
    _CIRCUIT_COOLDOWN 秒(快速失败,不退避不等待)。
    """
    global _fail_streak, _circuit_until
    if time.time() < _circuit_until:
        raise SourceError(
            f"circuit open: {_fail_streak} consecutive request failures;"
            " fast-failing for the cooldown window")
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        _throttle()
        try:
            out = fn()
            _fail_streak = 0  # 成功即复位熔断计数
            return out
        except (requests.RequestException, SourceError) as e:
            if getattr(e, "permanent", False):
                raise  # 4xx 类:重试无意义
            last = e
            if attempt < RETRIES:
                delay = 2**attempt
                log.warning("request failed (attempt %d): %s; retry in %ds",
                            attempt + 1, e, delay)
                time.sleep(delay)
    _fail_streak += 1
    if _fail_streak >= _CIRCUIT_THRESHOLD:
        _circuit_until = time.time() + _CIRCUIT_COOLDOWN
        log.error("circuit opened after %d consecutive failures;"
                  " fast-failing for %.0fs",
                  _fail_streak, _CIRCUIT_COOLDOWN)
    raise SourceError(str(last))


def http_get(url: str, params: dict | None = None, mailto: str | None = None) -> dict:
    p = dict(params or {})
    if mailto:
        p["mailto"] = mailto

    def _do() -> dict:
        with _session_lock:
            resp = _session.get(url, params=p, timeout=TIMEOUT,
                                headers={"User-Agent": UA})
        return _raise_for_status(resp)

    return _request(_do)


def http_post(url: str, data: dict) -> dict:
    def _do() -> dict:
        with _session_lock:
            resp = _session.post(url, data=data, timeout=TIMEOUT,
                                 headers={"User-Agent": UA})
        return _raise_for_status(resp)

    return _request(_do)


def _raise_for_status(resp: requests.Response) -> dict:
    if resp.status_code != 200:
        # 4xx 是永久错误(刊名打错/权限/参数),重试 16 秒只是浪费(P2-3)
        permanent = 400 <= resp.status_code < 500
        raise SourceError(
            f"HTTP {resp.status_code}: {resp.text[:200]}",
            permanent=permanent)
    try:
        return resp.json()
    except ValueError as e:  # 维护页等非 JSON 响应
        raise SourceError(f"non-JSON response: {resp.text[:120]}") from e
