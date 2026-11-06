from sm_scan import tags
from sm_scan.queries import DEFAULT_GROUPS

G3 = next(g for g in DEFAULT_GROUPS if g.name == "产物注释")
G3_PHRASES = [t.phrase for t in G3.terms]


def test_plant_and_imaging_tags():
    out = tags.assign(
        "Spatial metabolomics of Arabidopsis root by mass spectrometry imaging",
        "We imaged plant tissues.", G3_PHRASES)
    assert "植物" in out
    assert "成像/空间" in out


def test_msi_abbreviation_not_recognized():
    # MSI=微卫星不稳定 + 裸 imaging:不得打「成像/空间」(H1)
    out = tags.assign(
        "Microsatellite instability in colorectal cancer",
        "MSI status was assessed by imaging.", G3_PHRASES)
    assert "成像/空间" not in out
    assert "植物" not in out


def test_generic_tissue_not_plant():
    out = tags.assign(
        "Soft tissue resection margins",
        "Surgical margins of soft tissue sarcoma.", G3_PHRASES)
    assert "植物" not in out


def test_annotation_tag_from_group3_snapshot():
    out = tags.assign(
        "Novel workflow for metabolite annotation",
        "LC-MS/MS based annotation.", G3_PHRASES)
    assert "注释" in out


def test_empty_abstract_title_only():
    out = tags.assign(
        "MALDI imaging of Paeonia root", None, G3_PHRASES)
    assert "成像/空间" in out and "植物" in out
