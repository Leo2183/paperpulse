import ast
p = "paperpulse/gui/queries_view.py"
s = open(p, encoding="utf-8").read()

# ---- P1-1: _run_bg 统一后台入口(防重入 + deleteLater)
anchor = "class FnWorker(QThread):"
assert anchor in s
s = s.replace(anchor, '''class FnWorker(QThread):
    """通用后台函数执行(P2-8:主线程不再同步发 HTTP)。

    生命周期:done 发射后 self-deleteLater;持有者经 QueriesView._run_bg
    做同 key 防重入(运行中重复触发直接拒绝,P1-1)。
    """

    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.finished.connect(self.deleteLater)

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:  # noqa: BLE001
            self.done.emit(e)


FNWORKER_ANCHOR = None  # marker: class body below replaces the old one''', 1)

# 删除旧 FnWorker 类体(以 marker 为界删到下一个 class)
marker = "FNWORKER_ANCHOR = None"
i0 = s.index(marker)
i1 = s.index("class ", i0 + 10)
s = s[:i0] + s[i1:]

# QueriesView._run_bg + worker 注册表
anchor2 = "class QueriesView(QWidget):"
assert anchor2 in s
s = s.replace(anchor2, '''class QueriesView(QWidget):
    def _run_bg(self, key: str, job, done_cb) -> bool:
        """统一后台入口:同 key 运行中拒绝重入;done 后线程自毁(P1-1)。"""
        w = self._bg_workers.get(key)
        if w is not None and w.isRunning():
            return False
        worker = FnWorker(job)
        worker.done.connect(done_cb)
        self._bg_workers[key] = worker
        worker.start()
        return True''', 1)
# __init__ 注册表
old = '''        self._worker = None
        self._tg_worker = None'''
alt = '''        self._worker = None'''
if old in s:
    s = s.replace(old, '''        self._worker = None
        self._tg_worker = None
        self._bg_workers: dict = {}''')
elif alt in s:
    s = s.replace(alt, '''        self._worker = None
        self._bg_workers: dict = {}''', 1)
else:
    # 兜底:tabs 构造前插入
    s = s.replace("        tabs = QTabWidget()",
                  "        self._bg_workers: dict = {}\n        tabs = QTabWidget()", 1)

open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("_run_bg ok")
