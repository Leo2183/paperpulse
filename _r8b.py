import ast, re as _re

# ---- P2-4: NewJournalWorker 异常兜底 + 重复刊检测 + issn 唯一索引
p = "paperpulse/gui/queries_view.py"
s = open(p, encoding="utf-8").read()
old = '''            settings = store_mod.load_settings(conn)
            self.stage.emit(f"探测 {self.title} 近 90 天发文量…")
            try:
                count = crossref.count_recent(
                    self.issn, mailto=settings.get("crossref_mailto") or None)
            except Exception:
                count = -1
            mode = journals.suggest_mode(count) if count >= 0 else "filter"
            cur = conn.execute('''
new = '''            settings = store_mod.load_settings(conn)
            dup = conn.execute(
                "SELECT id FROM journal_subscriptions WHERE issn=?",
                (self.issn,)).fetchone()
            if dup:
                self.stage.emit(f"{self.title} 已在订阅列表中,跳过重复添加")
                self.done.emit(0)
                return
            self.stage.emit(f"探测 {self.title} 近 90 天发文量…")
            try:
                count = crossref.count_recent(
                    self.issn, mailto=settings.get("crossref_mailto") or None)
            except Exception:
                count = -1
            mode = journals.suggest_mode(count) if count >= 0 else "filter"
            cur = conn.execute('''
assert old in s, "worker head"
s = s.replace(old, new)
old = '''        except Exception:
            count = -1'''
# 上面 head 替换已含;接下来包整体 try/except:
old2 = '''    def run(self):
        from .. import store as store_mod

        conn = store_mod.connect()
        store_mod.init_db(conn)
        n = 0
        try:'''
new2 = '''    def run(self):
        from .. import store as store_mod

        conn = store_mod.connect()
        store_mod.init_db(conn)
        n = 0
        try:
            self._run_inner(conn) if False else None
        except Exception as e:  # P2-4:失败必须收尾,界面不得永久卡住
            try:
                self.stage.emit(f"添加/回填失败:{e}")
            except RuntimeError:
                pass
            self.done.emit(n)
        finally:
            conn.close()

    def _run_inner(self, conn) -> int:
        from .. import store as store_mod

        n = 0
        try:'''
assert old2 in s, "worker run"
s = s.replace(old2, new2)
# _run_inner 尾:return n(原来 finally: conn.close(); done.emit(n) 移除)
old3 = '''        finally:
            conn.close()
        self.done.emit(n)


def _parse_terms'''
new3 = '''        return n


def _parse_terms'''
assert old3 in s, "worker tail"
s = s.replace(old3, new3)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("worker ok")

# ---- P3-1: 日报摘要行动态口径
p = "paperpulse/report.py"
s = open(p, encoding="utf-8").read()
old = '''        f"分档:核心(植物+成像){len(tiers['t1'])} | 方法学 {len(tiers['t2'])}"
        f" | 其他 {len(tiers['t3'])} | 订阅补漏 {len(tiers['catchup'])}"'''
new = '''        f"分档:核心({name1}){len(tiers['t1'])} | {name2} {len(tiers['t2'])}"
        f" | 其他 {len(tiers['t3'])} | 订阅补漏 {len(tiers['catchup'])}"'''
assert old in s, "digest line"
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("report ok")

# ---- P3-7: out_dir Path 归一
s = open(p, encoding="utf-8").read()
s = s.replace("    out = (out_dir or config.reports_dir())",
              "    out = Path(out_dir) if out_dir is not None else config.reports_dir()")
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("out_dir ok")

# ---- P3-2: 删 BackfillWorker 死类
p = "paperpulse/gui/queries_view.py"
s = open(p, encoding="utf-8").read()
s = _re.sub(r"class BackfillWorker\(QThread\):\n(?:    .*\n|\n)*?\n\n", "", s, count=1)
if "BackfillWorker" in s:
    # 保守:按标记切
    i = s.find("class BackfillWorker")
    j = s.find("class ", i + 10)
    s = s[:i] + s[j:]
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("backfill worker removed" if "BackfillWorker" not in s else "STILL PRESENT")

# ---- P3-3: issn 唯一索引(迁移幂等)
p = "paperpulse/store.py"
s = open(p, encoding="utf-8").read()
s = s.replace("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);",
              "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);\n"
              "CREATE UNIQUE INDEX IF NOT EXISTS idx_journal_issn"
              " ON journal_subscriptions(issn);")
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("store ok")

# ---- P3-6: 死导入(app QLabel 残留?__main__ _date;__init__ docstring)
p = "paperpulse/gui/app.py"
s = open(p, encoding="utf-8").read()
s = s.replace("    QLabel, QListWidget, QMainWindow,\n", "    QListWidget, QMainWindow,\n") if "QLabel," in s else s
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
p = "paperpulse/__main__.py"
s = open(p, encoding="utf-8").read()
s = s.replace("        from datetime import date as _date\n\n        from .engine import run_scan\n", "        from .engine import run_scan\n")
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("dead imports ok")

# ---- P3-5: 测试质量(恒真断言/类级 _cb)
p = "tests/test_gui_smoke.py"
s = open(p, encoding="utf-8").read()
s = s.replace('    assert "解析" not in win.queries_view.j_status.text() or True\n', "")
s = s.replace('''    class SyncWorker:
        """同步 FakeWorker:job 执行后立即派发 done。"""

        _cb = []

        class done:
            @staticmethod
            def connect(fn):
                SyncWorker._cb.append(fn)

        def __init__(self, fn):
            self._fn = fn

        def start(self):
            res = self._fn()
            for cb in list(SyncWorker._cb):
                cb(res)''',
'''    class SyncWorker:
        """同步 FakeWorker:job 执行后立即派发 done(实例级回调,不跨用例累积)。"""

        def __init__(self, fn):
            self._fn = fn
            self._cb = []

        class _DoneSignal:
            def __init__(self, owner):
                self._owner = owner

            def connect(self, fn):
                self._owner._cb.append(fn)

        @property
        def done(self):
            return SyncWorker._DoneSignal(self)

        def start(self):
            res = self._fn()
            for cb in list(self._cb):
                cb(res)''')
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("tests ok")

# ---- 测试补 mock count_recent(94 秒问题)
s = open(p, encoding="utf-8").read()
s = s.replace('''    monkeypatch.setattr(jmod, "backfill_journal", boom)''',
'''    monkeypatch.setattr(jmod, "backfill_journal", boom)
    monkeypatch.setattr(
        __import__("paperpulse.gui.queries_view", fromlist=["crossref"]).crossref,
        "count_recent", lambda issn, days=90, mailto=None: 10)''')
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("test mock ok")
