from datetime import date

from sm_scan import queries


def test_translate_group1_with_refine_and_date():
    g = next(x for x in queries.DEFAULT_GROUPS if x.name.startswith("植物"))
    q = queries.to_europepmc_query(g, date(2026, 10, 4), date(2026, 11, 6))
    assert q.startswith('(TITLE_ABS:"mass spectrometry imaging" OR ')
    assert 'AND (TITLE_ABS:"plant" OR ' in q
    assert q.endswith("AND FIRST_IDATE:[2026-10-04 TO 2026-11-06]")


def test_translate_title_scope_uses_title_field():
    g = queries.TermGroup(
        name="t", enabled=True,
        terms=[queries.Term("MALDI", scope="title")],
        refine=[],
    )
    q = queries.to_europepmc_query(g, date(2026, 11, 1), date(2026, 11, 2))
    assert '(TITLE:"MALDI")' in q


def test_translate_group_without_refine_has_single_clause():
    g = queries.TermGroup(
        name="t", enabled=True,
        terms=[queries.Term("MALDI"), queries.Term("MALDI imaging")],
        refine=[],
    )
    q = queries.to_europepmc_query(g, date(2026, 11, 1), date(2026, 11, 2))
    assert "AND (" not in q  # 无 refine 括号
    assert '(TITLE_ABS:"MALDI" OR TITLE_ABS:"MALDI imaging")' in q


def test_phrases_always_quoted_even_with_slashes():
    g = queries.TermGroup(
        name="t", enabled=True,
        terms=[queries.Term("MS/MS"), queries.Term("LC-MS")],
        refine=[queries.Term("mass spectrometry")],
    )
    q = queries.to_europepmc_query(g, date(2026, 11, 1), date(2026, 11, 2))
    assert 'TITLE_ABS:"MS/MS"' in q
    assert 'TITLE_ABS:"LC-MS"' in q


def test_default_groups_three():
    names = [g.name for g in queries.DEFAULT_GROUPS]
    assert len(names) == 3
    assert any(n.startswith("植物") for n in names)
    assert any("MALDI" in n for n in names)
    assert any("注释" in n for n in names)
    g3 = next(g for g in queries.DEFAULT_GROUPS if "注释" in g.name)
    assert g3.refine, "组3 refine 必填"


def test_plant_terms_superset_of_group1_refine():
    g1 = next(g for g in queries.DEFAULT_GROUPS if g.name.startswith("植物"))
    refine_phrases = {t.phrase for t in g1.refine}
    assert refine_phrases <= set(queries.PLANT_TERMS)


def test_from_row_roundtrip():
    row = {
        "id": 7,
        "name": "x",
        "enabled": 1,
        "terms": '[{"phrase": "MALDI", "scope": "title"}, {"phrase": "DESI-MS"}]',
        "refine": '[{"phrase": "plant"}]',
    }
    g = queries.from_row(row)
    assert g.id == 7 and g.enabled and g.name == "x"
    assert g.terms[0].phrase == "MALDI" and g.terms[0].scope == "title"
    assert g.terms[1].scope == "title_abs"  # 默认
    assert [t.phrase for t in g.refine] == ["plant"]
    assert g.refine[0].scope == "title_abs"


def test_preset_loading_and_fallback(tmp_path, monkeypatch):
    """主题包优先,内置兜底(通用性:换主题=换 JSON)。"""
    import json

    (tmp_path / "mytopic.json").write_text(json.dumps({
        "groups": [{"id": 9, "name": "自定义组", "enabled": 1,
                    "terms": [{"phrase": "cryo-EM", "scope": "title_abs"}],
                    "refine": []}]
    }), encoding="utf-8")
    monkeypatch.setattr(queries, "PRESETS_DIR", tmp_path)
    groups = queries.get_default_groups()
    assert len(groups) == 1 and groups[0].name == "自定义组"

    monkeypatch.setattr(queries, "PRESETS_DIR", tmp_path / "empty")
    assert len(queries.get_default_groups()) == 3  # 内置兜底
