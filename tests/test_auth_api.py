import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from api.main import app
from eltool import userdb
from eltool import users as users_cli
from eltool.auth import COOKIE_NAME, clear_login_cookie, set_login_cookie


@pytest.fixture
def client():
    return TestClient(app)


def test_signed_out_requests_are_401(auth_env, client):
    assert client.get("/api/sessions").status_code == 401
    assert client.post("/api/sessions/parse-file").status_code == 401
    assert client.get("/api/llm/default-prompt").status_code == 401
    assert client.get("/api/feedback/options").status_code == 401
    assert client.get("/api/sessions").json() == {"detail": "Not signed in"}


def test_non_api_paths_pass_the_gate(auth_env, client):
    # Not an /api/ path: the gate must not answer 401 (the static app is public).
    assert client.get("/some-page").status_code != 401


def test_api_lookalike_prefix_is_not_gated(auth_env, client):
    # "/apix" is not under "/api/": the gate must not treat it as protected.
    assert client.get("/apix").status_code != 401
    assert client.get("/apix/sessions").status_code != 401
    assert client.get("/api/anything-else").status_code == 401


def test_info_is_public_and_hides_secrets(auth_env, client, monkeypatch):
    monkeypatch.setenv("HELIX_FIREBASE_API_KEY", "key-1")
    monkeypatch.setenv("HELIX_FIREBASE_AUTH_DOMAIN", "x.firebaseapp.com")
    monkeypatch.setenv("HELIX_FIREBASE_PROJECT_ID", "proj-1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-do-not-leak")
    r = client.get("/api/info")
    assert r.status_code == 200
    body = r.json()
    assert body["auth"] is True
    assert body["firebase"] == {"apiKey": "key-1", "authDomain": "x.firebaseapp.com",
                                "projectId": "proj-1"}
    assert "secret-do-not-leak" not in r.text


def test_info_firebase_block_needs_all_three_vars(auth_env, client, monkeypatch):
    monkeypatch.setenv("HELIX_FIREBASE_API_KEY", "key-1")
    monkeypatch.delenv("HELIX_FIREBASE_AUTH_DOMAIN", raising=False)
    monkeypatch.delenv("HELIX_FIREBASE_PROJECT_ID", raising=False)
    assert client.get("/api/info").json()["firebase"] is None


def test_me_returns_signed_in_user(make_user, login_as, client):
    ann = make_user("Ann@x.org", "reviewer", "Ann")
    login_as(client, ann)
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() == {"id": ann.id, "email": "ann@x.org", "name": "Ann", "role": "reviewer"}
    assert client.get("/api/sessions").status_code == 200


def test_me_without_cookie_is_401(auth_env, client):
    assert client.get("/api/auth/me").status_code == 401


def test_unknown_cookie_is_401(auth_env, client):
    client.cookies.set(COOKIE_NAME, "not-a-real-token")
    assert client.get("/api/auth/me").status_code == 401


def test_deactivated_user_cookie_stops_working(make_user, login_as, client):
    ann = make_user("a@x.org")
    login_as(client, ann)
    assert client.get("/api/auth/me").status_code == 200
    userdb.set_active(ann.id, False)
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_is_401(make_user, login_as, client):
    ann = make_user("a@x.org")
    login_as(client, ann)
    assert client.get("/api/auth/me").status_code == 200
    conn = userdb.connect()
    with conn:
        conn.execute("UPDATE auth_sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
    conn.close()
    assert client.get("/api/auth/me").status_code == 401


def test_only_the_hash_is_stored(make_user):
    ann = make_user("a@x.org")
    token = userdb.create_auth_session(ann.id)
    conn = userdb.connect()
    rows = [r["token_hash"] for r in conn.execute("SELECT token_hash FROM auth_sessions")]
    conn.close()
    assert rows == [userdb.token_hash(token)]
    assert userdb.user_for_token(token).id == ann.id
    assert userdb.user_for_token(userdb.token_hash(token)) is None


def test_logout_invalidates_cookie(make_user, login_as, client):
    ann = make_user("a@x.org")
    login_as(client, ann)
    token = client.cookies.get(COOKIE_NAME)
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert COOKIE_NAME in r.headers["set-cookie"]
    # Even a client that kept the old cookie is signed out: the row is gone.
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/api/auth/me").status_code == 401
    assert userdb.user_for_token(token) is None


def test_logout_with_auth_off_leaves_the_db_alone(tmp_path, monkeypatch, client):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr("eltool.session.SESSIONS_DIR", tmp_path)
    client.cookies.set(COOKIE_NAME, "stale-cookie")
    assert client.post("/api/auth/logout").status_code == 200
    assert not (tmp_path / "helix.db").exists()


def test_creating_a_session_prunes_expired_ones(make_user):
    ann = make_user("a@x.org")
    old = userdb.create_auth_session(ann.id)
    conn = userdb.connect()
    with conn:
        conn.execute("UPDATE auth_sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
    conn.close()
    fresh = userdb.create_auth_session(ann.id)
    conn = userdb.connect()
    rows = [r["token_hash"] for r in conn.execute("SELECT token_hash FROM auth_sessions")]
    conn.close()
    assert rows == [userdb.token_hash(fresh)]
    assert userdb.user_for_token(old) is None


def test_delete_user_auth_sessions_signs_out_every_device(make_user):
    ann, bob = make_user("a@x.org"), make_user("b@x.org")
    t1, t2 = userdb.create_auth_session(ann.id), userdb.create_auth_session(ann.id)
    tb = userdb.create_auth_session(bob.id)
    userdb.delete_user_auth_sessions(ann.id)
    assert userdb.user_for_token(t1) is None and userdb.user_for_token(t2) is None
    assert userdb.user_for_token(tb).id == bob.id


def test_cookie_is_httponly_samesite_strict(monkeypatch):
    monkeypatch.delenv("HELIX_HOSTED", raising=False)
    resp = Response()
    set_login_cookie(resp, "tok")
    header = resp.headers["set-cookie"].lower()
    assert f"{COOKIE_NAME}=tok" in header
    assert "httponly" in header
    assert "samesite=strict" in header
    assert "max-age=1209600" in header
    assert "secure" not in header

    monkeypatch.setenv("HELIX_HOSTED", "1")
    resp = Response()
    set_login_cookie(resp, "tok")
    assert "secure" in resp.headers["set-cookie"].lower()

    resp = Response()
    clear_login_cookie(resp)
    assert "max-age=0" in resp.headers["set-cookie"].lower()


def test_auth_off_changes_nothing(tmp_path, monkeypatch, client):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr("eltool.session.SESSIONS_DIR", tmp_path)
    assert client.get("/api/sessions").status_code == 200
    info = client.get("/api/info").json()
    assert info["auth"] is False
    assert info["firebase"] is None
    assert not (tmp_path / "helix.db").exists()


def test_cli_create_admin_and_duplicate_refused(auth_env, capsys):
    assert users_cli.main(["create-admin", "--email", "Boss@x.org", "--name", "The Boss"]) == 0
    u = userdb.get_user_by_email("boss@x.org")
    assert (u.role, u.name, u.active) == ("admin", "The Boss", True)
    conn = userdb.connect()
    assert conn.execute("SELECT firebase_uid FROM users").fetchone()[0] is None
    conn.close()
    assert users_cli.main(["create-admin", "--email", "boss@X.org"]) == 1
    assert "already" in capsys.readouterr().err.lower()
    assert len(userdb.list_users()) == 1


def test_cli_name_defaults_to_email_local_part(auth_env):
    assert users_cli.main(["create-admin", "--email", "jo@x.org"]) == 0
    assert userdb.get_user_by_email("jo@x.org").name == "jo"
