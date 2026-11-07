"""Markdown 日报(spec §12):三档 + 订阅补漏 4a/4b + 待精读。

分档:含组1 → 一档;未入一档但含组2 或「成像/空间」标签 → 二档;
其余 → 三档;matched_groups 空且 subscription 非空 → 补漏节。
组命中判断走 json_each(SQL)与集合判断(Python),绝不用 LIKE 匹配 JSON。
"""

import json
from datetime import date
from pathlib import Path

from . import config, queries, store

IMAGING_TAG = "成像/空间"


def _tier_ids(conn) -> tuple[list, list[str]]:
    """分档角色:全组(含禁用)按插入序——禁用组1 时一档为空,组2 不顶位(P2-2)。"""
    roles = queries.group_roles(conn)
    rows = [r for r in (roles["tier1"], roles["tier2"], roles["annotation"])
            if r is not None]
    return rows, [r["name"] for r in rows]
ABSTRACT_WORDS = 300


def _day_papers(conn, day: date) -> list[dict]:
    prefix = day.isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM papers WHERE first_seen LIKE ?"
        " AND COALESCE(stale,0)=0 ORDER BY pub_date DESC",
        (prefix + "%",),
    ).fetchall()]


def _tier(p: dict, g1: str, g2: str) -> str:
    groups = set(json.loads(p["matched_groups"] or "[]"))
    tags = set(json.loads(p["tags"] or "[]"))
    # 组命中优先于订阅(spec §12:订阅刊论文同时命中检索组按组分档,不进补漏)
    if g1 in groups:
        return "t1"
    if g2 in groups or IMAGING_TAG in tags:
        return "t2"
    if p["subscription"]:
        return "catchup"
    return "t3"


def _entry(p: dict, journal_mode: dict) -> str:
    d = p["pub_date"] or "日期未知"
    j = p["journal"] or p["subscription"] or ""  # P3-5:回退订阅名,不用模式
    tag_s = " ".join(f"`{t}`" for t in json.loads(p["tags"] or "[]"))
    mark = ""
    if p["subscription"]:
        mark = "[整刊]" if journal_mode.get(p["subscription"]) == "full" else "[订阅]"
    digest = p["llm_digest"]
    if digest:
        body = f"> **导读**:{digest}"
    elif p["abstract"]:
        body = "> " + " ".join(p["abstract"].split()[:ABSTRACT_WORDS])
    else:
        body = "> 摘要未提供"
    url = p["url"] or ""
    link = f"[DOI]({url})" if url else ""
    src = p["source"]
    head = f'### [{d}] {p["title"]} — *{j}* {tag_s} {mark}'.rstrip()
    tail = (f'{p["authors"] or ""} | {link} | 来源 {src}').rstrip()
    return head + '\n' + tail + '\n' + body + '\n'


def _count(day: list[dict], pred) -> int:
    return sum(1 for p in day if pred(p))


def generate(conn, for_date: date, out_dir=None) -> Path:
    day = _day_papers(conn, for_date)
    modes = {
        r["name"]: r["mode"]
        for r in conn.execute("SELECT name, mode FROM journal_subscriptions")
    }
    group_rows, group_names = _tier_ids(conn)
    enabled_group_names = [
        r["name"] for r in conn.execute(
            "SELECT name FROM term_groups WHERE enabled=1 ORDER BY rowid")
    ] or group_names
    g1 = str(group_rows[0]["id"]) if len(group_rows) >= 1 else None
    g2 = str(group_rows[1]["id"]) if len(group_rows) >= 2 else None
    name1 = group_names[0] if group_names else "核心"
    name2 = group_names[1] if len(group_names) > 1 else "方法学"
    tiers = {"t1": [], "t2": [], "t3": [], "catchup": []}
    for p in day:
        tiers[_tier(p, g1, g2)].append(p)

    sub_cnt = _count(day, lambda p: bool(p["subscription"]))
    pmc_cnt = _count(day, lambda p: not p["subscription"] and p["source"] == "europepmc")
    brx_cnt = _count(day, lambda p: p["source"] == "biorxiv")
    total = len(day)

    lines = [
        f"# paperpulse 日报 {for_date.isoformat()}",
        f"新增 {total} 篇(Europe PMC {pmc_cnt} / 订阅 {sub_cnt} / bioRxiv {brx_cnt})"
        f" | 组:{' '.join(n + '✓' for n in enabled_group_names)} | 订阅 {len(modes)} 刊",
        f"分档:核心({name1}){len(tiers['t1'])} | {name2} {len(tiers['t2'])}"
        f" | 其他 {len(tiers['t3'])} | 订阅补漏 {len(tiers['catchup'])}"
        "(通常为日报最大一节,属预期)",
        "",
    ]
    sections = [
        (f"## 一、核心:{name1}({len(tiers['t1'])})", tiers["t1"]),
        (f"## 二、{name2}({len(tiers['t2'])})", tiers["t2"]),
        (f"## 三、其他({len(tiers['t3'])})", tiers["t3"]),
    ]
    for head, ps in sections:
        lines.append(head)
        for p in ps:
            lines.append(_entry(p, modes))
            lines.append("")
    if tiers["catchup"]:
        lines.append(
            f"## 四、订阅补漏({len(tiers['catchup'])},未命中任何检索式,"
            "来自订阅刊,确定性补齐)")
        per_journal = {}
        for p in tiers["catchup"]:
            name = p["subscription"] or "?"
            per_journal[name] = per_journal.get(name, 0) + 1
        counts = " | ".join(
            f"{k} × {v}" for k, v in sorted(
                per_journal.items(), key=lambda kv: -kv[1]))
        lines.append(f"每刊计数:{counts}")
        if conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] <= 2:
            lines.append("(首跑回填,单日量大属预期;日常窗口会回落)")
        a = [p for p in tiers["catchup"] if modes.get(p["subscription"]) != "full"]
        b = [p for p in tiers["catchup"] if modes.get(p["subscription"]) == "full"]
        lines.append(f"### 4a 巨刊筛选(filter,标 [订阅])")
        for p in a:
            lines.append(_entry(p, modes))
            lines.append("")
        lines.append("### 4b 整刊直收(full 窄刊,标 [整刊]——全收是设计意图,扫一眼有无漏即可)")
        for p in b:
            lines.append(_entry(p, modes))
            lines.append("")

    todos = [dict(r) for r in conn.execute(
        "SELECT title, url, llm_digest FROM papers WHERE to_read=1"
    ).fetchall()]
    run = conn.execute(
        "SELECT per_source FROM runs WHERE finished_at IS NOT NULL"
        " ORDER BY id DESC LIMIT 1").fetchone()
    if run and run["per_source"]:
        try:
            dem = json.loads(run["per_source"]).get("journals", {}).get("demoted")
        except ValueError:
            dem = None
        if dem:
            lines.append(f"> 注:安全阀降级 filter(单日发文量暴涨):{', '.join(dem)}")
    lines.append("---")
    lines.append(f"## 待精读({len(todos)},累积状态,与前文分档有重叠)")
    for t in todos:
        digest = f" — {t['llm_digest']}" if t["llm_digest"] else ""
        lines.append(f"- [{t['title']}]({t['url'] or ''}){digest}")
    lines.append("")

    from pathlib import Path as _P
    out = _P(out_dir) if out_dir is not None else config.reports_dir()
    if out_dir is not None:
        out.mkdir(parents=True, exist_ok=True)
    path = out / f"{for_date.isoformat()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def export_todo(conn, for_date: date | None = None) -> Path:
    """待精读清单(标题+DOI+导读),衔接精读工作流(spec §9 视图2)。"""
    for_date = for_date or date.today()
    rows = [dict(r) for r in conn.execute(
        "SELECT title, url, doi, llm_digest FROM papers WHERE to_read=1"
        " ORDER BY first_seen DESC")]
    lines = [f"# 待精读清单 {for_date.isoformat()}({len(rows)} 篇)", ""]
    for r in rows:
        digest = f" — {r['llm_digest']}" if r["llm_digest"] else ""
        link = r["url"] or (f"https://doi.org/{r['doi']}" if r["doi"] else "")
        item = f"- [{r['title']}]({link})" if link else f"- {r['title']}"
        lines.append(item + digest)
    lines.append("")
    path = config.reports_dir() / f"todo-reading-{for_date.isoformat()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
