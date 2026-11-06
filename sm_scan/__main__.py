"""CLI 入口:python -m sm_scan scan|gui|report|task(spec §11)。"""

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler


def setup_logging() -> None:
    from . import config

    handler = RotatingFileHandler(
        config.data_dir() / "scan.log", maxBytes=1_000_000,
        backupCount=3, encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[handler],
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sm-scan")
    sub = parser.add_subparsers(dest="cmd")

    sc = sub.add_parser("scan", help="run all tracks")
    sc.add_argument("--days", type=int, default=None)
    sc.add_argument("--llm", action="store_true")
    sc.add_argument("--dry-run", action="store_true")

    rp = sub.add_parser("report", help="regenerate a daily report")
    rp.add_argument("--date", default=None, help="YYYY-MM-DD")

    tk = sub.add_parser("task", help="manage scheduled task")
    tk.add_argument("action", choices=["register", "remove", "status"])

    args = parser.parse_args(argv)
    if args.cmd == "scan":
        setup_logging()
        from datetime import date as _date

        from .engine import run_scan

        rc = run_scan(dry_run=args.dry_run, llm=args.llm, days=args.days)
        if not args.dry_run and rc in (0, 2):
            from . import report as _report, store as _store
            from .engine import last_report_date

            conn = _store.connect()
            _store.init_db(conn)
            path = _report.generate(conn, last_report_date(conn))
            print(f"report: {path}")
        return rc

    if args.cmd == "report":
        from datetime import date as _date

        from . import report as _report, store as _store

        conn = _store.connect()
        _store.init_db(conn)
        d = (_date.fromisoformat(args.date)
             if args.date else _date.today())
        path = _report.generate(conn, d)
        print(path)
        return 0

    if args.cmd == "task":
        from . import task as _task

        if args.action == "register":
            ok, msg = _task.register()
        elif args.action == "remove":
            ok, msg = _task.remove()
        else:
            info = _task.status()
            print(("已注册" if info["registered"] else "未注册"), info["raw"])
            return 0
        print(("OK: " if ok else "FAIL: ") + msg)
        return 0 if ok else 1

    if args.cmd == "gui":
        from .gui.app import main as gui_main

        return gui_main()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
