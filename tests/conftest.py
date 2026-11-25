import pytest


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """Auth mode on, with the sessions directory (and helix.db) in tmp_path."""
    monkeypatch.setenv("HELIX_AUTH", "1")
    monkeypatch.setattr("eltool.session.SESSIONS_DIR", tmp_path)
    return tmp_path
