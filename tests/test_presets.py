"""预设(主题包)行为:一致性哨兵、空预设语义、坏 JSON 可见、打包定位。"""
import json
import sys

import pytest

from paperpulse import queries
from paperpulse.sources import journals


def test_preset_matches_builtins_no_drift():
    """P2-1 哨兵:presets JSON 与内置副本必须一致,漂移即红。"""
    preset = json.loads(
        sorted((__import__("paperpulse.config", fromlist=["presets_dir"]).presets_dir()).glob("*.json"))[0].read_text(encoding="utf-8"))
    g = [
        {"id": bg.id, "name": bg.name, "enabled": int(bg.enabled),
         "terms": [{"phrase": t.phrase, "scope": t.scope} for t in bg.terms],
         "refine": [{"phrase": t.phrase, "scope": t.scope} for t in bg.refine]}
        for bg in queries._BUILTIN_GROUPS
    ]
    assert preset["groups"] == g
    j = [{"name": r["name"], "issn": r["issn"], "mode": r["mode"]}
         for r in journals._BUILTIN_JOURNAL_ROWS]
    assert sorted(preset["journals"], key=lambda x: x["issn"]) == \
        sorted(j, key=lambda x: x["issn"])


def test_empty_preset_is_respected_not_fallback(tmp_path, monkeypatch):
    """P2-2:有意空订阅/空组 = 空清单,不静默回退内置。"""
    (tmp_path / "t.json").write_text(json.dumps({"groups": [], "journals": []}),
                                     encoding="utf-8")
    monkeypatch.setattr("paperpulse.config.presets_dir", lambda: tmp_path)
    monkeypatch.setattr("paperpulse.config.presets_dir", lambda: tmp_path)
    assert queries.get_default_groups() == []
    assert journals.get_default_journals() == []


def test_corrupt_preset_is_loud(tmp_path, monkeypatch, capsys):
    """P2-3:坏 JSON 必须报出原因,不静默回退。"""
    (tmp_path / "bad.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr("paperpulse.config.presets_dir", lambda: tmp_path)
    groups = queries.get_default_groups()
    assert len(groups) == 3  # 回退内置,但必须告知
    err = capsys.readouterr().err
    assert "presets" in err.lower() or "json" in err.lower()


def test_frozen_preset_dir_prefers_exe_side(tmp_path, monkeypatch):
    """P2-4:打包版优先读 exe 旁 presets(用户可编辑),其次 _MEIPASS。"""
    from paperpulse import config

    exe_side = tmp_path / "dist" / "paperpulse"
    exe_side.mkdir(parents=True)
    (exe_side / "presets").mkdir()
    bundled = tmp_path / "bundled"
    (bundled / "presets").mkdir(parents=True)
    fake_exe = exe_side / "paperpulse.exe"
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundled), raising=False)
    assert config.presets_dir() == exe_side / "presets"

    (exe_side / "presets").rmdir()  # exe 旁没有 → 落到打包内
    assert config.presets_dir() == bundled / "presets"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from paperpulse import config, store

    monkeypatch.setattr(config, "ROOT", tmp_path)
    c = store.connect()
    store.init_db(c)
    return c


@pytest.mark.parametrize("bad", ["[1, 2, 3]", '"a string"', "42"])
def test_preset_wrong_top_level_falls_back_loud(tmp_path, monkeypatch, capsys, bad):
    """P1-1:顶层非对象(手改常见误写)必须响亮回退,不得在导入期炸。"""
    (tmp_path / "t.json").write_text(bad, encoding="utf-8")
    monkeypatch.setattr("paperpulse.config.presets_dir", lambda: tmp_path)
    from paperpulse import queries as q
    from paperpulse.sources import journals as j

    # 模拟导入期路径:模块级 DEFAULT_JOURNAL_ROWS 依赖的加载不得抛
    assert len(q.get_default_groups()) == 3  # 回退内置
    assert len(j.get_default_journals()) == 32
    assert q._load_preset_groups() is None
    err = capsys.readouterr().err
    assert "presets" in err.lower() or "json" in err.lower()


def test_loader_prefers_exe_side_presets(tmp_path, monkeypatch):
    """P2-1 集成:exe 旁与包内各一份,加载器必须取 exe 旁(不是只有函数取)。"""
    exe_side = tmp_path / "exe" / "presets"
    bundled = tmp_path / "bundled" / "presets"
    exe_side.mkdir(parents=True)
    bundled.mkdir(parents=True)
    (exe_side / "user.json").write_text(json.dumps(
        {"groups": [{"id": 1, "name": "EXE_SIDE", "enabled": 1,
                     "terms": [], "refine": []}]}), encoding="utf-8")
    (bundled / "bundled.json").write_text(json.dumps(
        {"groups": [{"id": 1, "name": "BUNDLED", "enabled": 1,
                     "terms": [], "refine": []}]}), encoding="utf-8")
    monkeypatch.setattr("paperpulse.config.presets_dir", lambda: exe_side)
    from paperpulse import queries as q

    names = [g.name for g in q.get_default_groups()]
    assert names == ["EXE_SIDE"], names


def test_group_roles_survive_disabled_groups(conn):
    """P2-2:禁用组不改变 tier/注释组角色(全组位置语义)。"""
    from paperpulse import queries as q
    from paperpulse import store as st

    st.seed_groups(conn, [
        dict(id=g.id, name=g.name, enabled=1,
             terms=[{"phrase": t.phrase, "scope": t.scope} for t in g.terms],
             refine=[{"phrase": t.phrase, "scope": t.scope} for t in g.refine])
        for g in queries.get_default_groups()])
    roles0 = q.group_roles(conn)
    assert roles0["tier1"]["name"] == "植物+空间成像"
    assert roles0["annotation"]["name"] == "产物注释"
    conn.execute("UPDATE term_groups SET enabled=0 WHERE id=2")
    conn.commit()
    roles1 = q.group_roles(conn)
    # 禁用组2 后:t2 仍是组2(方法学),注释组仍是组3(全组位置语义,P2-2)
    assert roles1["tier2"]["id"] == 2
    assert roles1["annotation"]["id"] == 3
