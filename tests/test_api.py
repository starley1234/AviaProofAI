"""Тесты REST API: полный цикл «синхронизация -> анализ -> правка -> миграция»."""
from __future__ import annotations

from sqlalchemy import select

from app.models import Requirement


def test_health(db, api):
    r = api.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_api_key_required(db, api_client):
    r = api_client.get("/api/v1/requirements")
    assert r.status_code == 401


def test_sync_endpoint(seeded):
    runs = seeded.get("/api/v1/sync/runs").json()
    assert runs and runs[0]["status"] == "ok"
    assert runs[0]["stats"]["created"] == 16


def test_dashboard_summary(seeded):
    d = seeded.get("/api/v1/dashboard/summary").json()
    assert d["total"] == 16
    assert set(d["by_status"]) <= {"ok", "weak", "conflict", "needs_edit", "cert_risk"}
    assert d["write_access"]["service_wide_allowed"] is False  # безопасный дефолт
    assert d["last_sync"]["status"] == "ok"


def test_analysis_pipeline(seeded):
    a = seeded.post("/api/v1/analysis/audit").json()
    assert a["checked"] == 11 and a["issues"] >= 3
    c = seeded.post("/api/v1/analysis/conflicts").json()
    assert c["conflicts_found"] == 1
    t = seeded.post("/api/v1/analysis/traceability").json()
    assert t["flagged"] == 3

    # статусы на дашборде
    d = seeded.get("/api/v1/dashboard/summary").json()
    assert d["by_status"]["conflict"] == 1
    assert d["by_status"]["weak"] == 2
    assert d["by_status"]["cert_risk"] == 3


def test_requirement_detail(seeded):
    r = seeded.get("/api/v1/requirements/rev-REQ-1201-A")
    assert r.status_code == 200
    body = r.json()
    assert body["item_id"] == "REQ-1201"
    assert body["section_path"] == "2.1"
    assert body["parent"]["item_id"] == "SS-1200"
    assert "не более 100 Н" in body["text"]
    assert body["raw_data"]["owning_user"] == "petrov"
    assert body["traceability_links"]["in"] == ["rev-REQ-1101-A"]


def test_requirement_filters(seeded):
    seeded.post("/api/v1/analysis/traceability")
    flagged = seeded.get("/api/v1/requirements?status=cert_risk").json()
    assert {f["item_id"] for f in flagged} == {"REQ-1103", "REQ-1301", "REQ-1401"}
    sec2 = seeded.get("/api/v1/requirements?section=2").json()
    assert {f["item_id"] for f in sec2} == {"REQ-1201", "REQ-1203", "REQ-1204"}
    found = seeded.get("/api/v1/requirements?search=усилие").json()
    assert {f["item_id"] for f in found} == {"REQ-1201", "REQ-1301"}


def test_traceability_endpoint(seeded):
    r = seeded.get("/api/v1/requirements/rev-REQ-1101-A/traceability")
    body = r.json()
    assert [u["item_id"] for u in body["down"]] == ["REQ-1201", "REQ-1302", "REQ-1402"]
    leaf = seeded.get("/api/v1/requirements/rev-REQ-1401-A/traceability").json()
    assert leaf["up"] == [] and leaf["down"] == [] and leaf["children"] == []


def test_full_twin_workflow(seeded):
    """ИИ нашёл проблему -> конструктор подтвердил/правил -> правка ушла в TC."""
    # 1) синхронизация + аудит
    seeded.post("/api/v1/analysis/audit")
    uid = "rev-REQ-1101-A"

    # 2) ИИ предлагает правку
    r = seeded.post(f"/api/v1/requirements/{uid}/drafts", json={"source": "ai"},
                    headers=seeded.as_user("petrov"))
    assert r.status_code == 200
    draft = r.json()
    assert draft["source"] == "ai" and draft["status"] == "proposed"
    assert draft["version"] == 1
    assert "10 000" in draft["proposed_text"]  # конкретный критерий вместо «достаточно надёжной»
    assert draft["current_text"] == draft["proposed_text"]

    # 3) конструктор правит формулировку
    fixed = draft["current_text"].replace("10 000 ч", "5 000 ч")
    r = seeded.patch(f"/api/v1/drafts/{draft['id']}",
                     json={"text": fixed, "rationale": "Уточнён ресурс по данным эксплуатации"},
                     headers=seeded.as_user("petrov"))
    assert r.status_code == 200 and r.json()["status"] == "reviewed"

    # 4) состояние готовности
    r = seeded.post(f"/api/v1/drafts/{draft['id']}/submit", headers=seeded.as_user("petrov"))
    assert r.json()["status"] == "ready"

    # 5) push в TC (права: сервис включён ниже; сначала проверим, что без прав нельзя)
    from stub_tc.server import store
    before = store.revisions[uid]["object_string"]
    r = seeded.post(f"/api/v1/drafts/{draft['id']}/push", headers=seeded.as_user("petrov"))
    assert r.status_code == 403  # глобальный выключатель записи выключен

    seeded.put("/api/v1/settings/write-access", json={"enabled": True},
               headers=seeded.as_user("infodba"))
    r = seeded.post(f"/api/v1/drafts/{draft['id']}/push", headers=seeded.as_user("petrov"))
    assert r.status_code == 200 and r.json()["status"] == "pushed"
    assert r.json()["pushed_at"] is not None

    # 6) текст реально обновился в «Teamcenter»
    after = store.revisions[uid]["object_string"]
    assert after != before and "5 000 ч" in after

    # 7) локальная копия обновилась, история сохранена
    req = seeded.get(f"/api/v1/requirements/{uid}").json()
    assert "5 000 ч" in req["text"]
    assert req["drafts"][0]["status"] == "pushed"


def test_user_draft_manual(seeded):
    r = seeded.post("/api/v1/requirements/rev-REQ-1401-A/drafts",
                    json={"source": "user", "text": "Сигнализация об отказе канала — световая и звуковая.",
                          "rationale": "Уточнение по протоколу ПСИ"},
                    headers=seeded.as_user("petrov"))
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "user" and d["status"] == "draft"
    # требование получило статус needs_edit
    req = seeded.get("/api/v1/requirements/rev-REQ-1401-A").json()
    assert req["status"] == "needs_edit"


def test_draft_reject(seeded):
    r = seeded.post("/api/v1/requirements/rev-REQ-1103-A/drafts", json={"source": "ai"},
                    headers=seeded.as_user("petrov"))
    did = r.json()["id"]
    r = seeded.post(f"/api/v1/drafts/{did}/reject", headers=seeded.as_user("petrov"))
    assert r.json()["status"] == "rejected"


def test_push_requires_ready_state(seeded):
    r = seeded.post("/api/v1/requirements/rev-REQ-1103-A/drafts", json={"source": "ai"},
                    headers=seeded.as_user("petrov"))
    did = r.json()["id"]
    r = seeded.post(f"/api/v1/drafts/{did}/push", headers=seeded.as_user("petrov"))
    assert r.status_code == 409  # не в состоянии ready


def test_unknown_requirement_404(seeded):
    assert seeded.get("/api/v1/requirements/rev-NOPE-A").status_code == 404


def test_audit_closes_stale_findings_after_fix(seeded):
    """Полный цикл: слабое место -> правка ИИ -> push -> ре-аудит -> статус ok."""
    seeded.post("/api/v1/analysis/audit")
    uid = "rev-REQ-1101-A"
    assert seeded.get(f"/api/v1/requirements/{uid}").json()["status"] == "weak"

    # ИИ предлагает правку, конструктор отправляет её в TC
    r = seeded.post(f"/api/v1/requirements/{uid}/drafts", json={"source": "ai"},
                    headers=seeded.as_user("petrov"))
    did = r.json()["id"]
    seeded.post(f"/api/v1/drafts/{did}/submit", headers=seeded.as_user("petrov"))
    seeded.put("/api/v1/settings/write-access", json={"enabled": True},
               headers=seeded.as_user("infodba"))
    seeded.post(f"/api/v1/drafts/{did}/push", headers=seeded.as_user("petrov"))

    # повторная синхронизация подтягивает новый текст, аудит закрывает старое замечание
    seeded.post("/api/v1/sync/run", json={"spec_id": "SPEC-BRAKE-001", "wait": True})
    seeded.post("/api/v1/analysis/audit")
    req = seeded.get(f"/api/v1/requirements/{uid}").json()
    assert req["status"] == "ok"
    assert "10 000" in req["text"]
