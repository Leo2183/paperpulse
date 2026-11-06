"""LLM 中文导读(spec §8):通用 OpenAI 兼容接口 + DPAPI 加密存储。

默认关;结果持久缓存(papers.llm_digest),已有导读不再调用。
"""

import base64
import json
import logging

import requests

from .sources.base import SourceError

log = logging.getLogger(__name__)

TIMEOUT = 60
SYSTEM_PROMPT = (
    "你是科研文献导读助手。用一句中文(60字内)概括这篇论文做了什么、"
    "关键发现是什么。只输出这一句话。"
)


def normalize_base_url(u: str) -> str:
    u = u.rstrip("/")
    if not u.endswith("/chat/completions"):
        u += "/chat/completions"
    return u


# ---------------------------------------------------------------- DPAPI

def dpapi_encrypt(plain: str) -> str:
    """Windows DPAPI 加密(绑定当前用户),base64 存库。"""
    import ctypes
    import ctypes.wintypes as wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    data = plain.encode("utf-8")
    buf = ctypes.create_string_buffer(data, len(data))
    inb = DATA_BLOB(len(data),
                    ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    outb = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(outb))
    if not ok:
        raise OSError("CryptProtectData failed")
    raw = ctypes.string_at(outb.pbData, outb.cbData)
    ctypes.windll.kernel32.LocalFree(outb.pbData)
    return base64.b64encode(raw).decode("ascii")


def dpapi_decrypt(b64: str) -> str:
    import ctypes
    import ctypes.wintypes as wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    data = base64.b64decode(b64)
    buf = ctypes.create_string_buffer(data, len(data))
    inb = DATA_BLOB(len(data),
                    ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    outb = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(outb))
    if not ok:
        raise OSError("CryptUnprotectData failed")
    raw = ctypes.string_at(outb.pbData, outb.cbData)
    ctypes.windll.kernel32.LocalFree(outb.pbData)
    return raw.decode("utf-8")


# ---------------------------------------------------------------- 客户端

def _chat(cfg: dict, messages: list[dict], retries: int = 1) -> str:
    url = normalize_base_url(cfg["llm_base_url"])
    body = {
        "model": cfg["llm_model"],
        "messages": messages,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {cfg['llm_api_key']}"}
    last = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
            if resp.status_code != 200:
                raise SourceError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()["choices"][0]["message"]["content"].strip()
        except (requests.RequestException, SourceError, ValueError, KeyError) as e:
            last = e
            if attempt < retries:
                log.warning("LLM call failed (%s); retrying", e)
    raise SourceError(f"LLM call failed: {last}")


def digest(paper: dict, cfg: dict) -> str:
    """单篇导读:标题+摘要 → 一句中文。无摘要 → ValueError(调用方跳过)。"""
    if not paper.get("abstract"):
        raise ValueError("no abstract")
    content = (
        f"标题: {paper['title']}\n摘要: {paper['abstract'][:3000]}"
    )
    return _chat(cfg, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ])


def batch_digest(conn, papers: list[dict], cfg: dict,
                 progress=None) -> tuple[int, int]:
    """批量导读:已有缓存跳过;单篇失败跳过计数不中断。返回 (成功, 失败)。

    progress(processed) 每处理完一篇回调(GUI 进度条)。
    """
    ok = fail = 0
    for i, p in enumerate(papers):
        if p.get("llm_digest"):
            continue
        try:
            text = digest(p, cfg)
        except ValueError:
            fail += 1  # 无摘要(Crossref 路由)
        except SourceError as e:
            log.error("digest failed for %s: %s", p.get("id"), e)
            fail += 1
        else:
            conn.execute(
                "UPDATE papers SET llm_digest=? WHERE id=?", (text, p["id"]))
            conn.commit()
            ok += 1
        if progress:
            progress(i + 1)
    return ok, fail
