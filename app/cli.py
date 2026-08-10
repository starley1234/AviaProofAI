"""CLI AviaProofAI.

Примеры:
    python -m app.cli init-db
    python -m app.cli sync --spec SPEC-BRAKE-001
    python -m app.cli audit
    python -m app.cli conflicts
    python -m app.cli traceability
    python -m app.cli dashboard
    python -m app.cli users add petrov petrov --write
    python -m app.cli write-on / write-off
    python -m app.cli serve
"""
from __future__ import annotations

import argparse
import json
import sys


def _db():
    from app.db import session_scope
    return session_scope()


def cmd_init_db(_):
    from app.db import init_db
    init_db()
    print("База данных инициализирована")


def cmd_sync(args):
    from app.config import get_settings
    from app.services.teamcenter.factory import build_tc_client
    from app.services.teamcenter.sync import TeamcenterSync
    s = get_settings()
    with _db() as db:
        client = build_tc_client(s)
        try:
            run = TeamcenterSync(client, db).run(args.spec or s.tc_spec_id)
        finally:
            client.close()
    print(f"sync #{run.id}: {run.status} {json.dumps(run.stats, ensure_ascii=False)}")
    if run.error:
        print(f"ОШИБКА: {run.error}", file=sys.stderr)
        sys.exit(1)


def cmd_audit(args):
    from app.api.services import analysis_service
    with _db() as db:
        print(json.dumps(analysis_service(db).run_quality_audit(), ensure_ascii=False))


def cmd_conflicts(args):
    from app.api.services import analysis_service
    with _db() as db:
        print(json.dumps(analysis_service(db).run_conflict_scan(), ensure_ascii=False))


def cmd_traceability(args):
    from app.services.traceability import TraceabilityService
    with _db() as db:
        print(json.dumps(TraceabilityService(db).run(), ensure_ascii=False, default=str))


def cmd_dashboard(args):
    from sqlalchemy import func, select
    from app.models import Requirement, SyncRun
    with _db() as db:
        by_status = dict(db.execute(select(Requirement.status, func.count())
                                    .group_by(Requirement.status)).all())
        last = db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(1)).first()
        print(json.dumps({"by_status": by_status,
                          "last_sync": {"id": last.id, "status": last.status,
                                        "stats": last.stats} if last else None},
                         ensure_ascii=False, default=str))


def cmd_users(args):
    from sqlalchemy import select
    from app.models import User
    with _db() as db:
        if args.action == "add":
            if db.scalar(select(User).where(User.tc_login == args.login)):
                print(f"Пользователь {args.login} уже есть")
                return
            db.add(User(tc_login=args.login, koseven_login=args.koseven or args.login,
                        full_name=args.name or "", role=args.role,
                        write_to_tc_allowed=args.write))
            print(f"Добавлен пользователь {args.login} (запись в TC: {'да' if args.write else 'нет'})")
        elif args.action == "list":
            for u in db.scalars(select(User).order_by(User.tc_login)):
                print(f"{u.tc_login:12} koseven={u.koseven_login:12} role={u.role:8} "
                      f"write={u.write_to_tc_allowed}")
        elif args.action == "write":
            from app.services.settings_service import SettingsService
            SettingsService(db).set_user_write(args.login, args.value)
            print(f"{args.login}: запись в TC = {args.value}")


def cmd_write(args):
    from app.services.settings_service import SettingsService
    with _db() as db:
        SettingsService(db).set_write_allowed_service_wide(args.on, actor="cli")
        state = SettingsService(db).write_access_state()
    print(json.dumps(state, ensure_ascii=False))


def cmd_serve(args):
    import uvicorn
    from app.config import get_settings
    s = get_settings()
    uvicorn.run("app.main:app", host=args.host or s.api_host, port=args.port or s.api_port)


def main():
    p = argparse.ArgumentParser(prog="avia", description="AviaProofAI CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)

    ps = sub.add_parser("sync"); ps.add_argument("--spec", default=None); ps.set_defaults(fn=cmd_sync)
    sub.add_parser("audit").set_defaults(fn=cmd_audit)
    sub.add_parser("conflicts").set_defaults(fn=cmd_conflicts)
    sub.add_parser("traceability").set_defaults(fn=cmd_traceability)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)

    pu = sub.add_parser("users"); pu.add_argument("action", choices=["add", "list", "write"])
    pu.add_argument("login", nargs="?")
    pu.add_argument("--koseven"); pu.add_argument("--name"); pu.add_argument("--role", default="engineer")
    pu.add_argument("--write", action="store_true"); pu.add_argument("--value", type=lambda v: v == "true", default=None)
    pu.set_defaults(fn=cmd_users)

    pw = sub.add_parser("write-on"); pw.set_defaults(fn=cmd_write, on=True)
    pw = sub.add_parser("write-off"); pw.set_defaults(fn=cmd_write, on=False)

    psrv = sub.add_parser("serve"); psrv.add_argument("--host"); psrv.add_argument("--port", type=int)
    psrv.set_defaults(fn=cmd_serve)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
