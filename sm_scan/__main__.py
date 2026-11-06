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

    args = parser.parse_args(argv)
    if args.cmd == "scan":
        setup_logging()
        from .engine import run_scan

        return run_scan(dry_run=args.dry_run, llm=args.llm, days=args.days)

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

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
