"""The data source notice (HELIX_NOTICE) served by /api/info."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.mark.parametrize("value, expected", [(None, None), ("  ", None), (" MeSH 2026 ", "MeSH 2026")])
def test_info_notice(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("HELIX_NOTICE", raising=False)
    else:
        monkeypatch.setenv("HELIX_NOTICE", value)
    assert TestClient(app).get("/api/info").json()["notice"] == expected
