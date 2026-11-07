import re

# ---- P2-12: 抽 ids.py 消除 sources→engine 反向依赖
ids_src = '''"""DOI/标题归一化与主键(自 engine 抽出,消除 sources→engine 环,P2-12)。"""

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
'''
open("sm_scan/ids.py", "w", encoding="utf-8").write(ids_src)

# engine: 删除原实现,re-export
p = "sm_scan/engine.py"
s = open(p, encoding="utf-8").read()
old_block = re.search(r"_DOI_PREFIXES = \(.*?titlehash:\"\+h\n", s, re.S)
# 直接删三个定义(精确锚点)
import re as _re
s = _re.sub(r"_DOI_PREFIXES = \([\s\S]*?def paper_key\(doi: str \| None, title: str\) -> str:\n(?:.*\n)*?    return f\"titlehash:\{h\}\"\n", "", s, count=1)
s = s.replace("from . import prepare, queries, store, tags",
              "from . import prepare, queries, store, tags\nfrom .ids import normalize_doi, paper_key  # noqa: F401  (re-export)")
open(p, "w", encoding="utf-8").write(s)
import ast
ast.parse(s)
print("engine slimmed")

# sources 三处改 import ids
for f, imp in [
    ("sm_scan/sources/europepmc.py", "from .. import engine, store"),
    ("sm_scan/sources/crossref.py", "from .. import engine, store"),
    ("sm_scan/sources/biorxiv.py", "from .. import engine, store"),
]:
    s = open(f, encoding="utf-8").read()
    s = s.replace("from .. import engine, store",
                  "from .. import ids, store")
    s = s.replace("engine.normalize_doi", "ids.normalize_doi")
    s = s.replace("engine.paper_key", "ids.paper_key")
    open(f, "w", encoding="utf-8").write(s)
print("sources rewired")

# ---- P3-2 死代码:dates_ok(及其测试)、engine any_ok 残留、app.set_view
p = "sm_scan/engine.py"
s = open(p, encoding="utf-8").read()
s = _re.sub(r"\ndef dates_ok\([\s\S]*?return window\[0\] <= d <= window\[1\]\n", "\n", s, count=1)
s = s.replace("        any_ok = False\n", "")
s = s.replace("                    any_ok = True\n", "")
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("engine dead code removed")

p = "tests/test_engine_core.py"
s = open(p, encoding="utf-8").read()
s = _re.sub(r"\ndef test_dates_ok[\s\S]*?(?=\ndef |\Z)", "\n", s, count=1)
open(p, "w", encoding="utf-8").write(s)

p = "sm_scan/gui/app.py"
s = open(p, encoding="utf-8").read()
s = _re.sub(r"    def set_view\([\s\S]*?self\.views\[index\] = widget\n", "", s, count=1)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("app.set_view removed")

# ---- P3-3: 组1 refine 从 PLANT_TERMS 派生(防漂移)
p = "sm_scan/queries.py"
s = open(p, encoding="utf-8").read()
old = '''_GROUP1_REFINE = [
    "plant", "plants", "Arabidopsis", "Oryza", "rice", "maize", "wheat",
    "soybean", "medicinal plant", "plant tissue", "leaf tissue",
    "root exudate", "phytochemistry", "natural product",
    "secondary metabolite",
]'''
new = '''# 组1 refine = 植物词表去掉仅用于标签的物种专名(派生,防两表漂移,P3-3)
_GROUP1_REFINE = [t for t in PLANT_TERMS if t not in ("Medicago", "Nicotiana", "Paeonia")]'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("refine derived")
