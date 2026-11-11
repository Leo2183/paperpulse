"""GUI 通用后台 worker(第11轮 P3-9:自 queries_view 抽出,视图间不互相依赖)。

第13轮 P1-1:六个 QThread 散在三个视图里,关窗时靠 getattr 逐个点名必然
漏——这里提供统一注册表,closeEvent 经 shutdown() 请求中断并等待,绝不
带着活线程退出(析构运行中的 QThread 会让进程以 0xC0000409 abort)。
"""

import threading

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


# ---------------------------------------------------------------- 注册表
_workers: set = set()
_lock = threading.Lock()


def start(w) -> None:
    """启动 worker 并登记;结束自动除名(测试里的假 worker 没有 finished,跳过)。"""
    with _lock:
        _workers.add(w)
    fin = getattr(w, "finished", None)
    if fin is not None:
        fin.connect(lambda: _discard(w))
    w.start()


def _discard(w) -> None:
    with _lock:
        _workers.discard(w)


def running() -> int:
    with _lock:
        return sum(1 for w in _workers if w.isRunning())


def shutdown(timeout_ms: int = 2000) -> bool:
    """请求全部运行中 worker 中断并等待;全部退出返回 True(可安全关窗)。"""
    with _lock:
        alive = [w for w in _workers if w.isRunning()]
    for w in alive:
        try:
            w.requestInterruption()
        except Exception:  # noqa: BLE001  假 worker/无中断语义的线程
            pass
    return all(w.wait(timeout_ms) for w in alive)
