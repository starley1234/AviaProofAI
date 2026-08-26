#!/usr/bin/env python3
"""Embedded PostgreSQL для демо (без установленного сервера PG).

Запуск:  python scripts/run_embedded_pg.py [--dir ~/avia_pgdata]
Держит процесс живым; в DATABASE_URL подставляйте socket-путь из вывода.
"""
import argparse
import os
import sys
import time

import pgserver


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.expanduser("~/avia_pgdata"))
    args = ap.parse_args()
    os.makedirs(args.dir, exist_ok=True)
    db = pgserver.get_server(args.dir)
    uri = db.get_uri()
    print(f"PostgreSQL готов: {uri}", flush=True)
    print("DATABASE_URL=" + uri.replace("postgresql://", "postgresql+psycopg2://"), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
