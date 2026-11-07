# ScanWorker 兜底(P2-6)+ task.status 容错(P2-7)
p = "sm_scan/gui/scan_view.py"
s = open(p, encoding="utf-8").read()
old = '''    def run(self):
        handler = _QtLogHandler(self.log_line.emit)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            rc = engine.run_scan()
            try:
                conn = store.connect()
                report.generate(conn, engine.last_report_date(conn))
            except Exception:
                self.log_line.emit("日报生成失败(可稍后 sm_scan report 重试)")
        finally:
            root.removeHandler(handler)
        self.finished_code.emit(rc)'''
new = '''    def run(self):
        handler = _QtLogHandler(self.log_line.emit)
        root = logging.getLogger()
        root.addHandler(handler)
        rc = 3
        try:
            rc = engine.run_scan()
            try:
                conn = store.connect()
                report.generate(conn, engine.last_report_date(conn))
            except Exception:
                self.log_line.emit("日报生成失败(可稍后 sm_scan report 重试)")
        except Exception as e:  # P2-6:非 SourceError 也必须收尾,防按钮永久禁用
            self.log_line.emit(f"扫描异常终止:{e}")
        finally:
            root.removeHandler(handler)
        self.finished_code.emit(rc)'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("scan_view ok")

p = "sm_scan/task.py"
s = open(p, encoding="utf-8").read()
old = '''    r = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True, text=True, errors="replace",
    )
    return {"registered": r.returncode == 0,
            "raw": (r.stdout or r.stderr or "")[:500]}'''
new = '''    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True, text=True, errors="replace",
        )
    except (OSError, PermissionError) as e:  # P2-7:组策略/权限拦截不得炸 GUI
        return {"registered": False, "raw": f"查询失败:{e}"}
    return {"registered": r.returncode == 0,
            "raw": (r.stdout or r.stderr or "")[:500]}'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("task ok")

# --days 生效(P2-5):给出 days 时窗口起点取更早者
p = "sm_scan/engine.py"
s = open(p, encoding="utf-8").read()
old = '''    window = scan_window(
        last_ok_finished(conn), date.today(),
        backfill_days=int(days or settings.get("backfill_days", "30")),
    )'''
new = '''    window = scan_window(
        last_ok_finished(conn), date.today(),
        backfill_days=int(days or settings.get("backfill_days", "30")),
    )
    if days:  # P2-5:显式 --days 时扩大窗口(起点取更早者)
        forced = date.today() - timedelta(days=days)
        window = (min(window[0], forced), window[1])'''
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("engine ok")
