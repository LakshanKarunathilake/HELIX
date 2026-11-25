import pytest

from eltool import userdb
from eltool.hosting import auth_enabled


def test_auth_enabled_follows_env(monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    assert auth_enabled() is False
    monkeypatch.setenv("HELIX_AUTH", "1")
    assert auth_enabled() is True
    monkeypatch.setenv("HELIX_AUTH", "0")
    assert auth_enabled() is False


def test_email_is_lowercased_and_unique(auth_env):
    u = userdb.create_user("A@x.org", "Ann", "annotator")
    assert u.email == "a@x.org"
    assert userdb.get_user_by_email("a@x.org").id == u.id
    assert userdb.get_user_by_email(" A@X.org ").id == u.id
    with pytest.raises(ValueError):
        userdb.create_user(" a@X.org ", "Ann again", "reviewer")


def test_role_must_be_valid(auth_env):
    with pytest.raises(ValueError):
        userdb.create_user("a@x.org", "Ann", "superuser")
    assert userdb.list_users() == []


def test_link_uid_and_lookup_by_uid(auth_env):
    u = userdb.create_user("a@x.org", "Ann", "annotator")
    assert userdb.get_user_by_uid("uid-1") is None
    userdb.link_uid(u.id, "uid-1")
    assert userdb.get_user_by_uid("uid-1").id == u.id
    assert userdb.get_user(u.id).email == "a@x.org"


def test_uid_is_unique(auth_env):
    a = userdb.create_user("a@x.org", "Ann", "annotator")
    b = userdb.create_user("b@x.org", "Bob", "annotator")
    userdb.link_uid(a.id, "uid-1")
    with pytest.raises(ValueError):
        userdb.link_uid(b.id, "uid-1")
    with pytest.raises(ValueError):
        userdb.create_user("c@x.org", "Cy", "annotator", firebase_uid="uid-1")


def test_db_file_is_created_in_sessions_dir(auth_env):
    assert not (auth_env / "helix.db").exists()
    userdb.connect().close()
    assert (auth_env / "helix.db").is_file()


def test_schema_is_idempotent(auth_env):
    c1 = userdb.connect()
    c1.execute("INSERT INTO users(email, name, role, active, created_at) "
               "VALUES ('k@x.org', 'K', 'admin', 1, 'now')")
    c1.commit()
    c1.close()
    c2 = userdb.connect()
    tables = {r["name"] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "invites", "auth_sessions", "memberships",
            "decisions", "adjudications"} <= tables
    assert c2.execute("PRAGMA user_version").fetchone()[0] == 1
    assert c2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c2.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    c2.close()


def test_set_role_active_and_touch_login(auth_env):
    u = userdb.create_user("a@x.org", "Ann", "annotator")
    assert u.active is True
    userdb.set_role(u.id, "reviewer")
    userdb.set_active(u.id, False)
    got = userdb.get_user(u.id)
    assert got.role == "reviewer" and got.active is False
    with pytest.raises(ValueError):
        userdb.set_role(u.id, "nope")
    assert [x.id for x in userdb.list_users()] == [u.id]


def test_touch_login_stamps_last_login(auth_env):
    u = userdb.create_user("a@x.org", "Ann", "annotator")
    conn = userdb.connect()
    sql = "SELECT last_login_at FROM users WHERE id = ?"
    assert conn.execute(sql, (u.id,)).fetchone()[0] is None
    userdb.touch_login(u.id)
    assert conn.execute(sql, (u.id,)).fetchone()[0] is not None
    conn.close()


def test_updates_on_a_missing_user_raise(auth_env):
    for call in (lambda: userdb.link_uid(99, "uid"),
                 lambda: userdb.set_role(99, "reviewer"),
                 lambda: userdb.set_active(99, False),
                 lambda: userdb.touch_login(99)):
        with pytest.raises(ValueError):
            call()


def test_connect_on_an_existing_db_does_not_take_the_write_lock(auth_env):
    userdb.connect().close()
    writer = userdb.connect()
    writer.execute("BEGIN IMMEDIATE")  # holds the write lock
    try:
        userdb.connect().close()  # must not block or raise "database is locked"
    finally:
        writer.rollback()
        writer.close()


def test_token_hash_is_sha256_hex():
    assert userdb.token_hash("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
