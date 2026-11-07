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

_GROUP1_REFINE = [
    "plant", "plants", "Arabidopsis", "Oryza", "rice", "maize", "wheat",
    "soybean", "medicinal plant", "plant tissue", "leaf tissue",
    "root exudate", "phytochemistry", "natural product",
    "secondary metabolite",
]

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


PRESETS_DIR = Path(__file__).resolve().parent.parent / "presets"


def _load_preset_groups() -> list[TermGroup] | None:
    """从 presets/<topic>.json 加载检索组;无主题包或损坏时返回 None(回落内置)。"""
    if not PRESETS_DIR.is_dir():
        return None
    files = sorted(PRESETS_DIR.glob("*.json"))
    if not files:
        return None
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return [
            TermGroup(
                id=g.get("id"), name=g["name"],
                enabled=bool(g.get("enabled", 1)),
                terms=[Term(**t) for t in g.get("terms", [])],
                refine=[Term(**t) for t in g.get("refine", [])],
            )
            for g in data.get("groups", [])
        ]
    except (ValueError, KeyError, TypeError):
        return None


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
    """默认检索组:presets 优先,内置兜底(离线/无主题包)。"""
    loaded = _load_preset_groups()
    return loaded if loaded else _BUILTIN_GROUPS


# 兼容旧引用(测试/回填等):延迟求值代理
class _GroupsProxy:
    def __iter__(self):
        return iter(get_default_groups())

    def __len__(self):
        return len(get_default_groups())

    def __getitem__(self, i):
        return get_default_groups()[i]


DEFAULT_GROUPS = _GroupsProxy()
