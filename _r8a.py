import ast

# ---- queries: 形状校验 + 统一走 config.presets_dir() + group_roles()
p = "paperpulse/queries.py"
s = open(p, encoding="utf-8").read()

old = '''def _preset_files(d=None):
    from . import config as _config

    d = d if (d is not None and d.is_dir()) else (
        PRESETS_DIR if PRESETS_DIR.is_dir() else _config.presets_dir())
    if not d.is_dir():
        return None
    files = sorted(d.glob("*.json"))
    return files or None'''
new = '''def _preset_files(d=None):
    """预设文件定位:统一走 config.presets_dir()(exe 旁优先,P2-1)。"""
    from . import config as _config

    dd = d if d is not None else _config.presets_dir()
    if not Path(dd).is_dir():
        return None
    files = sorted(Path(dd).glob("*.json"))
    return files or None'''
assert old in s, "files anchor"
s = s.replace(old, new)

# 形状校验:两处 json.loads 后
old = '''    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return [
            TermGroup('''
new = '''    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"预设顶层必须是对象,得到 {type(data).__name__}(P1-1)")
        return [
            TermGroup('''
assert old in s, "groups shape"
s = s.replace(old, new)

# loud 错误接 logging
old = '''def _loud_preset_error(path, err) -> None:
    import sys as _sys

    msg = f"[presets] {path.name} 解析失败({err}),回退内置默认"
    print(msg, file=_sys.stderr)'''
new = '''def _loud_preset_error(path, err) -> None:
    import logging as _logging
    import sys as _sys

    msg = f"[presets] {path.name} 解析失败({err}),回退内置默认"
    print(msg, file=_sys.stderr)
    _logging.getLogger(__name__).error(msg)  # GUI(pythonw)下进 scan.log'''
assert old in s, "loud anchor"
s = s.replace(old, new)

# group_roles:全组(含禁用)按插入序的角色(P2-2/3)
s += '''

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
'''
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("queries ok")

# ---- journals loader: 形状校验 + 走统一 _preset_files(无参= config 路径)
p = "paperpulse/sources/journals.py"
s = open(p, encoding="utf-8").read()
old = '''    from ..queries import _preset_files, _loud_preset_error

    files = _preset_files(PRESETS_DIR)
    if files is None:
        return None
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        rows = data.get("journals", [])'''
new = '''    from ..queries import _loud_preset_error, _preset_files

    files = _preset_files()  # 统一 config.presets_dir()(P2-1)
    if files is None:
        return None
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"预设顶层必须是对象,得到 {type(data).__name__}(P1-1)")
        rows = data.get("journals", [])'''
assert old in s, "journals loader"
s = s.replace(old, new)
# 回填的注释组判定改共用函数(P2-3)
old = '''    from .. import queries as _q

    groups = [_q.from_row(r) for r in conn.execute(
        "SELECT * FROM term_groups WHERE enabled=1").fetchall()]
    g3 = next((g for g in groups if "注释" in g.name), None)
    g3_phrases = [t.phrase for t in g3.terms] if g3 else []'''
new = '''    from .. import queries as _q

    g3_phrases = _q.annotation_phrases(conn)  # 与引擎共用(P2-3)'''
assert old in s, "backfill g3"
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("journals ok")

# ---- engine: 注释组改共用
p = "paperpulse/engine.py"
s = open(p, encoding="utf-8").read()
old = '''    g3 = groups[2] if len(groups) >= 3 else None  # 第 3 组(位置语义,通用主题)
    g3_phrases = [t.phrase for t in g3.terms] if g3 else []'''
new = '''    g3_phrases = queries.annotation_phrases(conn)  # 全组位置语义,与回填共用(P2-2/3)'''
assert old in s, "engine g3"
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("engine ok")

# ---- report: 分档角色走 group_roles(全组含禁用)
p = "paperpulse/report.py"
s = open(p, encoding="utf-8").read()
old = '''def _tier_ids(conn) -> tuple[list, list[str]]:
    """当前启用组(按 id 升序):第 1 组→一档,第 2 组→二档(位置语义,换主题生效)。"""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, name FROM term_groups WHERE enabled=1 ORDER BY id")]
    if not rows:
        rows = [dict(id=g.id, name=g.name)
                for g in queries.get_default_groups() if g.enabled]
    return rows, [r["name"] for r in rows]'''
new = '''def _tier_ids(conn) -> tuple[list, list[str]]:
    """分档角色:全组(含禁用)按插入序——禁用组1 时一档为空,组2 不顶位(P2-2)。"""
    roles = queries.group_roles(conn)
    rows = [r for r in (roles["tier1"], roles["tier2"], roles["annotation"])
            if r is not None]
    return rows, [r["name"] for r in rows]'''
assert old in s, "report tier"
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("report ok")

# ---- queries_view: refine 校验用 group_roles(位置)
p = "paperpulse/gui/queries_view.py"
s = open(p, encoding="utf-8").read()
old = '''        is_third = (self.group_list.currentRow() == 2)  # 第 3 组(位置语义)
        if (is_third or "注释" in name) and not refine:'''
new = '''        from .. import queries as _q

        roles = _q.group_roles(self.conn)
        is_third = roles["annotation"] and name == roles["annotation"]["name"]
        if (is_third or "注释" in name) and not refine:'''
assert old in s, "qv refine"
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("queries_view ok")
