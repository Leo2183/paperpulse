import re

# ---- P2-10: tags 词边界(单词类 \b,多词短语子串)
p = "sm_scan/tags.py"
s = open(p, encoding="utf-8").read()
old = '''def _hit(text: str, phrases: list[str]) -> bool:
    t = text.lower()
    return any(p.lower() in t for p in phrases)'''
new = '''def _hit(text: str, phrases: list[str]) -> bool:
    """单词短语按词边界(\b)匹配防子串误报;多词短语仍子串(P2-10)。"""
    t = text.lower()
    for p in phrases:
        q = p.lower()
        if " " in q or "-" in q:
            if q in t:
                return True
        elif re.search(r"\b" + re.escape(q) + r"\b", t):
            return True
    return False'''
assert old in s
s = s.replace(old, new)
s = s.replace('"""\n\nIMAGING_TERMS', '"""\n\nimport re\n\nIMAGING_TERMS')
open(p, "w", encoding="utf-8").write(s)
print("tags ok")

# ---- P2-1: 安全阀日均口径
p = "sm_scan/sources/journals.py"
s = open(p, encoding="utf-8").read()
old = '''    daily = (window[1] - window[0]).days <= DAILY_WINDOW_DAYS
    demoted = False
    if row["mode"] == "full":
        batch = list(papers)
        if daily and len(batch) > SAFETY_VALVE:'''
new = '''    window_days = max((window[1] - window[0]).days, 1)
    daily = window_days <= DAILY_WINDOW_DAYS
    demoted = False
    if row["mode"] == "full":
        batch = list(papers)
        # 阈值按日均(P2-1):多天窗口合计大不触发,单日暴涨才触发
        if daily and len(batch) / window_days > SAFETY_VALVE:'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("valve ok")

# ---- P2-2: engine 收集 demoted → per_source;日报读 run 显示
p = "sm_scan/engine.py"
s = open(p, encoding="utf-8").read()
old = '''        try:
            for p, jrow in journals.scan_journals(
                conn, window, date.today(),
                mailto=settings.get("crossref_mailto") or None,
            ):
                t["fetched"] += 1
                stale = p.get("_stale", False)  # 注册日保护:只入库不计新增
                if _upsert(p, subscription=jrow["name"]) and not stale:
                    new_count += 1
                    t["new"] += 1'''
new = '''        demoted: set[str] = set()
        try:
            for p, jrow in journals.scan_journals(
                conn, window, date.today(),
                mailto=settings.get("crossref_mailto") or None,
            ):
                t["fetched"] += 1
                stale = p.get("_stale", False)  # 注册日保护:只入库不计新增
                if jrow.get("_demoted"):
                    demoted.add(jrow["name"])  # P2-2:日报可见
                if _upsert(p, subscription=jrow["name"]) and not stale:
                    new_count += 1
                    t["new"] += 1
            if demoted:
                t["demoted"] = sorted(demoted)'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("engine demoted ok")

# ---- P2-4: --llm 未配置可见报错
p = "sm_scan/engine.py"
s = open(p, encoding="utf-8").read()
old = '''    if not (settings.get("llm_base_url") and settings.get("llm_model")):
        log.warning("--llm set but LLM not configured (base_url/model); digest skipped")
        return'''
new = '''    if not (settings.get("llm_base_url") and settings.get("llm_model")):
        log.warning("--llm set but LLM not configured (base_url/model); digest skipped")
        print("[llm] 未配置:请在 GUI「检索式配置 → 源与 LLM」填写 base_url 与 model;"
              "本次跳过导读(spec §13 明确报错,不静默)")
        return'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("llm loud ok")

# ---- P3-4: crossref indexed_date = created
p = "sm_scan/sources/crossref.py"
s = open(p, encoding="utf-8").read()
old = '''    p = store.Paper(
        id=engine.paper_key(doi, title),
        doi=doi,
        pmcid=None,
        title=title,
        authors=_authors_display(item),
        journal=(item.get("container-title") or [None])[0],
        pub_date=pub,
        indexed_date=None,'''
new = '''    created = (item.get("created") or {}).get("date-time")  # P3-4: 注册日
    p = store.Paper(
        id=engine.paper_key(doi, title),
        doi=doi,
        pmcid=None,
        title=title,
        authors=_authors_display(item),
        journal=(item.get("container-title") or [None])[0],
        pub_date=pub,
        indexed_date=created[:10] if created else None,'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("crossref ok")

# ---- P2-2: 日报读当日 run 的 demoted 注记
p = "sm_scan/report.py"
s = open(p, encoding="utf-8").read()
old = '''    lines.append("---")
    lines.append(f"## 待精读({len(todos)},累积状态,与前文分档有重叠)")'''
new = '''    run = conn.execute(
        "SELECT per_source FROM runs WHERE finished_at IS NOT NULL"
        " ORDER BY id DESC LIMIT 1").fetchone()
    if run and run["per_source"]:
        try:
            demoted = json.loads(run["per_source"]).get("journals", {}).get("demoted")
        except ValueError:
            demoted = None
        if demoted:
            lines.append(f"> 注:安全阀降级 filter(发文量暴涨):{', '.join(demoted)}")
    lines.append("---")
    lines.append(f"## 待精读({len(todos)},累积状态,与前文分档有重叠)")'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("report demoted ok")
