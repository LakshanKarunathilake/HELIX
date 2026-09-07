"""End-to-end: the annotate endpoint must persist the study's provenance.

This is the path the pilot study actually runs through, so it is worth an
integration test rather than trusting the units to compose.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    from api.main import app

    return TestClient(app)


def _make_session(tmp_path, name: str, **cfg_extra) -> None:
    sdir = tmp_path / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["lung", "DM"], "document_id": ["d1", "d2"]}).to_csv(
        inp, index=False
    )
    cfg = {
        "session_name": name,
        "annotator": "lakshan",
        "input_path": str(inp),
        "delimiter": ",",
        "label_column": "entity_text",
        "kbs": [{"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query"}],
        **cfg_extra,
    }
    (sdir / "session.json").write_text(json.dumps(cfg))


def test_annotate_persists_provenance_for_a_study_session(client, tmp_path):
    _make_session(tmp_path, "study-c3", study_condition="C3")

    resp = client.post(
        "/api/sessions/study-c3/rows/0",
        json={
            "annotations": {"UMLS": "C0024109"},
            "elapsed_ms": 4210,
            "selected_rank": 3,
            "top1_id": "C0225730",
            "user_interacted": True,
        },
    )

    assert resp.status_code == 200
    saved = pd.read_csv(tmp_path / "study-c3" / "annotated.csv", dtype=str,
                        keep_default_na=False)
    assert saved.at[0, "_method"] == "user_override"
    assert saved.at[0, "_elapsed_ms"] == "4210"
    assert saved.at[0, "_selected_rank"] == "3"
    assert saved.at[0, "_top1_id"] == "C0225730"
    assert saved.at[0, "_condition"] == "C3"


def test_condition_is_stamped_from_session_config_not_the_client(client, tmp_path):
    """The annotator cannot mislabel which arm a row belongs to."""
    _make_session(tmp_path, "study-c1", study_condition="C1")

    client.post(
        "/api/sessions/study-c1/rows/0",
        json={"annotations": {"UMLS": "C1"}, "user_interacted": False,
              "top1_id": "C1", "condition": "C3"},
    )

    saved = pd.read_csv(tmp_path / "study-c1" / "annotated.csv", dtype=str,
                        keep_default_na=False)
    assert saved.at[0, "_condition"] == "C1"
    assert saved.at[0, "_method"] == "auto_accept"


def test_non_study_session_keeps_its_original_columns(client, tmp_path):
    """No study_condition set => the pre-existing BC5CDR layout is preserved."""
    _make_session(tmp_path, "bc5cdr-like")

    resp = client.post(
        "/api/sessions/bc5cdr-like/rows/0", json={"annotations": {"UMLS": "C1"}}
    )

    assert resp.status_code == 200
    saved = pd.read_csv(tmp_path / "bc5cdr-like" / "annotated.csv", dtype=str,
                        keep_default_na=False)
    assert list(saved.columns) == [
        "entity_text", "document_id", "annotator", "llm_used", "UMLS",
    ]
