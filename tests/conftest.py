import pytest


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """Auth mode on, with the sessions directory (and helix.db) in tmp_path."""
    monkeypatch.setenv("HELIX_AUTH", "1")
    monkeypatch.setattr("eltool.session.SESSIONS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def make_user(auth_env):
    """Create an active user in the test helix.db."""
    from eltool import userdb

    def _make(email, role="annotator", name=None):
        return userdb.create_user(email, name or email.split("@")[0], role)
    return _make


@pytest.fixture
def login_as():
    """Give a TestClient the login cookie of ``user``."""
    from eltool import userdb
    from eltool.auth import COOKIE_NAME

    def _login(client, user):
        client.cookies.set(COOKIE_NAME, userdb.create_auth_session(user.id))
        return client
    return _login
