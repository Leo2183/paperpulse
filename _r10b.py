import ast, re as _re

def load(p):
    return open(p, encoding="utf-8").read()

def save(p, s):
    open(p, "w", encoding="utf-8").write(s)
    ast.parse(s)

def rep(s, old, new, tag):
    assert old in s, f"MISS: {tag}"
    return s.replace(old, new)

# ---- P2-3: 回填失败删半成品订阅行
p = "paperpulse/gui/queries_view.py"
s = load(p)
s = rep(s, '''        cur = conn.execute(
            "INSERT INTO journal_subscriptions (name, issn, mode, route,"
            " enabled, added_at) VALUES (?,?,?,'',1,datetime('now','localtime'))",
            (self.title, self.issn, mode))
        conn.commit()
        jid = cur.lastrowid''',
'''        cur = conn.execute(
            "INSERT INTO journal_subscriptions (name, issn, mode, route,"
            " enabled, added_at) VALUES (?,?,?,'',1,datetime('now','localtime'))",
            (self.title, self.issn, mode))
        conn.commit()
        jid = cur.lastrowid
        self._inserted_jid = jid  # 失败时由 run() 回滚(P2-3)''', "P2-3 mark")
s = rep(s, '''        except Exception as e:  # P2-4:失败必须收尾,界面不得永久卡住
            try:
                self.stage.emit(f"添加/回填失败:{e}")
            except RuntimeError:
                pass
            n = 0''',
'''        except Exception as e:  # P2-4:失败必须收尾,界面不得永久卡住
            try:
                self.stage.emit(f"添加/回填失败:{e};已撤销订阅行,可重试")
            except RuntimeError:
                pass
            jid = getattr(self, "_inserted_jid", None)
            if jid is not None:  # P2-3:删半成品订阅,重复添加可重试
                try:
                    conn.execute(
                        "DELETE FROM journal_subscriptions WHERE id=?", (jid,))
                    conn.commit()
                except Exception:
                    pass
                self._inserted_jid = None
            n = 0''', "P2-3 rollback")
# 取消文案 + 完成语(P3-5)
s = rep(s, 'self.j_status.setText("正在取消回 fill…")',
        'self.j_status.setText("正在取消回填…")', "cancel text")
s = rep(s, "        self._worker.done.connect(lambda _n: self._reload_journals())",
        '''        self._worker.done.connect(
            lambda n: (self._reload_journals(),
                       self.j_status.setText(
                           self.j_status.text() if "失败" in self.j_status.text()
                           else f"回填完成,新增 {n} 篇")))''', "done msg")

# P3-4: refine 校验按注释组 id
s = rep(s, '''        from .. import queries as _q

        roles = _q.group_roles(self.conn)
        is_third = bool(roles["annotation"]) and name == roles["annotation"]["name"]
        if (is_third or "注释" in name) and not refine:''',
'''        from .. import queries as _q

        roles = _q.group_roles(self.conn)
        ann = roles["annotation"]
        gid = groups[row]["id"] if 0 <= row < len(groups) else None
        is_third = bool(ann) and gid == ann["id"]  # 按组 id,改名不可绕过(P3-4)
        if (is_third or "注释" in name) and not refine:''', "refine by id")
save(p, s)
print("queries_view ok")

# ---- P3-1/2: 日报摘要行动态 + 启用组勾选
p = "paperpulse/report.py"
s = load(p)
s = rep(s, '''        f"分档:核心({name1}){len(tiers['t1'])} | {name2} {len(tiers['t2'])}"
        f" | 其他 {len(tiers['t3'])} | 订阅补漏 {len(tiers['catchup'])}"
        "(通常为日报最大一节,属预期)",''',
'''        f"分档:核心({name1}){len(tiers['t1'])} | {name2} {len(tiers['t2'])}"
        f" | 其他 {len(tiers['t3'])} | 订阅补漏 {len(tiers['catchup'])}"
        "(通常为日报最大一节,属预期)",''', "digest noop") if False else s
old = '''        f" | 组:{' '.join(n + '✓' for n in group_names)} | 订阅 {len(modes)} 刊",'''
assert old in s, "MISS: header groups"
enabled = '''        f" | 组:{' '.join(n + '✓' for n in enabled_group_names)} | 订阅 {len(modes)} 刊",'''
s = s.replace(old, enabled)
old = '''    group_rows, group_names = _tier_ids(conn)'''
assert old in s, "MISS: tier call"
s = s.replace(old, '''    group_rows, group_names = _tier_ids(conn)
    enabled_group_names = [
        r["name"] for r in conn.execute(
            "SELECT name FROM term_groups WHERE enabled=1 ORDER BY rowid")
    ] or group_names''')
# 摘要行 name1/name2 已在上轮接线?grep 确认后处理
if "分档:核心(植物+成像)" in s:
    s = rep(s, "        f\"分档:核心(植物+成像){len(tiers['t1'])} | 方法学 {len(tiers['t2'])}\"",
            "        f\"分档:核心({name1}){len(tiers['t1'])} | {name2} {len(tiers['t2'])}\"",
            "digest line")
save(p, s)
print("report ok")

# ---- P3-6: out_dir Path 化
s = load(p)
s = rep(s, "    out = (out_dir or config.reports_dir())",
        "    from pathlib import Path as _P\n    out = _P(out_dir) if out_dir is not None else config.reports_dir()",
        "out_dir path") if "out = (out_dir or config.reports_dir())" in s else s
if "out = _P(out_dir)" not in s and "Path(out_dir)" not in s:
    # 兜底找现有实现
    assert "out_dir" in s
    s = rep(s, "    path = config.reports_dir() / f\"{for_date.isoformat()}.md\"",
            "    from pathlib import Path as _P\n    out = _P(out_dir) if out_dir is not None else config.reports_dir()\n    if out_dir is not None:\n        out.mkdir(parents=True, exist_ok=True)\n    path = out / f\"{for_date.isoformat()}.md\"", "out_dir fallback")
save(p, s)
print("out_dir ok")

# ---- P3-3: 删 BackfillWorker 死类 + PRESETS_DIR 死常量
p = "paperpulse/gui/queries_view.py"
s = load(p)
assert "class BackfillWorker" in s, "MISS: BackfillWorker"
i0 = s.index("class BackfillWorker")
i1 = s.index("class ", i0 + 10)
s = s[:i0] + s[i1:]
save(p, s)
print("BackfillWorker removed")

for p, pat in (("paperpulse/queries.py", "PRESETS_DIR = Path("),
               ("paperpulse/sources/journals.py", "PRESETS_DIR = Path(")):
    s = load(p)
    if pat in s:
        i0 = s.index(pat)
        i1 = s.index("\n", i0)
        s = s[:i0] + s[i1 + 1:]
        save(p, s)
        print(f"PRESETS_DIR removed from {p}")

# ---- P3-9: docstring / QLabel / README / pyproject
p = "paperpulse/__init__.py"
s = load(p)
s = s.replace("空间代谢组学论文扫描器", "可配置的公开论文定期监控器")
s = s.replace("空间代谢组学论文定期扫描器", "可配置的公开论文定期监控器")
save(p, s)
print("init doc ok")

p = "paperpulse/gui/app.py"
s = load(p)
if "QLabel" in s and "QLabel," in s and s.count("QLabel") == 1:
    s = s.replace("    QLabel, QListWidget, QMainWindow,\n", "    QListWidget, QMainWindow,\n")
    save(p, s)
    print("QLabel removed")

p = "README.md"
s = load(p)
s = s.replace("# 140 用例,离线", "# 154 用例,离线")
if "供应商" not in s:
    s = rep(s, "## LLM 中文导读(可选,默认关)\n\nGUI → 检索式配置 → 源与 LLM:填 OpenAI 兼容 `base_url` / `api_key` / `model`",
            "## LLM 中文导读(可选,默认关)\n\nGUI → 检索式配置 → 源与 LLM:**供应商下拉**(智谱 GLM/Coding Plan、DeepSeek、OpenAI、Kimi、通义、SiliconFlow、火山方舟、xAI、Gemini 兼容、Groq、本地 Ollama/LM Studio、自定义)自动填 `base_url`;填 `api_key` 后**「获取模型列表」**直接拉取可用模型下拉选择", "readme llm")
save(p, s)
print("readme ok")

p = "pyproject.toml"
s = load(p)
s = rep(s, 'version = "0.1.0"', 'version = "0.1.1"', "version")
save(p, s)
print("pyproject ok")
