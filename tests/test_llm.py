import json

import pytest

from sm_scan import llm


def test_normalize_base_url_both_forms():
    assert llm.normalize_base_url("https://open.bigmodel.cn/api/paas/v4") == \
        "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert llm.normalize_base_url("http://localhost:11434/v1/chat/completions") == \
        "http://localhost:11434/v1/chat/completions"


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="DPAPI 仅 Windows")
def test_dpapi_roundtrip():
    enc = llm.dpapi_encrypt("sk-secret-123")
    assert enc != "sk-secret-123"
    assert llm.dpapi_decrypt(enc) == "sk-secret-123"


CFG = {"llm_base_url": "https://x/v4", "llm_api_key": "k", "llm_model": "m"}


def test_chat_raises_on_401(monkeypatch):
    class Resp:
        status_code = 401
        text = "unauthorized"

    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: Resp())
    with pytest.raises(llm.SourceError):
        llm._chat(CFG, [{"role": "user", "content": "hi"}])


def test_chat_raises_on_bad_json(monkeypatch):
    class Resp:
        status_code = 200
        text = "<html>"

        def json(self):
            raise ValueError()

    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: Resp())
    with pytest.raises(llm.SourceError):
        llm._chat(CFG, [{"role": "user", "content": "hi"}])


def test_digest_ok(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "一句话。"}}]}

    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: Resp())
    p = dict(title="T", abstract="A", llm_digest=None)
    assert llm.digest(p, CFG) == "一句话。"


def test_digest_no_abstract_raises_value_error():
    p = dict(title="T", abstract=None, llm_digest=None)
    with pytest.raises(ValueError):
        llm.digest(p, CFG)


def test_batch_digest_skips_cached_and_continues_on_error(conn, monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    calls = []

    def flaky_post(url, json=None, **kw):
        calls.append(url)
        if json and "标题: B" in json_str(json):  # 论文 B 永远 401(含重试)
            class R:
                status_code = 401
                text = "x"
            return R()
        return Resp()

    def json_str(body):
        import json as _j
        return _j.dumps(body, ensure_ascii=False)

    monkeypatch.setattr(llm.requests, "post", flaky_post)
    papers = [
        {"id": "a", "title": "A", "abstract": "abs", "llm_digest": "cached"},
        {"id": "b", "title": "B", "abstract": "abs", "llm_digest": None},
        {"id": "c", "title": "C", "abstract": "abs", "llm_digest": None},
    ]
    ok, fail = llm.batch_digest(conn, papers, CFG)
    assert ok == 1 and fail == 1
    # 缓存篇(a)未调用;b 两轮尝试 + c 一次
    assert len(calls) == 3
    row = conn.execute("SELECT llm_digest FROM papers WHERE id='c'").fetchone()
    assert row["llm_digest"] == "ok"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from sm_scan import config, store

    monkeypatch.setattr(config, "ROOT", tmp_path)
    c = store.connect()
    store.init_db(c)
    return c
