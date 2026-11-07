from paperpulse.sources import base


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
    assert len(sleeps) == 2  # 重试 2 次


def test_request_raises_after_retries_exhausted(monkeypatch):
    monkeypatch.setattr(base, "_throttle", lambda: None)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)

    def always_fail(*a, **kw):
        raise base.SourceError("down")

    import pytest
    with pytest.raises(base.SourceError):
        base._request(always_fail)


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
