# ---- P3-5: report 细节
p = "sm_scan/report.py"
s = open(p, encoding="utf-8").read()
old = '''def _entry(p: dict, journal_mode: dict) -> str:
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
            + f"\n{body}\n")'''
new = '''def _entry(p: dict, journal_mode: dict) -> str:
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
    head = f"### [{d}] {p['title']} — *{j}* {tag_s} {mark}".rstrip()
    return (f"{head}\n{p['authors'] or ''} | {link} | 来源 {src}".rstrip()
            + f"\n{body}\n")'''
assert old in s, "entry anchor"
s = s.replace(old, new)
old2 = '''    for r in rows:
        digest = f" — {r['llm_digest']}" if r["llm_digest"] else ""
        lines.append(f"- [{r['title']}]({r['url'] or r['doi'] or ''}){digest}")
    lines.append("")
    path = config.reports_dir() / f"todo-reading-{for_date.isoformat()}.md"'''
new2 = '''    for r in rows:
        digest = f" — {r['llm_digest']}" if r["llm_digest"] else ""
        link = r["url"] or (f"https://doi.org/{r['doi']}" if r["doi"] else "")
        item = f"- [{r['title']}]({link})" if link else f"- {r['title']}"
        lines.append(item + digest)
    lines.append("")
    path = config.reports_dir() / f"todo-reading-{for_date.isoformat()}.md"'''
assert old2 in s, "todo anchor"
s = s.replace(old2, new2)
open(p, "w", encoding="utf-8").write(s)
print("report ok")

# ---- P3-8: 锁原子创建(open x);P2-9 回填防重入;P2-14 logsetup
p = "sm_scan/task.py"
s = open(p, encoding="utf-8").read()
old = '''    lock.write_text(json.dumps({"pid": _my_pid(), "ts": time.time()}))
    return True'''
new = '''    try:
        with open(lock, "x", encoding="utf-8") as f:  # 原子创建(P3-8)
            f.write(json.dumps({"pid": _my_pid(), "ts": time.time()}))
    except FileExistsError:
        return False  # 竞态:他人刚创建
    return True'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("lock ok")

p = "sm_scan/gui/queries_view.py"
s = open(p, encoding="utf-8").read()
old = '''        self._worker = BackfillWorker(jid)'''
new = '''        if self._worker and self._worker.isRunning():  # P2-9 防重入
            self.j_status.setText("上一轮回填仍在进行,请稍候。")
            return
        self._worker = BackfillWorker(jid)'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("queries ok")
