"""P1-1:落库前加工统一入口,三条路径(主题/订阅/回填)共用。"""
from paperpulse import prepare


def test_prepare_topic_paper():
    p = prepare.prepare_paper(
        dict(id="10.1/a", doi="10.1/a", title="MALDI imaging of plant",
             abstract="abs", matched_groups=[], tags=[], first_seen=None),
        group_names=["1"], g3_phrases=["metabolite annotation"],
        started="2026-11-07T09:00:00")
    assert p["matched_groups"] == ["1"]
    assert p["tags"]  # tags.assign 已执行
    assert p["first_seen"] == "2026-11-07T09:00:00"


def test_prepare_subscription_paper():
    p = prepare.prepare_paper(
        dict(id="10.1/b", doi="10.1/b", title="Nature pick", abstract=None,
             matched_groups=[], tags=[], first_seen=None, subscription=None),
        group_names=[], g3_phrases=[],
        started="2026-11-07T09:00:00", subscription="Nature")
    assert p["subscription"] == "Nature"
    assert p["matched_groups"] == []


def test_prepare_stale_flag_passthrough():
    p = dict(id="10.1/c", doi="10.1/c", title="Old", abstract=None,
             matched_groups=[], tags=[], first_seen=None)
    p["_stale"] = True
    out = prepare.prepare_paper(p, group_names=[], g3_phrases=[],
                                started="2026-11-07T09:00:00")
    assert out.get("_stale") is True


def test_prepare_keeps_existing_groups_when_none():
    p = prepare.prepare_paper(
        dict(id="10.1/d", doi="10.1/d", title="Preprint",
             abstract=None, matched_groups=["1", "2"], tags=[],
             first_seen=None),
        group_names=None, g3_phrases=[], started="2026-11-07T09:00:00")
    assert p["matched_groups"] == ["1", "2"]
