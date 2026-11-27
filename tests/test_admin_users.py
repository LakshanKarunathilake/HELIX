import pytest
from fastapi.testclient import TestClient

from api.main import app
from eltool import userdb


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def make_session(auth_env):
    def _make(name):
        d = auth_env / name
        d.mkdir()
        (d / "session.json").write_text("{}")
    return _make


@pytest.fixture
def admin_client(client, make_user, login_as):
    admin = make_user("root@x.org", "admin")
    login_as(client, admin)
    return client, admin


def test_admin_lists_users_with_sessions(admin_client, make_user, make_session):
    client, admin = admin_client
    ann = make_user("ann@x.org", "annotator")
    make_session("s1")
    make_session("s2")
    userdb.add_member("s1", ann.id)
    userdb.add_member("s2", ann.id)
    rows = {u["email"]: u for u in client.get("/api/admin/users").json()}
    assert rows["ann@x.org"] == {"id": ann.id, "email": "ann@x.org", "name": "ann",
                                 "role": "annotator", "active": True,
                                 "session_names": ["s1", "s2"]}
    assert rows["root@x.org"]["session_names"] == []


def test_change_role_and_deactivate(admin_client, make_user):
    client, _ = admin_client
    ann = make_user("ann@x.org")
    r = client.patch(f"/api/admin/users/{ann.id}", json={"role": "reviewer"})
    assert r.status_code == 200
    assert r.json()["role"] == "reviewer"
    r = client.patch(f"/api/admin/users/{ann.id}", json={"active": False})
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert userdb.get_user(ann.id).active is False
    assert userdb.get_user(ann.id).role == "reviewer"


def test_patch_rejects_bad_role_and_unknown_user(admin_client, make_user):
    client, _ = admin_client
    ann = make_user("ann@x.org")
    assert client.patch(f"/api/admin/users/{ann.id}", json={"role": "king"}).status_code in (400, 422)
    assert userdb.get_user(ann.id).role == "annotator"
    assert client.patch("/api/admin/users/9999", json={"active": False}).status_code == 404


def test_last_admin_cannot_demote_or_deactivate_self(admin_client):
    client, admin = admin_client
    r = client.patch(f"/api/admin/users/{admin.id}", json={"role": "reviewer"})
    assert r.status_code == 409
    assert r.json() == {"detail": "Cannot remove the last admin"}
    r = client.patch(f"/api/admin/users/{admin.id}", json={"active": False})
    assert r.status_code == 409
    assert r.json() == {"detail": "Cannot remove the last admin"}
    got = userdb.get_user(admin.id)
    assert got.role == "admin" and got.active is True


def test_second_admin_can_demote_first(admin_client, make_user):
    client, first = admin_client
    second = make_user("two@x.org", "admin")
    r = client.patch(f"/api/admin/users/{first.id}", json={"role": "reviewer"})
    assert r.status_code == 200
    assert userdb.get_user(first.id).role == "reviewer"
    assert userdb.count_active_admins() == 1
    assert userdb.get_user(second.id).role == "admin"


def test_editing_others_is_fine_with_one_admin(admin_client, make_user):
    client, _ = admin_client
    ann = make_user("ann@x.org")
    assert client.patch(f"/api/admin/users/{ann.id}", json={"active": False}).status_code == 200


def test_membership_replace_adds_and_removes(admin_client, make_user, make_session):
    client, _ = admin_client
    ann = make_user("ann@x.org")
    for n in ("s1", "s2", "s3"):
        make_session(n)
    r = client.put(f"/api/admin/users/{ann.id}/sessions", json={"session_names": ["s1", "s2"]})
    assert r.status_code == 200
    assert userdb.sessions_for(ann.id) == ["s1", "s2"]
    r = client.put(f"/api/admin/users/{ann.id}/sessions", json={"session_names": ["s2", "s3"]})
    assert r.status_code == 200
    assert r.json()["session_names"] == ["s2", "s3"]
    assert userdb.sessions_for(ann.id) == ["s2", "s3"]
    assert userdb.is_member("s3", ann.id) and not userdb.is_member("s1", ann.id)
    assert [u.id for u in userdb.members("s2")] == [ann.id]


def test_unknown_session_name_is_400(admin_client, make_user, make_session):
    client, _ = admin_client
    ann = make_user("ann@x.org")
    make_session("s1")
    userdb.add_member("s1", ann.id)
    r = client.put(f"/api/admin/users/{ann.id}/sessions", json={"session_names": ["s1", "nope"]})
    assert r.status_code == 400
    assert userdb.sessions_for(ann.id) == ["s1"]  # unchanged
    assert client.put("/api/admin/users/9999/sessions", json={"session_names": []}).status_code == 404


def test_deactivating_user_deletes_their_login_sessions(admin_client, make_user):
    client, _ = admin_client
    ann = make_user("ann@x.org")
    token = userdb.create_auth_session(ann.id)
    assert userdb.user_for_token(token) is not None
    client.patch(f"/api/admin/users/{ann.id}", json={"active": False})
    assert userdb.user_for_token(token) is None


def test_annotator_cannot_call_admin_users(client, make_user, login_as):
    ann = make_user("ann@x.org")
    login_as(client, ann)
    assert client.get("/api/admin/users").status_code == 403
    assert client.patch(f"/api/admin/users/{ann.id}", json={"role": "admin"}).status_code == 403
    assert client.put(f"/api/admin/users/{ann.id}/sessions", json={"session_names": []}).status_code == 403
    assert userdb.get_user(ann.id).role == "annotator"


def test_admin_users_404_when_auth_off(client, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    assert client.get("/api/admin/users").status_code == 404
