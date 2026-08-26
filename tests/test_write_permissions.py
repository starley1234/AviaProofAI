"""Тесты прав записи в Teamcenter: сервис в целом + пользователь."""
from __future__ import annotations


def test_default_write_access_state(seeded):
    r = seeded.get("/api/v1/settings/write-access", headers=seeded.as_user("petrov"))
    body = r.json()
    assert body["service_wide_allowed"] is False
    assert body["per_user_allowed"] is True   # у петрова право есть
    assert body["effective"] is False         # но сервис запрещает


def test_service_wide_toggle_admin_only(seeded):
    # не-admin не может включить
    r = seeded.put("/api/v1/settings/write-access", json={"enabled": True},
                   headers=seeded.as_user("petrov"))
    assert r.status_code == 403
    # admin может
    r = seeded.put("/api/v1/settings/write-access", json={"enabled": True},
                   headers=seeded.as_user("infodba"))
    assert r.status_code == 200 and r.json()["service_wide_allowed"] is True
    # выключить обратно
    r = seeded.put("/api/v1/settings/write-access", json={"enabled": False},
                   headers=seeded.as_user("infodba"))
    assert r.json()["service_wide_allowed"] is False


def test_per_user_write_flag(seeded):
    # у сидорова нет права записи
    r = seeded.get("/api/v1/settings/write-access", headers=seeded.as_user("sidorov"))
    assert r.json()["per_user_allowed"] is False
    # admin выдаёт право
    users = seeded.get("/api/v1/users").json()
    sidorov = next(u for u in users if u["tc_login"] == "sidorov")
    r = seeded.patch(f"/api/v1/users/{sidorov['id']}", json={"write_to_tc_allowed": True})
    assert r.json()["write_to_tc_allowed"] is True


def test_push_blocked_when_service_write_off(seeded):
    uid = "rev-REQ-1102-A"
    r = seeded.post(f"/api/v1/requirements/{uid}/drafts", json={"source": "ai"},
                    headers=seeded.as_user("petrov"))
    did = r.json()["id"]
    seeded.post(f"/api/v1/drafts/{did}/submit", headers=seeded.as_user("petrov"))
    r = seeded.post(f"/api/v1/drafts/{did}/push", headers=seeded.as_user("petrov"))
    assert r.status_code == 403
    assert "запрещена" in r.json()["detail"].lower()


def test_push_blocked_for_user_without_right(seeded):
    seeded.put("/api/v1/settings/write-access", json={"enabled": True},
               headers=seeded.as_user("infodba"))
    uid = "rev-REQ-1102-A"
    r = seeded.post(f"/api/v1/requirements/{uid}/drafts", json={"source": "ai"},
                    headers=seeded.as_user("sidorov"))
    did = r.json()["id"]
    seeded.post(f"/api/v1/drafts/{did}/submit", headers=seeded.as_user("sidorov"))
    r = seeded.post(f"/api/v1/drafts/{did}/push", headers=seeded.as_user("sidorov"))
    assert r.status_code == 403


def test_push_success_updates_teamcenter(seeded):
    from stub_tc.server import store
    seeded.put("/api/v1/settings/write-access", json={"enabled": True},
               headers=seeded.as_user("infodba"))
    uid = "rev-REQ-1303-A"
    r = seeded.post(f"/api/v1/requirements/{uid}/drafts",
                    json={"source": "user", "text": "Время растормаживания — не более 1,5 с."},
                    headers=seeded.as_user("petrov"))
    did = r.json()["id"]
    seeded.post(f"/api/v1/drafts/{did}/submit", headers=seeded.as_user("petrov"))
    r = seeded.post(f"/api/v1/drafts/{did}/push", headers=seeded.as_user("petrov"))
    assert r.status_code == 200
    assert "1,5 с" in store.revisions[uid]["object_string"]


def test_users_crud(seeded):
    r = seeded.post("/api/v1/users", json={"tc_login": "novikov", "koseven_login": "novikov"})
    assert r.status_code == 200 and r.json()["write_to_tc_allowed"] is False
    # дубликат запрещён
    assert seeded.post("/api/v1/users", json={"tc_login": "novikov",
                                              "koseven_login": "novikov"}).status_code == 409
    users = seeded.get("/api/v1/users").json()
    assert {u["tc_login"] for u in users} >= {"petrov", "sidorov", "infodba", "novikov"}


def test_unknown_user_rejected(seeded):
    r = seeded.post("/api/v1/requirements/rev-REQ-1103-A/drafts", json={"source": "ai"},
                    headers=seeded.as_user("unknown-user"))
    assert r.status_code == 403
