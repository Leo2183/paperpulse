"""预设(主题包)行为:一致性哨兵、空预设语义、坏 JSON 可见、打包定位。"""
import json
import sys
from pathlib import Path

import pytest

from paperpulse import queries
from paperpulse.sources import journals


def test_preset_matches_builtins_no_drift():
    """P2-1 哨兵:presets JSON 与内置副本必须一致,漂移即红。"""
    preset = json.loads(
        sorted((queries.PRESETS_DIR).glob("*.json"))[0].read_text(encoding="utf-8"))
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
    monkeypatch.setattr(queries, "PRESETS_DIR", tmp_path)
    monkeypatch.setattr(journals, "PRESETS_DIR", tmp_path)
    assert queries.get_default_groups() == []
    assert journals.get_default_journals() == []


def test_corrupt_preset_is_loud(tmp_path, monkeypatch, capsys):
    """P2-3:坏 JSON 必须报出原因,不静默回退。"""
    (tmp_path / "bad.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(queries, "PRESETS_DIR", tmp_path)
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
