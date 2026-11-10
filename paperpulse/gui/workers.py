"""GUI 通用后台 worker(第11轮 P3-9:自 queries_view 抽出,视图间不互相依赖)。"""

from PySide6.QtCore import QThread, Signal


class FnWorker(QThread):
    """通用后台函数执行;持有者经 _run_bg 防重入(P1-1/P2-8)。

    不接 deleteLater:自毁会让 _bg_workers 里的包装器失效,
    第二次点击在守卫处抛 RuntimeError(P0);已结束对象最多每 key 一个。
    """

    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:  # noqa: BLE001
            self.done.emit(e)
