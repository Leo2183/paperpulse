"""Markdown 日报(spec §12):三档 + 订阅补漏 4a/4b + 待精读。

分档:含组1 → 一档;未入一档但含组2 或「成像/空间」标签 → 二档;
其余 → 三档;matched_groups 空且 subscription 非空 → 补漏节。
组命中判断走 json_each(SQL)与集合判断(Python),绝不用 LIKE 匹配 JSON。
"""

import json
from datetime import date
from pathlib import Path

from . import config, store

G1 = "植物+空间成像"
G2 = "MALDI/MSI方法学"
IMAGING_TAG = "成像/空间"
ABSTRACT_WORDS = 300


def _day_papers(conn, day: date) -> list[dict]:
    prefix = day.isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM papers WHERE first_seen LIKE ? ORDER BY pub_date DESC",
        (prefix + "%",),
    ).fetchall()]


def _tier(p: dict) -> str:
    groups = set(json.loads(p["matched_groups"] or "[]"))
    tags = set(json.loads(p["tags"] or "[]"))
    sub = p["subscription"]
    if sub:
        return "catchup"
    if G1 in groups:
        return "t1"
    if G2 in groups or IMAGING_TAG in tags:
        return "t2"
    return "t3"


def _entry(p: dict, journal_mode: dict) -> str:
    d = p["pub_date"] or "日期未知"
    j = p["journal"] or journal_mode.get(p["subscription"] or "", "")
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
        body = "> 摘要未提供(Crossref)"
    url = p["url"] or ""
    link = f"[DOI]({url})" if url else ""
    src = p["source"]
    head = f"### [{d}] {p['title']} — *{j}* {tag_s} {mark}".rstrip()
    return (f"{head}\n{p['authors'] or ''} | {link} | 来源 {src} {mark}".rstrip()
            + f"\n{body}\n")


def _count(day: list[dict], pred) -> int:
    return sum(1 for p in day if pred(p))


def generate(conn, for_date: date) -> Path:
    day = _day_papers(conn, for_date)
    modes = {
        r["name"]: r["mode"]
        for r in conn.execute("SELECT name, mode FROM journal_subscriptions")
    }
    tiers = {"t1": [], "t2": [], "t3": [], "catchup": []}
    for p in day:
        tiers[_tier(p)].append(p)

    sub_cnt = _count(day, lambda p: bool(p["subscription"]))
    pmc_cnt = _count(day, lambda p: not p["subscription"] and p["source"] == "europepmc")
    brx_cnt = _count(day, lambda p: p["source"] == "biorxiv")
    total = len(day)

    lines = [
        f"# sm-scan 日报 {for_date.isoformat()}",
        f"新增 {total} 篇(Europe PMC {pmc_cnt} / 订阅 {sub_cnt} / bioRxiv {brx_cnt})"
        f" | 组1✓ 组2✓ 组3✓ | 订阅 {len(modes)} 刊",
        f"分档:核心(植物+成像){len(tiers['t1'])} | 方法学 {len(tiers['t2'])}"
        f" | 其他 {len(tiers['t3'])} | 订阅补漏 {len(tiers['catchup'])}"
        "(通常为日报最大一节,属预期)",
        "",
    ]
    sections = [
        (f"## 一、核心:植物+空间成像({len(tiers['t1'])})", tiers["t1"]),
        (f"## 二、方法学:MALDI/MSI({len(tiers['t2'])})", tiers["t2"]),
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
    lines.append("---")
    lines.append(f"## 待精读({len(todos)},累积状态,与前文分档有重叠)")
    for t in todos:
        digest = f" — {t['llm_digest']}" if t["llm_digest"] else ""
        lines.append(f"- [{t['title']}]({t['url'] or ''}){digest}")
    lines.append("")

    path = config.reports_dir() / f"{for_date.isoformat()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
