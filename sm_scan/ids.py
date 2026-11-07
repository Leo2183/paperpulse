"""DOI/标题归一化与主键(自 engine 抽出,消除 sources→engine 环,P2-12)。"""

import hashlib
import re

_DOI_PREFIXES = (
    "https://doi.org/", "http://doi.org/", "doi.org/", "doi:", "info:doi/",
)


def normalize_doi(raw):
    """小写、剥常见前缀、分号拼接取首段;空返回 None。"""
    if not raw or not raw.strip():
        return None
    s = raw.strip().lower().split(";")[0].strip()
    changed = True
    while changed:
        changed = False
        for pref in _DOI_PREFIXES:
            if s.startswith(pref):
                s = s[len(pref):].strip()
                changed = True
    return s or None


def _norm_title(title):
    t = re.sub(r"[^\w\s]", "", title.lower())
    return " ".join(t.split())


def paper_key(doi, title):
    d = normalize_doi(doi)
    if d:
        return d
    h = hashlib.sha1(_norm_title(title).encode("utf-8")).hexdigest()[:16]
    return "titlehash:" + h
