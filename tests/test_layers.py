"""Per-user annotation layers and the endpoint access matrix (auth mode)."""
from __future__ import annotations

import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool import feedback as fb
from eltool import userdb


def _make_session(root, name: str, **cfg_extra) -> None:
    sdir = root / name
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
        "doc_id_col": "document_id",
        "kbs": [{"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query"}],
        **cfg_extra,
    }
    (sdir / "session.json").write_text(json.dumps(cfg))


def _read(path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _save(client, name, value, idx=0):
    r = client.post(f"/api/sessions/{name}/rows/{idx}", json={"annotations": {"UMLS": value}})
    assert r.status_code == 200, r.text
    return r


@pytest.fixture
def world(auth_env, make_user):
    """Session s1 with members alice and bob, plus an admin and an outsider."""
    _make_session(auth_env, "s1", collect_feedback=True)
    admin = make_user("root@x.org", "admin", name="Root")
    alice = make_user("alice@x.org", name="Alice")
    bob = make_user("bob@x.org", name="Bob")
    eve = make_user("eve@x.org", name="Eve")
    userdb.add_member("s1", alice.id)
    userdb.add_member("s1", bob.id)
    return {"root": auth_env, "admin": admin, "alice": alice, "bob": bob, "eve": eve}


@pytest.fixture
def as_user(login_as):
    from api.main import app

    def _as(user):
        return login_as(TestClient(app), user)
    return _as


def test_user_a_and_b_get_separate_layer_files(world, as_user):
    a, b, root = world["alice"], world["bob"], world["root"]
    _save(as_user(a), "s1", "C0001")
    _save(as_user(b), "s1", "C0002")
    layer_a = _read(root / "s1" / "annotations" / f"{a.id}.csv")
    layer_b = _read(root / "s1" / "annotations" / f"{b.id}.csv")
    assert layer_a.at[0, "UMLS"] == "C0001"
    assert layer_b.at[0, "UMLS"] == "C0002"
    assert not (root / "s1" / "annotated.csv").exists()


def test_annotator_sees_only_own_saved_value(world, as_user):
    _save(as_user(world["alice"]), "s1", "C0001")
    row = as_user(world["bob"]).get("/api/sessions/s1/rows/0").json()
    assert row["annotations"] == {"UMLS": ""}
    row = as_user(world["alice"]).get("/api/sessions/s1/rows/0").json()
    assert row["annotations"] == {"UMLS": "C0001"}


FLOW_ROUTES = [
    ("get", "/api/sessions/s1", None),
    ("get", "/api/sessions/s1/rows/0", None),
    ("post", "/api/sessions/s1/rows/0", {"annotations": {"UMLS": "C1"}}),
    ("post", "/api/sessions/s1/rows/batch", {"updates": [{"idx": 0, "annotations": {"UMLS": "C1"}}]}),
    ("post", "/api/sessions/s1/rows/0/feedback", {"hint": "x", "confidence": "y"}),
    ("get", "/api/sessions/s1/reflection", None),
    ("put", "/api/sessions/s1/reflection", {"ranking": [], "comment": ""}),
    ("get", "/api/sessions/s1/progress", None),
    ("get", "/api/sessions/s1/documents", None),
    ("get", "/api/sessions/s1/documents/mentions?idx=0", None),
    ("post", "/api/sessions/s1/candidates", {"label": "lung", "kb_name": "UMLS"}),
    ("post", "/api/sessions/s1/graph/candidates", {"ids": []}),
    ("post", "/api/sessions/s1/graph/document", {"cuis": []}),
    ("post", "/api/sessions/s1/graph/optimize", {"idx": 0, "kb_name": "UMLS"}),
    ("post", "/api/sessions/s1/graph/optimize-all", None),
    ("post", "/api/sessions/s1/auto-annotate", None),
    ("get", "/api/sessions/s1/kb/UMLS/entity/C0001", None),
    ("post", "/api/sessions/s1/snapshot?stage=retrieval", None),
    ("get", "/api/sessions/s1/snapshots", None),
    ("get", "/api/sessions/s1/export", None),
]


@pytest.mark.parametrize("method,path,body", FLOW_ROUTES)
def test_non_member_gets_404_on_every_flow_endpoint(world, as_user, method, path, body):
    client = as_user(world["eve"])
    kwargs = {"json": body} if body is not None else {}
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 404
    assert r.json()["detail"] == "Session not found"
    assert not (world["root"] / "s1" / "annotations").exists()


def test_session_list_is_filtered_to_memberships(world, as_user):
    _make_session(world["root"], "s2")
    assert as_user(world["alice"]).get("/api/sessions").json() == {"sessions": ["s1"]}
    assert as_user(world["eve"]).get("/api/sessions").json() == {"sessions": []}
    assert as_user(world["admin"]).get("/api/sessions").json() == {"sessions": ["s1", "s2"]}


ADMIN_ROUTES = [
    ("post", "/api/sessions", {"json": {}}),
    ("put", "/api/sessions/s1", {"json": {}}),
    ("delete", "/api/sessions/s1", {}),
    ("post", "/api/sessions/s1/context", {"files": {"file": ("c.zip", b"x")}}),
    ("post", "/api/sessions/parse-file", {"files": {"file": ("a.csv", b"a\n1\n")}}),
    ("post", "/api/kb/test", {"json": {"kb_config": {}}}),
    ("post", "/api/graph/test", {"json": {"graph": {}}}),
    ("get", "/api/metrics/status", {}),
    ("post", "/api/metrics/toggle", {"json": {"enabled": False}}),
    ("get", "/api/sessions/s1/metrics/insights", {}),
    ("post", "/api/sessions/s1/metrics/reset", {}),
    ("get", "/api/sessions/s1/metrics/export", {}),
    ("get", "/api/sessions/s1/graph/stats", {}),
    ("post", "/api/sessions/s1/graph/stats/reset", {}),
]


@pytest.mark.parametrize("method,path,kwargs", ADMIN_ROUTES)
def test_annotator_cannot_create_or_edit_sessions(world, as_user, method, path, kwargs):
    r = getattr(as_user(world["alice"]), method)(path, **kwargs)
    assert r.status_code == 403
    assert (world["root"] / "s1" / "session.json").exists()


def test_admin_can(world, as_user):
    client = as_user(world["admin"])
    up = client.post("/api/sessions/parse-file",
                     files={"file": ("in.csv", b"entity_text\nlung\nDM\n")}).json()
    r = client.post("/api/sessions", json={
        "session_name": "s3", "annotator": "anything", "tmp_path": up["tmp_path"],
        "original_filename": "in.csv", "delimiter": up["delimiter"],
        "label_column": "entity_text",
        "kbs": [{"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query"}]})
    assert r.status_code == 200, r.text
    cfg = client.get("/api/sessions/s3").json()
    assert cfg["annotator"] == "Root"
    cfg["auto_select_secs"] = 9
    assert client.put("/api/sessions/s3", json=cfg).status_code == 200
    assert client.get("/api/sessions/s3").json()["auto_select_secs"] == 9
    assert client.get("/api/metrics/status").status_code == 200
    assert client.get("/api/sessions/s3/graph/stats").status_code == 200
    assert client.delete("/api/sessions/s3").status_code == 200
    assert not (world["root"] / "s3").exists()


def test_legacy_session_opens_with_empty_layer(world, as_user):
    root = world["root"]
    legacy = _read(root / "s1" / "input.csv")
    legacy["annotator"] = "lakshan"
    legacy["llm_used"] = ""
    legacy["UMLS"] = ["C0001", "C0002"]
    legacy.to_csv(root / "s1" / "annotated.csv", index=False)
    client = as_user(world["alice"])
    r = client.get("/api/sessions/s1/progress")
    assert r.status_code == 200
    assert r.json()["done"] == 0
    assert client.get("/api/sessions/s1/rows/0").json()["annotations"] == {"UMLS": ""}
    assert _read(root / "s1" / "annotated.csv").at[0, "UMLS"] == "C0001"


def test_reflection_is_per_user(world, as_user):
    a, b = world["alice"], world["bob"]
    ranking = sorted(fb.hint_codes(include_ai=False))
    ca = as_user(a)
    r = ca.put("/api/sessions/s1/reflection", json={"ranking": ranking, "comment": "ok"})
    assert r.status_code == 200
    assert r.json()["annotator"] == "Alice"
    assert (world["root"] / "s1" / f"reflection.{a.id}.json").exists()
    assert not (world["root"] / "s1" / "reflection.json").exists()
    assert ca.get("/api/sessions/s1/reflection").json()["comment"] == "ok"
    assert as_user(b).get("/api/sessions/s1/reflection").json() is None


def test_snapshots_are_per_user_and_listing_excludes_legacy_and_other_users(world, as_user):
    a, b, root = world["alice"], world["bob"], world["root"]
    (root / "s1" / "annotated.old.csv").write_text("x\n")
    ca, cb = as_user(a), as_user(b)
    _save(ca, "s1", "C0001")
    _save(cb, "s1", "C0002")
    assert ca.post("/api/sessions/s1/snapshot?stage=retrieval").status_code == 200
    assert cb.post("/api/sessions/s1/snapshot?stage=graph").status_code == 200
    assert (root / "s1" / f"annotated.retrieval.{a.id}.csv").exists()
    assert ca.get("/api/sessions/s1/snapshots").json() == {"stages": ["retrieval"]}
    assert cb.get("/api/sessions/s1/snapshots").json() == {"stages": ["graph"]}
    r = ca.get("/api/sessions/s1/export?stage=retrieval")
    assert r.status_code == 200
    assert "C0001" in r.text
    assert "s1_retrieval" in r.headers["content-disposition"]
    assert ca.get("/api/sessions/s1/export?stage=graph").status_code == 404


def test_export_user_param_is_admin_only(world, as_user):
    a, b = world["alice"], world["bob"]
    ca = as_user(a)
    _save(ca, "s1", "C0001")
    assert "C0001" in ca.get("/api/sessions/s1/export").text      # own layer
    assert ca.get(f"/api/sessions/s1/export?user={a.id}").status_code == 403
    assert as_user(b).get(f"/api/sessions/s1/export?user={a.id}").status_code == 403
    admin = as_user(world["admin"])
    r = admin.get(f"/api/sessions/s1/export?user={a.id}")
    assert r.status_code == 200
    assert _read(io.StringIO(r.text)).at[0, "UMLS"] == "C0001"
    ca.post("/api/sessions/s1/snapshot?stage=retrieval")
    assert admin.get(f"/api/sessions/s1/export?user={a.id}&stage=retrieval").status_code == 200
    assert admin.get(f"/api/sessions/s1/export?user={b.id}").status_code == 404


def test_save_row_stamps_user_name_in_annotator_column(world, as_user):
    a = world["alice"]
    ca = as_user(a)
    _save(ca, "s1", "C0001")
    r = ca.post("/api/sessions/s1/rows/batch",
                json={"updates": [{"idx": 1, "annotations": {"UMLS": "C0002"}}]})
    assert r.status_code == 200
    layer = _read(world["root"] / "s1" / "annotations" / f"{a.id}.csv")
    assert list(layer["annotator"]) == ["Alice", "Alice"]


def test_auth_off_paths_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    from api.main import app

    client = TestClient(app)
    _make_session(tmp_path, "s1")
    _save(client, "s1", "C0001")
    out = _read(tmp_path / "s1" / "annotated.csv")
    assert out.at[0, "UMLS"] == "C0001"
    assert out.at[0, "annotator"] == "lakshan"
    assert not (tmp_path / "s1" / "annotations").exists()
    assert client.get("/api/sessions").json() == {"sessions": ["s1"]}
    assert client.post("/api/sessions/s1/snapshot?stage=retrieval").status_code == 200
    assert client.get("/api/sessions/s1/snapshots").json() == {"stages": ["retrieval"]}
    assert "C0001" in client.get("/api/sessions/s1/export").text
