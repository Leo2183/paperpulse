import pytest

from paperpulse.sources import base


def _reset_circuit(monkeypatch):
    """熔断是模块级状态,测试前后必须复位防污染(第13轮 P2-3)。"""
    monkeypatch.setattr(base, "_fail_streak", 0)
    monkeypatch.setattr(base, "_circuit_until", 0.0)


def test_request_retries_on_exception(monkeypatch):
    calls = {"n": 0}
    sleeps = []

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise base.SourceError("boom")
        return {"ok": True}

    monkeypatch.setattr(base, "_throttle", lambda: None)
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    out = base._request(flaky)
    assert out == {"ok": True}
    assert calls["n"] == 3
    assert len(sleeps) == 2  # 成功前重试 2 次


def test_retry_policy_five_attempts_pow2_backoff(monkeypatch):
    """第13轮 P3-4:钉死 5 次尝试与 1/2/4/8s 退避序列。"""
    _reset_circuit(monkeypatch)
    calls, sleeps = [], []
    monkeypatch.setattr(base, "_throttle", lambda: None)
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))

    def always_fail(*a, **kw):
        calls.append(1)
        raise base.SourceError("down")

    with pytest.raises(base.SourceError):
        base._request(always_fail)
    assert len(calls) == base.RETRIES + 1 == 5
    assert sleeps == [1, 2, 4, 8]


def test_permanent_4xx_not_retried(monkeypatch):
    """4xx 是永久错误:包装为 permanent 且 _request 不重试不退避。"""
    _reset_circuit(monkeypatch)

    class Resp:
        status_code = 404
        text = "not found"

    with pytest.raises(base.SourceError) as ei:
        base._raise_for_status(Resp())
    assert ei.value.permanent is True

    calls = []
    monkeypatch.setattr(base, "_throttle", lambda: None)

    def boom(*a, **kw):
        calls.append(1)
        raise base.SourceError("HTTP 404", permanent=True)

    with pytest.raises(base.SourceError):
        base._request(boom)
    assert calls == [1], "permanent 错误不得重试"


def test_circuit_opens_after_three_exhausted_failures(monkeypatch):
    """连续 3 次重试耗尽 → 熔断:冷却期内快速失败,不再发请求。"""
    _reset_circuit(monkeypatch)
    monkeypatch.setattr(base, "_throttle", lambda: None)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)

    def dead(*a, **kw):
        raise base.SourceError("net down")

    for _ in range(3):
        with pytest.raises(base.SourceError):
            base._request(dead)

    calls = []

    def probe(*a, **kw):
        calls.append(1)
        return {}

    with pytest.raises(base.SourceError, match="circuit open"):
        base._request(probe)
    assert not calls, "熔断期内不得再发请求"


def test_success_resets_fail_streak(monkeypatch):
    _reset_circuit(monkeypatch)
    monkeypatch.setattr(base, "_throttle", lambda: None)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)

    def dead(*a, **kw):
        raise base.SourceError("net down")

    for _ in range(2):
        with pytest.raises(base.SourceError):
            base._request(dead)
    assert base._request(lambda: {"ok": 1}) == {"ok": 1}  # 复位
    with pytest.raises(base.SourceError) as ei:
        base._request(dead)  # streak 回到 1,不开熔断
    assert "circuit" not in str(ei.value)


def test_throttle_enforces_interval(monkeypatch):
    state = {"t": 100.0, "slept": []}

    class FakeTime:
        @staticmethod
        def time():
            return state["t"]

    monkeypatch.setattr(base.time, "time", FakeTime.time)
    monkeypatch.setattr(
        base.time, "sleep", lambda s: state["slept"].append(s) or state.__setitem__("t", state["t"] + s)
    )
    base._throttle()
    base._throttle()  # 紧接着第二次:应 sleep 补足间隔
    assert state["slept"], "第二次调用必须补足 ≥1s 间隔"
    assert all(s > 0 for s in state["slept"])
