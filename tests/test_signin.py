import pytest
from fastapi.testclient import TestClient

from api.main import app
from eltool import idtoken, invites, userdb
from eltool.auth import COOKIE_NAME

REFUSAL = {"detail": "This Google account is not authorised for HELIX"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin(make_user):
    return make_user("boss@x.org", "admin", "Boss")


@pytest.fixture
def two_sessions(auth_env):
    for name in ("alpha", "beta"):
        (auth_env / name).mkdir()
        (auth_env / name / "session.json").write_text("{}")
    return ["alpha", "beta"]


def claims(email="new@x.org", uid="uid-1", verified=True, provider="google.com", name="New Person"):
    return {"uid": uid, "email": email, "email_verified": verified,
            "name": name, "provider": provider}


@pytest.fixture
def sign_in(client, monkeypatch):
    """POST /api/auth/session with the verifier stubbed to return ``claims``."""
    def _sign_in(c=None, error=None):
        def fake(token):
            if error:
                raise error
            return c
        monkeypatch.setattr(idtoken, "verify_id_token", fake)
        return client.post("/api/auth/session", json={"id_token": "t"})
    return _sign_in


def members(user_id):
    conn = userdb.connect()
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT session_name FROM memberships WHERE user_id = ?", (user_id,)))
    finally:
        conn.close()


def test_invited_account_signs_in_with_role_and_memberships(admin, two_sessions, sign_in, client):
    invites.create_invite("New@x.org", "reviewer", two_sessions, admin.id)
    r = sign_in(claims())
    assert r.status_code == 200
    u = userdb.get_user_by_email("new@x.org")
    assert r.json() == {"id": u.id, "email": "new@x.org", "name": "New Person", "role": "reviewer"}
    assert members(u.id) == ["alpha", "beta"]
    assert userdb.get_user_by_uid("uid-1").id == u.id
    # The cookie works for the gate.
    assert COOKIE_NAME in r.headers["set-cookie"]
    assert client.get("/api/auth/me").json()["email"] == "new@x.org"


def test_name_falls_back_to_email_local_part(admin, sign_in):
    invites.create_invite("jo@x.org", "annotator", [], admin.id)
    assert sign_in(claims("jo@x.org", name="")).json()["name"] == "jo"


def test_uninvited_account_is_refused(admin, sign_in):
    r = sign_in(claims("stranger@x.org"))
    assert (r.status_code, r.json()) == (403, REFUSAL)
    assert userdb.get_user_by_email("stranger@x.org") is None
    assert COOKIE_NAME not in r.headers.get("set-cookie", "")


def test_unverified_email_is_refused(admin, sign_in):
    invites.create_invite("new@x.org", "annotator", [], admin.id)
    r = sign_in(claims(verified=False))
    assert (r.status_code, r.json()) == (403, REFUSAL)
    assert userdb.get_user_by_email("new@x.org") is None


def test_non_google_provider_is_refused(admin, sign_in):
    invites.create_invite("new@x.org", "annotator", [], admin.id)
    r = sign_in(claims(provider="password"))
    assert (r.status_code, r.json()) == (403, REFUSAL)


def test_token_from_wrong_project_is_refused(admin, sign_in):
    invites.create_invite("new@x.org", "annotator", [], admin.id)
    r = sign_in(error=idtoken.IdTokenError("wrong audience"))
    assert (r.status_code, r.json()) == (403, REFUSAL)
    assert userdb.get_user_by_email("new@x.org") is None


def test_revoked_invite_is_refused(admin, sign_in):
    inv, _ = invites.create_invite("new@x.org", "annotator", [], admin.id)
    invites.revoke(inv.id)
    r = sign_in(claims())
    assert (r.status_code, r.json()) == (403, REFUSAL)
    assert userdb.get_user_by_email("new@x.org") is None


def test_inactive_user_is_refused_with_same_message(admin, sign_in):
    invites.create_invite("new@x.org", "annotator", [], admin.id)
    assert sign_in(claims()).status_code == 200
    userdb.set_active(userdb.get_user_by_email("new@x.org").id, False)
    r = sign_in(claims())
    assert (r.status_code, r.json()) == (403, REFUSAL)


def test_all_refusals_have_identical_body(admin, sign_in):
    inv, _ = invites.create_invite("revoked@x.org", "annotator", [], admin.id)
    invites.revoke(inv.id)
    cases = [
        sign_in(claims("nobody@x.org")),
        sign_in(claims("revoked@x.org")),
        sign_in(claims(verified=False)),
        sign_in(claims(provider="facebook.com")),
        sign_in(error=idtoken.IdTokenError("bad")),
    ]
    assert {(r.status_code, r.text) for r in cases} == {(403, cases[0].text)}


def test_second_sign_in_matches_by_uid_after_email_change(admin, sign_in):
    invites.create_invite("old@x.org", "annotator", [], admin.id)
    first = sign_in(claims("old@x.org", uid="uid-7")).json()
    second = sign_in(claims("renamed@x.org", uid="uid-7"))
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    assert len(userdb.list_users()) == 2  # admin + the one invitee


def test_email_match_is_case_insensitive(admin, sign_in):
    invites.create_invite("mixed@x.org", "annotator", [], admin.id)
    assert sign_in(claims("MiXeD@X.org")).status_code == 200


def test_bootstrap_admin_links_on_first_sign_in(auth_env, sign_in):
    boss = userdb.create_user("boss@x.org", "Boss", "admin")
    r = sign_in(claims("Boss@x.org", uid="uid-boss", name="Google Boss"))
    assert r.status_code == 200
    assert r.json() == {"id": boss.id, "email": "boss@x.org", "name": "Boss", "role": "admin"}
    assert userdb.get_user_by_uid("uid-boss").id == boss.id


def test_registered_user_with_a_different_uid_is_refused(auth_env, sign_in):
    userdb.create_user("a@x.org", "A", "annotator", firebase_uid="uid-original")
    r = sign_in(claims("a@x.org", uid="uid-other"))
    assert (r.status_code, r.json()) == (403, REFUSAL)
    assert userdb.get_user_by_uid("uid-other") is None


def test_invite_for_registered_email_adds_memberships_immediately(admin, make_user, two_sessions):
    ann = make_user("ann@x.org", "annotator")
    inv, registered = invites.create_invite("ANN@x.org", "reviewer", ["beta"], admin.id)
    assert registered is True
    assert inv.status == "accepted"
    assert members(ann.id) == ["beta"]
    assert userdb.get_user(ann.id).role == "annotator"  # roles change on the Users page


def test_invite_is_marked_accepted_after_first_sign_in(admin, sign_in):
    inv, registered = invites.create_invite("new@x.org", "annotator", [], admin.id)
    assert registered is False and inv.status == "pending"
    assert [i.status for i in invites.list_invites()] == ["pending"]
    sign_in(claims())
    assert [i.status for i in invites.list_invites()] == ["accepted"]


def test_revoke_only_applies_to_pending(admin, sign_in):
    inv, _ = invites.create_invite("new@x.org", "annotator", [], admin.id)
    sign_in(claims())
    with pytest.raises(ValueError):
        invites.revoke(inv.id)
    assert invites.list_invites()[0].status == "accepted"


def test_second_pending_invite_for_same_email_is_refused(admin):
    invites.create_invite("new@x.org", "annotator", [], admin.id)
    with pytest.raises(ValueError):
        invites.create_invite("NEW@x.org", "reviewer", [], admin.id)


# ---- admin endpoints --------------------------------------------------

def test_admin_invite_endpoints(admin, login_as, client, two_sessions, monkeypatch):
    monkeypatch.setenv("HELIX_PUBLIC_URL", "https://helix.example.org")
    login_as(client, admin)
    r = client.post("/api/admin/invites",
                    json={"email": "New@x.org", "role": "reviewer", "session_names": ["alpha"]})
    assert r.status_code == 200
    body = r.json()
    assert body["sign_in_url"] == "https://helix.example.org"
    assert body["already_registered"] is False
    assert body["invite"]["email"] == "new@x.org"
    assert body["invite"]["status"] == "pending"
    assert body["invite"]["session_names"] == ["alpha"]
    listed = client.get("/api/admin/invites").json()
    assert [i["id"] for i in listed] == [body["invite"]["id"]]
    r = client.post(f"/api/admin/invites/{body['invite']['id']}/revoke")
    assert r.status_code == 200
    assert client.get("/api/admin/invites").json()[0]["status"] == "revoked"


def test_response_includes_sign_in_url(admin, login_as, client, monkeypatch):
    login_as(client, admin)
    monkeypatch.delenv("HELIX_PUBLIC_URL", raising=False)
    r = client.post("/api/admin/invites", json={"email": "a@x.org", "role": "annotator"})
    assert r.json()["sign_in_url"] == "http://localhost:5173"


def test_invite_for_registered_email_reports_it(admin, make_user, login_as, client):
    make_user("ann@x.org")
    login_as(client, admin)
    r = client.post("/api/admin/invites", json={"email": "ann@x.org", "role": "annotator"})
    assert r.json()["already_registered"] is True
    assert r.json()["invite"]["status"] == "accepted"


def test_invite_validation(admin, login_as, client, two_sessions):
    login_as(client, admin)

    def post(**kw):
        return client.post("/api/admin/invites", json=kw)

    assert post(email="a@x.org", role="annotator", session_names=["nope"]).status_code == 400
    assert post(email="a@x.org", role="superuser").status_code == 400
    assert post(email="not-an-email", role="annotator").status_code == 400
    assert post(email="a@x.org", role="annotator").status_code == 200
    assert post(email="a@x.org", role="annotator").status_code == 409  # duplicate pending
    assert client.post("/api/admin/invites/999/revoke").status_code == 404


def test_non_admin_cannot_manage_invites(make_user, login_as):
    for role in ("reviewer", "annotator"):
        c = TestClient(app)
        login_as(c, make_user(f"{role}@x.org", role))
        assert c.post("/api/admin/invites",
                      json={"email": "a@x.org", "role": "annotator"}).status_code == 403
        assert c.get("/api/admin/invites").status_code == 403
        assert c.post("/api/admin/invites/1/revoke").status_code == 403
    assert TestClient(app).get("/api/admin/invites").status_code == 401


def test_signin_endpoint_is_off_without_auth(tmp_path, monkeypatch, client):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr("eltool.session.SESSIONS_DIR", tmp_path)
    assert client.post("/api/auth/session", json={"id_token": "t"}).status_code == 404
    assert client.get("/api/admin/invites").status_code == 404
    assert not (tmp_path / "helix.db").exists()


def test_signin_needs_no_login_when_auth_is_on(auth_env, sign_in):
    # Public: a refusal is a 403 from the handler, never the gate's 401.
    assert sign_in(claims("nobody@x.org")).status_code == 403


def test_verify_id_token_without_project_id_raises(monkeypatch):
    monkeypatch.delenv("HELIX_FIREBASE_PROJECT_ID", raising=False)
    with pytest.raises(idtoken.IdTokenError):
        idtoken.verify_id_token("anything")


def test_verify_id_token_maps_claims_and_wraps_failures(monkeypatch):
    import google.oauth2.id_token as gid
    monkeypatch.setenv("HELIX_FIREBASE_PROJECT_ID", "proj-1")
    seen = {}

    def fake(token, request, audience=None, **kw):
        seen["audience"] = audience
        seen.update(kw)
        return {"sub": "u1", "email": "A@x.org", "email_verified": True, "name": "A",
                "iss": "https://securetoken.google.com/proj-1",
                "firebase": {"sign_in_provider": "google.com"}}
    monkeypatch.setattr(gid, "verify_firebase_token", fake)
    assert idtoken.verify_id_token("t") == {
        "uid": "u1", "email": "A@x.org", "email_verified": True, "name": "A",
        "provider": "google.com"}
    assert seen["audience"] == "proj-1"
    assert seen["clock_skew_in_seconds"] == 10

    def boom(token, request, audience=None, **kw):
        raise ValueError("Token has wrong audience")
    monkeypatch.setattr(gid, "verify_firebase_token", boom)
    with pytest.raises(idtoken.IdTokenError) as e:
        idtoken.verify_id_token("secret-token-value")
    assert "secret-token-value" not in str(e.value)


def _stub_verifier(monkeypatch, **overrides):
    import google.oauth2.id_token as gid
    monkeypatch.setenv("HELIX_FIREBASE_PROJECT_ID", "proj-1")
    payload = {"sub": "u1", "email": "A@x.org", "email_verified": True, "name": "A",
               "iss": "https://securetoken.google.com/proj-1",
               "firebase": {"sign_in_provider": "google.com"}}
    payload.update(overrides)
    monkeypatch.setattr(gid, "verify_firebase_token", lambda t, r, audience=None, **kw: payload)


def test_email_verified_must_be_the_boolean_true(monkeypatch):
    _stub_verifier(monkeypatch, email_verified="false")
    assert idtoken.verify_id_token("t")["email_verified"] is False
    _stub_verifier(monkeypatch, email_verified="true")
    assert idtoken.verify_id_token("t")["email_verified"] is False
    _stub_verifier(monkeypatch, email_verified=True)
    assert idtoken.verify_id_token("t")["email_verified"] is True


def test_wrong_issuer_is_rejected_without_leaking_the_token(monkeypatch):
    _stub_verifier(monkeypatch, iss="https://securetoken.google.com/other-proj")
    with pytest.raises(idtoken.IdTokenError) as e:
        idtoken.verify_id_token("secret-token-value")
    assert "secret-token-value" not in str(e.value)
    _stub_verifier(monkeypatch, iss=None)
    with pytest.raises(idtoken.IdTokenError):
        idtoken.verify_id_token("t")


# ---- provisioning is atomic and a refusal never changes the account ----

def _invite_status(email):
    return [i.status for i in invites.list_invites() if i.email == email]


def test_provisioning_failure_rolls_everything_back_and_retry_succeeds(
        admin, two_sessions, monkeypatch, sign_in):
    invites.create_invite("new@x.org", "reviewer", two_sessions, admin.id)
    real = invites._apply_invite

    def boom(*a, **k):
        raise RuntimeError("crash after the user insert")
    monkeypatch.setattr(invites, "_apply_invite", boom)
    with pytest.raises(RuntimeError):
        invites.provision(claims())
    assert userdb.get_user_by_email("new@x.org") is None
    assert userdb.get_user_by_uid("uid-1") is None
    assert _invite_status("new@x.org") == ["pending"]
    conn = userdb.connect()
    assert conn.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 0
    conn.close()

    monkeypatch.setattr(invites, "_apply_invite", real)
    r = sign_in(claims())
    assert r.status_code == 200
    assert members(r.json()["id"]) == ["alpha", "beta"]
    assert _invite_status("new@x.org") == ["accepted"]


def test_invite_revoked_between_lookup_and_consume_is_refused(admin, monkeypatch, sign_in):
    inv, _ = invites.create_invite("new@x.org", "annotator", [], admin.id)
    real = invites._pending_for

    def lookup_then_revoke(conn, email):
        row = real(conn, email)
        invites.revoke(inv.id)  # the admin revokes just after the sign-in read it
        return row
    monkeypatch.setattr(invites, "_pending_for", lookup_then_revoke)
    r = sign_in(claims())
    assert (r.status_code, r.json()) == (403, REFUSAL)
    assert userdb.get_user_by_email("new@x.org") is None
    assert _invite_status("new@x.org") == ["revoked"]


def test_refused_inactive_account_is_not_linked(auth_env, sign_in):
    boss = userdb.create_user("boss@x.org", "Boss", "admin")
    userdb.set_active(boss.id, False)
    r = sign_in(claims("boss@x.org", uid="uid-boss"))
    assert (r.status_code, r.json()) == (403, REFUSAL)
    assert userdb.get_user_by_uid("uid-boss") is None


def test_pending_invite_is_applied_when_the_email_became_registered_later(
        admin, two_sessions, sign_in):
    invites.create_invite("late@x.org", "reviewer", ["alpha"], admin.id)
    late = userdb.create_user("late@x.org", "Late", "annotator")  # e.g. created by the CLI
    r = sign_in(claims("late@x.org", uid="uid-late"))
    assert r.status_code == 200
    assert userdb.get_user_by_uid("uid-late").id == late.id
    assert members(late.id) == ["alpha"]
    assert _invite_status("late@x.org") == ["accepted"]
    assert userdb.get_user(late.id).role == "annotator"  # the invite's role is not applied


# ---- refusals are diagnosable from the server log, without leaking anything ----

def test_token_refusal_logs_a_category_without_the_token(auth_env, client, monkeypatch, caplog):
    import google.oauth2.id_token as gid
    monkeypatch.setenv("HELIX_FIREBASE_PROJECT_ID", "proj-1")

    def boom(token, request, audience=None, **kw):
        raise ValueError(f"Token used too early: {token}")
    monkeypatch.setattr(gid, "verify_firebase_token", boom)
    with caplog.at_level("WARNING", logger="api.auth_routes"):
        r = client.post("/api/auth/session", json={"id_token": "secret-token-value"})
    assert (r.status_code, r.json()) == (403, REFUSAL)
    (rec,) = [x for x in caplog.records if x.name == "api.auth_routes"]
    assert rec.levelname == "WARNING"
    assert rec.getMessage() == "sign-in refused: token (token rejected (ValueError))"
    assert "secret-token-value" not in caplog.text


@pytest.mark.parametrize("case, reason", [
    ("uninvited", "not_invited"), ("unverified", "claims"), ("inactive", "inactive"),
    ("other_uid", "uid_mismatch")])
def test_provisioning_refusal_logs_its_category_only(admin, sign_in, caplog, case, reason):
    if case == "inactive":
        u = userdb.create_user("off@x.org", "Off", "annotator")
        userdb.set_active(u.id, False)
        c = claims("off@x.org", uid="uid-off")
    elif case == "other_uid":
        u = userdb.create_user("linked@x.org", "L", "annotator")
        assert invites.provision(claims("linked@x.org", uid="uid-a")).id == u.id
        c = claims("linked@x.org", uid="uid-b")
    elif case == "unverified":
        c = claims(verified=False)
    else:
        c = claims("nobody@x.org", uid="uid-secret")
    with caplog.at_level("WARNING", logger="api.auth_routes"):
        r = sign_in(c)
    assert (r.status_code, r.json()) == (403, REFUSAL)
    msgs = [x.getMessage() for x in caplog.records if x.name == "api.auth_routes"]
    assert msgs == [f"sign-in refused: {reason}"]
    assert "@x.org" not in caplog.text and "uid-" not in caplog.text


def test_cert_fetch_uses_a_short_timeout_and_clock_skew(monkeypatch):
    import google.auth.transport.requests as greq
    import google.oauth2.id_token as gid
    monkeypatch.setenv("HELIX_FIREBASE_PROJECT_ID", "proj-1")
    seen = {}

    def parent_call(self, url, method="GET", body=None, headers=None, timeout=120, **kw):
        seen["timeout"] = timeout
        raise RuntimeError("no network in tests")
    monkeypatch.setattr(greq.Request, "__call__", parent_call)

    def verify(token, request, audience=None, clock_skew_in_seconds=0):
        seen["skew"] = clock_skew_in_seconds
        request("https://www.googleapis.com/robot/v1/metadata/x509/x")   # like fetch_certs
    monkeypatch.setattr(gid, "verify_firebase_token", verify)
    with pytest.raises(idtoken.IdTokenError):
        idtoken.verify_id_token("t")
    assert seen == {"skew": 10, "timeout": 5}
