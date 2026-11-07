"""检索式 IR 与 Europe PMC 查询翻译(spec §4)。

所有短语在查询串中一律加双引号(防 MS/MS、LC-MS 中的 / - 被分词)。
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date

# 植物词表:§7「植物」标签判定用;组1 refine 是其子集(共用同一份,防漂移)
PLANT_TERMS = [
    "plant", "plants", "Arabidopsis", "Oryza", "rice", "maize", "wheat",
    "soybean", "Medicago", "Nicotiana", "Paeonia", "medicinal plant",
    "phytochemistry", "root exudate", "leaf tissue", "plant tissue",
    "secondary metabolite", "natural product",
]

# 组1 refine 从 PLANT_TERMS 派生(去掉仅标签用的物种专名),两表不再各自维护
_GROUP1_REFINE = [t for t in PLANT_TERMS
                  if t not in ("Medicago", "Nicotiana", "Paeonia")]

_GROUP1_TERMS = [
    "mass spectrometry imaging", "MALDI imaging", "MALDI-MSI",
    "MALDI mass spectrometry imaging", "imaging mass spectrometry",
    "spatial metabolomics", "spatially resolved metabolomics", "DESI-MS",
    "desorption electrospray", "TOF-SIMS", "secondary ion mass spectrometry",
]

_GROUP2_TERMS = [
    "MALDI", "matrix-assisted laser desorption ionization",
    "matrix assisted laser desorption ionization", "MALDI imaging",
    "MALDI-MSI", "mass spectrometry imaging", "imaging mass spectrometry",
    "spatial metabolomics", "MALDI-TOF imaging",
]

_GROUP3_TERMS = [
    "metabolite annotation", "metabolite identification",
    "natural product annotation", "spectral annotation",
    "metabolite structure elucidation",
]

_GROUP3_REFINE = [
    "mass spectrometry", "MS/MS", "LC-MS", "MALDI",
    "tandem mass spectrometry",
]


@dataclass
class Term:
    phrase: str
    scope: str = "title_abs"  # title_abs | title(歧义缩写仅标题)


@dataclass
class TermGroup:
    name: str
    enabled: bool = True
    terms: list[Term] = field(default_factory=list)
    refine: list[Term] = field(default_factory=list)
    id: int | None = None


def _load_preset_groups() -> list[TermGroup] | None:
    """加载检索组:无主题包 → None(回落内置);损坏 → 响亮报错后回落内置。

    空组列表是合法配置(有意不建组),不回退(P2-2)。
    """
    files = _preset_files()
    if files is None:
        return None
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"预设顶层必须是对象,得到 {type(data).__name__}(P1-1)")
        return [
            TermGroup(
                id=g.get("id"), name=g["name"],
                enabled=bool(g.get("enabled", 1)),
                terms=[Term(**t) for t in g.get("terms", [])],
                refine=[Term(**t) for t in g.get("refine", [])],
            )
            for g in data.get("groups", [])
        ]
    except (ValueError, KeyError, TypeError) as e:
        _loud_preset_error(files[0], e)
        return None


def _preset_files(d=None):
    """预设文件定位:统一走 config.presets_dir()(exe 旁优先,P2-1)。"""
    from . import config as _config

    dd = d if d is not None else _config.presets_dir()
    if not Path(dd).is_dir():
        return None
    files = sorted(Path(dd).glob("*.json"))
    return files or None


def _loud_preset_error(path, err) -> None:
    import logging as _logging
    import sys as _sys

    msg = f"[presets] {path.name} 解析失败({err}),回退内置默认"
    print(msg, file=_sys.stderr)
    _logging.getLogger(__name__).error(msg)  # GUI(pythonw)下进 scan.log


PRESETS_DIR = Path(__file__).resolve().parent.parent / "presets"


def _terms(phrases: list[str]) -> list[Term]:
    return [Term(p) for p in phrases]


_BUILTIN_GROUPS: list[TermGroup] = [
    TermGroup(
        id=1, name="植物+空间成像",
        terms=_terms(_GROUP1_TERMS),
        refine=_terms(_GROUP1_REFINE),
    ),
    TermGroup(
        id=2, name="MALDI/MSI方法学",
        terms=_terms(_GROUP2_TERMS),
    ),
    TermGroup(
        id=3, name="产物注释",
        terms=_terms(_GROUP3_TERMS),
        refine=_terms(_GROUP3_REFINE),
    ),
]


def from_row(d: dict) -> TermGroup:
    """store.load_groups 的原始行 → TermGroup(terms/refine 为 JSON 字符串)。"""
    terms = [Term(**t) for t in json.loads(d["terms"])]
    refine_raw = json.loads(d["refine"]) if d["refine"] else []
    refine = [t if isinstance(t, Term) else Term(**t) for t in refine_raw]
    return TermGroup(
        id=d["id"], name=d["name"], enabled=bool(d["enabled"]),
        terms=terms, refine=refine,
    )


def _clause(t: Term) -> str:
    field_ = "TITLE" if t.scope == "title" else "TITLE_ABS"
    return f'{field_}:"{t.phrase}"'


def to_europepmc_query(g: TermGroup, d_from: date, d_to: date) -> str:
    """组 → Europe PMC 查询串:组内 OR、refine AND、再 AND 索引日窗口。"""
    q = "(" + " OR ".join(_clause(t) for t in g.terms) + ")"
    if g.refine:
        q += " AND (" + " OR ".join(_clause(t) for t in g.refine) + ")"
    q += f" AND FIRST_IDATE:[{d_from.isoformat()} TO {d_to.isoformat()}]"
    return q


def get_default_groups() -> list[TermGroup]:
    """默认检索组:presets 优先(空列表=有意为空,不回退),内置兜底。"""
    loaded = _load_preset_groups()
    return loaded if loaded is not None else _BUILTIN_GROUPS


# 兼容旧引用(测试/回填等):延迟求值代理
class _GroupsProxy:
    def __iter__(self):
        return iter(get_default_groups())

    def __len__(self):
        return len(get_default_groups())

    def __getitem__(self, i):
        return get_default_groups()[i]


DEFAULT_GROUPS = _GroupsProxy()


def group_roles(conn) -> dict:
    """组角色(全组含禁用,按插入序 rowid,P2-2):
    tier1=第1组、tier2=第2组、annotation=第3组(产物注释语义)。
    引擎/日报/回填/GUI 共用,禁用组不改变角色位置。
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT id, name FROM term_groups ORDER BY rowid")]
    if not rows:
        rows = [dict(id=g.id, name=g.name)
                for g in get_default_groups()]
    return {
        "tier1": rows[0] if len(rows) >= 1 else None,
        "tier2": rows[1] if len(rows) >= 2 else None,
        "annotation": rows[2] if len(rows) >= 3 else None,
    }


def annotation_phrases(conn) -> list[str]:
    """注释标签短语:第 3 组的 terms(引擎与回填共用,P2-3)。"""
    roles = group_roles(conn)
    a = roles["annotation"]
    if not a:
        return []
    row = conn.execute(
        "SELECT terms FROM term_groups WHERE id=?", (a["id"],)).fetchone()
    if not row:
        return []
    return [t.get("phrase") for t in json.loads(row["terms"])
            if t.get("phrase")]
