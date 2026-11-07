"""标签判定(spec §7):入库时执行,标题+摘要不区分大小写。

宁可漏标,不可错标:词表只用无判别力误报排除后的复合词/专名。
"""

import re

IMAGING_TERMS = [
    "mass spectrometry imaging", "MALDI-MSI", "MALDI imaging",
    "MALDI mass spectrometry imaging", "imaging mass spectrometry",
    "DESI-MSI", "DESI-MS", "spatial metabolomics",
    "spatially resolved metabolomics", "TOF-SIMS",
    "secondary ion mass spectrometry", "MSI imaging",
]

# 不认裸 imaging / spatial / MSI / mapping(医学影像、spatial
# transcriptomics、微卫星不稳定、QTL mapping 等单词级误报)

# 植物词表与 queries.PLANT_TERMS 同一份(共用常量,防漂移)
from .queries import PLANT_TERMS  # noqa: E402


def _hit(text: str, phrases: list[str]) -> bool:
    """单词短语按词边界匹配防子串误报;多词短语仍子串(P2-10)。"""
    t = text.lower()
    edge = chr(92) + "b"
    for ph in phrases:
        q = ph.lower()
        if " " in q or "-" in q:
            if q in t:
                return True
        elif re.search(edge + re.escape(q) + edge, t):
            return True
    return False


def assign(
    title: str,
    abstract: str | None,
    group3_phrases: list[str],
) -> list[str]:
    """返回命中的标签子集(成像/空间、植物、注释)。

    注释 = 命中入库当时的组3 短语(快照语义由调用方落库保证)。
    """
    text = title if not abstract else f"{title}\n{abstract}"
    out = []
    if _hit(text, IMAGING_TERMS):
        out.append("成像/空间")
    if _hit(text, PLANT_TERMS):
        out.append("植物")
    if group3_phrases and _hit(text, group3_phrases):
        out.append("注释")
    return out
