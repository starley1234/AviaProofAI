"""Тесты Guardrails: Traceability Audit (риск сертификации) и Impact Analysis."""
from __future__ import annotations

from sqlalchemy import select

from app.models import Analysis, Requirement
from app.services.impact import ImpactService
from app.services.traceability import TraceabilityService


def test_traceability_audit_flags_unlinked(seeded):
    """REQ-1103, REQ-1301, REQ-1401 не имеют связей -> «Риск сертификации»."""
    res = seeded.post("/api/v1/analysis/traceability")
    assert res.status_code == 200
    body = res.json()
    assert body["checked"] == 11 and body["flagged"] == 3
    flagged = {u.removeprefix("rev-").rsplit("-", 1)[0] for u in body["flagged_uids"]}
    assert flagged == {"REQ-1103", "REQ-1301", "REQ-1401"}

    rows = seeded.get("/api/v1/analysis/results?kind=traceability").json()
    assert all(r["severity"] == "critical" for r in rows)
    assert "Риск сертификации" in rows[0]["title"]


def test_cert_risk_status_on_dashboard(seeded):
    seeded.post("/api/v1/analysis/traceability")
    d = seeded.get("/api/v1/dashboard/summary").json()
    assert d["by_status"].get("cert_risk") == 3


def test_impact_analysis_lists_related(seeded):
    """REQ-1101 -> связанные по TC_Requirement_Trace_Relation: 1201, 1302, 1402."""
    uid = "rev-REQ-1101-A"
    res = seeded.post(f"/api/v1/requirements/{uid}/impact",
                      json={"new_text": "Система торможения должна обеспечивать торможение при разбеге."})
    assert res.status_code == 200
    body = res.json()
    assert body["related"] == 3
    # связанные отражены в analyses (kind=impact)
    rows = seeded.get(f"/api/v1/analysis/results?kind=impact&requirement_uid={uid}").json()
    related_uids = {r["other_requirement_uid"] for r in rows}
    assert related_uids == {"rev-REQ-1201-A", "rev-REQ-1302-A", "rev-REQ-1402-A"}


def test_impact_children_by_hierarchy(db):
    """Прямые дети по дереву (parent_uid) тоже считаются связанными."""
    from app.services.teamcenter.client import TeamcenterSoapClient
    from app.services.teamcenter.sync import TeamcenterSync
    from app.config import get_settings
    s = get_settings()
    c = TeamcenterSoapClient(s.tc_url, timeout=10)
    try:
        TeamcenterSync(c, db).run("SPEC-BRAKE-001")
    finally:
        c.close()
    # раздел SS-1200: дети по иерархии = REQ-1201, REQ-1203, REQ-1204
    svc = ImpactService(db)
    related = svc.related_requirements("rev-SS-1200-A")
    assert {r.item_id for r in related} == {"REQ-1201", "REQ-1203", "REQ-1204"}


def test_resolve_analysis(seeded):
    seeded.post("/api/v1/analysis/traceability")
    rows = seeded.get("/api/v1/analysis/results?kind=traceability&status=open").json()
    aid = rows[0]["id"]
    r = seeded.post(f"/api/v1/analysis/results/{aid}/resolve", json={"resolution": "dismissed"})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    # после закрытия флага требование больше не cert_risk
    d = seeded.get("/api/v1/dashboard/summary").json()
    assert d["by_status"].get("cert_risk") == 2
