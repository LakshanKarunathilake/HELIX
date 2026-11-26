"""The reviewer judges annotators' work, so the reviewer role writes no annotations."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool import decisions, userdb
from eltool import feedback as fb

REFUSED_MSG = "Reviewers cannot annotate"

# every route that writes into the caller's own layer: (method, path, request kwargs)
WRITES = {
    "rows_batch": ("POST", "/api/sessions/s1/rows/batch",
                   {"json": {"updates": [{"idx": 0, "annotations": {"UMLS": "C1"}}]}}),
    "row_save": ("POST", "/api/sessions/s1/rows/0",
                 {"json": {"annotations": {"UMLS": "C1"}}}),
    "feedback": ("POST", "/api/sessions/s1/rows/0/feedback",
                 {"json": {"hint": "name_match", "confidence": "sure"}}),
    "reflection": ("PUT", "/api/sessions/s1/reflection", {"json": {"ranking": sorted(fb.HINT_CODES)}}),
    "auto_annotate": ("POST", "/api/sessions/s1/auto-annotate", {}),
    "graph_optimize": ("POST", "/api/sessions/s1/graph/optimize",
                       {"json": {"idx": 0, "kb_name": "UMLS"}}),
    "graph_optimize_all": ("POST", "/api/sessions/s1/graph/optimize-all", {}),
    "snapshot": ("POST", "/api/sessions/s1/snapshot", {"params": {"stage": "retrieval"}}),
}


class _FakeKB:
    def __init__(self, cfg):
        pass

    def query(self, label):
        return [SimpleNamespace(id="C1", label=label, score=1.0, matched_alias="",
                                aliases=[], semantic_groups="", semantic_types="",
                                definition="")]


class _FakeGraph:
    def optimize_selection(self, mention_candidates, **kwargs):
        return {"selections": {str(i): "C1" for i, _ in mention_candidates},
                "edges_before": 0, "edges_after": 0}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No KB server and no Neo4j: both are replaced where api.main looks them up."""
    monkeypatch.setattr("api.main.KBClient", _FakeKB)
    monkeypatch.setattr("api.main.resolve_graph_client", lambda cfg, pool: _FakeGraph())


def _make_session(root, name: str = "s1") -> None:
    sdir = root / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["cold", "flu"], "document_id": ["d1", "d1"]}).to_csv(
        inp, index=False)
    (sdir / "session.json").write_text(json.dumps({
        "session_name": name, "annotator": "lakshan", "input_path": str(inp),
        "delimiter": ",", "label_column": "entity_text", "doc_id_col": "document_id",
        "kbs": [{"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query"}],
        "enable_graph": True, "collect_feedback": True}))


@pytest.fixture
def world(auth_env, make_user):
    _make_session(auth_env)
    admin = make_user("root@x.org", "admin", name="Root")
    rev = make_user("rev@x.org", "reviewer", name="Rev")
    alice = make_user("alice@x.org", name="Alice")
    for u in (rev, alice):
        userdb.add_member("s1", u.id)
    return {"root": auth_env, "admin": admin, "rev": rev, "alice": alice}


@pytest.fixture
def as_user(login_as):
    from api.main import app

    def _as(user):
        return login_as(TestClient(app), user)
    return _as


def _call(client, route: str):
    method, path, kwargs = WRITES[route]
    return client.request(method, path, **kwargs)


@pytest.mark.parametrize("route", WRITES)
def test_reviewer_gets_403_on_every_annotation_write(world, as_user, route):
    rev = world["rev"]
    r = _call(as_user(rev), route)
    assert (r.status_code, r.json().get("detail")) == (403, REFUSED_MSG)
    assert not (world["root"] / "s1" / "annotations" / f"{rev.id}.csv").exists()
    assert decisions.list_decisions("s1") == []


@pytest.mark.parametrize("who", ["alice", "admin"])
@pytest.mark.parametrize("route", WRITES)
def test_annotator_and_admin_are_not_refused(world, as_user, route, who):
    r = _call(as_user(world[who]), route)
    assert r.status_code != 403, r.text
    if route == "row_save":
        assert r.status_code == 200, r.text


@pytest.mark.parametrize("route", WRITES)
def test_reviewer_who_is_not_a_member_gets_404(world, as_user, make_user, route):
    outsider = make_user("out@x.org", "reviewer", name="Out")
    r = _call(as_user(outsider), route)
    assert (r.status_code, r.json()["detail"]) == (404, "Session not found")


def test_auth_off_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    _make_session(tmp_path)
    from api.main import app
    r = _call(TestClient(app), "row_save")
    assert r.status_code == 200, r.text


def test_answers_saved_before_becoming_reviewer_still_count(world, as_user):
    alice = world["alice"]
    r = _call(as_user(alice), "row_save")
    assert r.status_code == 200, r.text
    userdb.set_role(alice.id, "reviewer")
    r = as_user(world["rev"]).get("/api/sessions/s1/review")
    assert r.status_code == 200, r.text
    (row,) = r.json()["rows"]
    assert [(a["user_id"], a["kb_ids"]) for a in row["answers"]] == [
        (alice.id, {"UMLS": ["C1"]})]


def test_every_session_write_route_is_guarded():
    """A new write route under a session must say who may call it."""
    from api.main import app

    prefix = "/api/sessions/{name}"
    guards = {"annotator_access", "require_admin", "reviewer_access"}
    read_only = {f"{prefix}/{p}" for p in ("candidates", "graph/candidates", "graph/document")}
    checked, unguarded = 0, []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", None) or ())
        writes = methods & {"POST", "PUT", "PATCH", "DELETE"}
        if not path.startswith(prefix) or not writes or path in read_only:
            continue
        checked += 1
        if not guards & {d.call.__name__ for d in route.dependant.dependencies}:
            unguarded.append((sorted(writes), path))
    assert checked >= len(WRITES)      # the walk really found the routes
    assert unguarded == []
