"""The append-only decision log: every server-side layer write is logged."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool import decisions, userdb
from eltool.graph_client import GraphSchema, Neo4jGraphClient

CSV_HEADER = ["id", "ts", "session_name", "row_idx", "user_id", "user_name", "user_email",
              "actor", "action", "kb_values", "previous", "llm_used", "method",
              "elapsed_ms", "selected_rank", "revision"]


def _make_session(root, name: str, **cfg_extra) -> None:
    sdir = root / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["cold", "flu", "virus"],
                  "document_id": ["d1", "d1", "d1"]}).to_csv(inp, index=False)
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


def _layer(root, name, user) -> pd.DataFrame:
    return pd.read_csv(root / name / "annotations" / f"{user.id}.csv",
                       dtype=str, keep_default_na=False)


@pytest.fixture
def world(auth_env, make_user):
    _make_session(auth_env, "s1", collect_feedback=True, enable_graph=True)
    admin = make_user("root@x.org", "admin", name="Root")
    alice = make_user("alice@x.org", name="Alice")
    bob = make_user("bob@x.org", name="Bob")
    userdb.add_member("s1", alice.id)
    userdb.add_member("s1", bob.id)
    return {"root": auth_env, "admin": admin, "alice": alice, "bob": bob}


@pytest.fixture
def as_user(login_as):
    from api.main import app

    def _as(user, **kw):
        return login_as(TestClient(app, **kw), user)
    return _as


def _save(client, value, idx=0, name="s1", **extra):
    r = client.post(f"/api/sessions/{name}/rows/{idx}",
                    json={"annotations": {"UMLS": value}, **extra})
    assert r.status_code == 200, r.text


def test_save_row_logs_human_decision_with_previous_value(world, as_user):
    alice = world["alice"]
    c = as_user(alice)
    _save(c, "C1")
    _save(c, "C2")
    log = decisions.list_decisions("s1")
    assert [(d.actor, d.action, d.row_idx, d.user_id) for d in log] == [
        ("human", "save", 0, alice.id), ("human", "save", 0, alice.id)]
    assert log[0].kb_values == {"UMLS": "C1"}
    assert log[0].previous == {"UMLS": ""}
    assert log[1].kb_values == {"UMLS": "C2"}
    assert log[1].previous == {"UMLS": "C1"}
    assert log[1].revision is None          # ordinary session: no _revisions column


def test_save_row_logs_provenance_and_revision(auth_env, make_user, as_user):
    _make_session(auth_env, "study", study_condition="C3")
    alice = make_user("alice@x.org", name="Alice")
    userdb.add_member("study", alice.id)
    c = as_user(alice)
    _save(c, "C1", name="study", elapsed_ms=4210, selected_rank=3, top1_id="C9",
          user_interacted=True)
    _save(c, "C2", name="study")
    first, second = decisions.list_decisions("study")
    assert (first.method, first.elapsed_ms, first.selected_rank, first.revision) == \
        ("user_override", 4210, 3, 0)
    assert second.revision == 1


def test_batch_and_optimize_log_auto_match_actor(world, as_user, monkeypatch):
    import api.main as main
    cands = {"cold": [("C_TEMP", 1.0), ("C_DISEASE", 1.0)], "flu": [("C_FLU", 1.0)],
             "virus": [("C_VIRUS", 1.0)]}
    edges = [("C_DISEASE", "C_VIRUS")]

    def graph_client(cfg, pool):
        gc = Neo4jGraphClient.__new__(Neo4jGraphClient)
        gc.schema = GraphSchema()
        gc._edges_among = lambda ids: [(a, b) for a, b in edges if a in ids and b in ids]
        return gc

    monkeypatch.setattr(main, "_retrieve_fused", lambda kb_cfg, label: [
        SimpleNamespace(id=cid, score=sc) for cid, sc in cands.get(label, [])])
    monkeypatch.setattr(main, "resolve_graph_client", graph_client)
    monkeypatch.setattr(main.metrics, "record_graph", lambda *a, **k: None)

    alice, bob = world["alice"], world["bob"]
    ca = as_user(alice)
    r = ca.post("/api/sessions/s1/rows/batch", json={"updates": [
        {"idx": 0, "annotations": {"UMLS": "C_TEMP"}},
        {"idx": 1, "annotations": {"UMLS": "C_FLU"}, "llm_used": "m/x"},
        {"idx": 99, "annotations": {"UMLS": "C_OUT"}}]})       # out of range: not written
    assert r.status_code == 200, r.text
    # single-document optimise only returns selections: it writes and logs nothing
    assert ca.post("/api/sessions/s1/graph/optimize",
                   json={"idx": 0, "kb_name": "UMLS"}).status_code == 200
    log = decisions.list_decisions("s1", user_id=alice.id)
    assert [(d.row_idx, d.actor, d.action, d.kb_values, d.llm_used) for d in log] == [
        (0, "auto_match", "batch", {"UMLS": "C_TEMP"}, None),
        (1, "auto_match", "batch", {"UMLS": "C_FLU"}, "m/x")]

    r = as_user(bob).post("/api/sessions/s1/graph/optimize-all")
    assert r.status_code == 200, r.text
    log = decisions.list_decisions("s1", user_id=bob.id)
    assert r.json()["mentions_written"] == len(log) == 3
    assert {d.actor for d in log} == {"auto_match"}
    assert {d.action for d in log} == {"auto"}
    assert {d.row_idx: d.kb_values["UMLS"] for d in log} == _layer(
        world["root"], "s1", bob)["UMLS"].to_dict()
    assert all(d.previous == {"UMLS": ""} for d in log)


def test_auto_annotate_logs_auto_annotate_actor(world, as_user, monkeypatch):
    import api.main as main

    class FakeKB:
        def __init__(self, cfg):
            pass

        def query(self, label):
            return [SimpleNamespace(id=f"C_{label.upper()}", score=1.0, semantic_groups="")]

    monkeypatch.setattr(main, "KBClient", FakeKB)
    alice = world["alice"]
    c = as_user(alice)
    _save(c, "C_MINE", idx=1)            # a saved human decision is skipped, not overwritten
    r = c.post("/api/sessions/s1/auto-annotate")
    assert r.status_code == 200, r.text
    assert r.json()["annotated"] == 2
    auto = [d for d in decisions.list_decisions("s1") if d.actor == "auto_annotate"]
    assert [(d.row_idx, d.action, d.kb_values, d.previous) for d in auto] == [
        (0, "auto", {"UMLS": "C_COLD"}, {"UMLS": ""}),
        (2, "auto", {"UMLS": "C_VIRUS"}, {"UMLS": ""})]
    assert _layer(world["root"], "s1", alice)["UMLS"].tolist() == ["C_COLD", "C_MINE", "C_VIRUS"]


def test_llm_accept_is_human_with_llm_used(world, as_user):
    _save(as_user(world["alice"]), "C1", llm_used="anthropic/claude")
    (d,) = decisions.list_decisions("s1")
    assert (d.actor, d.action, d.llm_used) == ("human", "save", "anthropic/claude")


def test_feedback_is_logged_with_empty_kb_values(world, as_user):
    c = as_user(world["alice"])
    _save(c, "C1")
    r = c.post("/api/sessions/s1/rows/0/feedback", json={"hint": "definition", "confidence": "sure"})
    assert r.status_code == 200, r.text
    last = decisions.list_decisions("s1")[-1]
    assert (last.actor, last.action, last.kb_values) == ("human", "feedback", {})


def test_failed_log_insert_prevents_the_save(world, as_user, monkeypatch):
    alice = world["alice"]
    c = as_user(alice, raise_server_exceptions=False)
    _save(c, "C1")

    def boom(self, *a, **k):
        raise RuntimeError("log insert failed")
    monkeypatch.setattr(decisions._Transaction, "add", boom)

    r = c.post("/api/sessions/s1/rows/0", json={"annotations": {"UMLS": "C2"}})
    assert r.status_code == 500
    assert _layer(world["root"], "s1", alice).at[0, "UMLS"] == "C1"
    assert len(decisions.list_decisions("s1")) == 1


def test_failed_csv_write_rolls_back_the_log(world, as_user, monkeypatch):
    import api.main as main

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(main, "write_row", boom)
    r = as_user(world["alice"], raise_server_exceptions=False).post(
        "/api/sessions/s1/rows/0", json={"annotations": {"UMLS": "C1"}})
    assert r.status_code == 500
    assert decisions.list_decisions("s1") == []


def test_two_users_saving_same_row_concurrently(world, as_user):
    alice, bob = world["alice"], world["bob"]
    clients = {alice.id: as_user(alice), bob.id: as_user(bob)}
    errors = []

    def save(uid):
        try:
            _save(clients[uid], f"C{uid}")
        except AssertionError as e:      # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=save, args=(uid,)) for uid in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    for u in (alice, bob):
        assert _layer(world["root"], "s1", u).at[0, "UMLS"] == f"C{u.id}"
    log = decisions.list_decisions("s1")
    assert sorted((d.user_id, d.kb_values["UMLS"]) for d in log) == [
        (alice.id, f"C{alice.id}"), (bob.id, f"C{bob.id}")]


def test_auth_off_writes_no_log(tmp_path, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    _make_session(tmp_path, "s1")
    from api.main import app
    _save(TestClient(app), "C1")
    assert (tmp_path / "s1" / "annotated.csv").exists()
    assert not (tmp_path / "helix.db").exists()


def test_delete_refused_after_decisions_and_allowed_before(world, as_user):
    root, admin, alice = world["root"], world["admin"], world["alice"]
    _make_session(root, "s2")
    userdb.add_member("s2", alice.id)
    ca = as_user(admin)

    r = ca.delete("/api/sessions/s2")
    assert r.status_code == 200, r.text
    assert not (root / "s2").exists()
    assert userdb.members("s2") == []

    _save(as_user(alice), "C1")
    r = ca.delete("/api/sessions/s1")
    assert r.status_code == 409
    assert r.json()["detail"] == "This session has logged decisions and cannot be deleted"
    assert (root / "s1").exists()
    assert [m.id for m in userdb.members("s1")] == [alice.id, world["bob"].id]


def test_decisions_csv_is_admin_only_and_has_header(world, as_user):
    alice, admin = world["alice"], world["admin"]
    _save(as_user(alice), "C1")
    _save(as_user(world["bob"]), "C9")
    ca = as_user(alice)
    assert ca.get("/api/admin/decisions.csv?session=s1").status_code == 403
    assert ca.get("/api/admin/decisions?session=s1").status_code == 403

    r = as_user(admin).get("/api/admin/decisions.csv?session=s1")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == CSV_HEADER
    assert len(rows) == 3
    first = dict(zip(CSV_HEADER, rows[1]))
    assert (first["user_name"], first["user_email"], first["actor"]) == \
        ("Alice", "alice@x.org", "human")
    assert json.loads(first["kb_values"]) == {"UMLS": "C1"}

    r = as_user(admin).get(f"/api/admin/decisions?session=s1&user={alice.id}")
    assert r.status_code == 200
    assert [(d["user_id"], d["kb_values"]) for d in r.json()] == [(alice.id, {"UMLS": "C1"})]


def test_decision_routes_are_404_with_auth_off(tmp_path, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    from api.main import app
    c = TestClient(app)
    assert c.get("/api/admin/decisions").status_code == 404
    assert c.get("/api/admin/decisions.csv").status_code == 404


def test_no_route_modifies_decisions():
    from api.main import app
    writers = [r.path for r in app.routes
               if "decisions" in getattr(r, "path", "")
               and set(getattr(r, "methods", None) or ()) - {"GET", "HEAD"}]
    assert writers == []
    assert not [n for n in dir(decisions) if n.startswith(("update", "delete", "remove"))]


def test_latest_human_or_script_ignores_feedback(world, as_user):
    alice = world["alice"]
    with decisions.transaction() as tx:
        tx.add("s1", 0, alice.id, "auto_annotate", "auto", {"UMLS": "A"}, {"UMLS": ""})
        tx.add("s1", 0, alice.id, "human", "save", {"UMLS": "B"}, {"UMLS": "A"})
        tx.add("s1", 0, alice.id, "human", "feedback", {}, {})
        tx.add("s1", 1, alice.id, "auto_match", "auto", {"UMLS": "C"}, {"UMLS": ""})
    latest = decisions.latest_human_or_script("s1")
    assert {k: (d.actor, d.action) for k, d in latest.items()} == {
        (alice.id, 0): ("human", "save"), (alice.id, 1): ("auto_match", "auto")}
    assert decisions.has_decisions("s1") and not decisions.has_decisions("other")


def test_transaction_rolls_back_on_exception(world):
    with pytest.raises(RuntimeError):
        with decisions.transaction() as tx:
            tx.add("s1", 0, world["alice"].id, "human", "save", {"UMLS": "A"}, {"UMLS": ""})
            raise RuntimeError("boom")
    assert decisions.list_decisions("s1") == []


def test_adjudication_stores_basis_sources_and_based_on(world):
    admin, alice = world["admin"], world["alice"]
    based_on = {str(alice.id): {"UMLS": "C1|C2"}}
    made = decisions.add_adjudication(
        "s1", 0, admin.id, {"UMLS": "C1|C2"}, "why", basis="accepted",
        source_users=[alice.id], based_on=based_on)
    assert set(made) == {"id", "ts", "row_idx", "reviewer_id", "kb_values", "note",
                         "basis", "source_users", "based_on"}
    assert (made["row_idx"], made["reviewer_id"], made["kb_values"], made["note"]) == \
        (0, admin.id, {"UMLS": "C1|C2"}, "why")
    assert (made["basis"], made["source_users"], made["based_on"]) == \
        ("accepted", [alice.id], based_on)
    assert decisions.current_adjudications("s1") == {0: made}
    plain = decisions.add_adjudication("s1", 1, admin.id, {"UMLS": "C3"})
    assert (plain["basis"], plain["source_users"], plain["based_on"], plain["note"]) == \
        ("legacy", [], None, "")


def test_review_transaction_reads_and_writes_one_session(world):
    admin, alice = world["admin"], world["alice"]
    with decisions.transaction() as tx:
        tx.add("s1", 0, alice.id, "human", "save", {"UMLS": "A"}, {"UMLS": ""})
        tx.add("s1", 0, alice.id, "human", "feedback", {}, {})
        tx.add("other", 0, alice.id, "human", "save", {"UMLS": "Z"}, {"UMLS": ""})
    decisions.add_adjudication("other", 5, admin.id, {"UMLS": "Z"})
    with decisions.review_transaction("s1") as tx:
        assert tx.latest() == decisions.latest_human_or_script("s1")
        assert {k: d.kb_values for k, d in tx.latest().items()} == {
            (alice.id, 0): {"UMLS": "A"}}
        assert tx.adjudications() == {}
        first = tx.add(0, admin.id, {"UMLS": "A"}, "", basis="accepted",
                       source_users=[alice.id], based_on={str(alice.id): {"UMLS": "A"}})
        second = tx.add(0, admin.id, {"UMLS": "B"}, "n", basis="override",
                        source_users=[], based_on={})
        assert tx.adjudications() == {0: second}
    assert first["basis"] == "accepted"
    assert (second["source_users"], second["based_on"]) == ([], {})
    assert decisions.current_adjudications("s1") == {0: second}
    assert list(decisions.current_adjudications("other")) == [5]


def test_review_transaction_holds_the_write_lock(world):
    with decisions.review_transaction("s1"):
        other = userdb.connect()
        other.execute("PRAGMA busy_timeout=0")
        try:
            with pytest.raises(sqlite3.OperationalError):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()


def test_review_transaction_rolls_back(world):
    with pytest.raises(RuntimeError):
        with decisions.review_transaction("s1") as tx:
            tx.add(0, world["admin"].id, {"UMLS": "A"}, "", basis="override",
                   source_users=[], based_on={})
            raise RuntimeError("boom")
    assert decisions.current_adjudications("s1") == {}
    assert not decisions.has_adjudications("s1")


def _fake_kb(main, monkeypatch):
    class FakeKB:
        def __init__(self, cfg):
            pass

        def query(self, label):
            return [SimpleNamespace(id=f"C_{label.upper()}", score=1.0, semantic_groups="")]
    monkeypatch.setattr(main, "KBClient", FakeKB)


def _save_during_pick(main, monkeypatch, user, idx, label, annotations):
    """Make a human save of row ``idx`` land while auto-annotate picks for
    ``label`` (the KB calls and the pick run outside the write lock)."""
    real = main._pick_best
    done = []

    def pick(dense, lexical, entity_type, threshold):
        best = real(dense, lexical, entity_type, threshold)
        if best is not None and best.id == f"C_{label.upper()}" and not done:
            done.append(1)
            main.save_row("s1", idx, main.AnnotateRequest(annotations=annotations), user=user)
        return best
    monkeypatch.setattr(main, "_pick_best", pick)


def test_auto_annotate_skips_row_saved_by_a_human_during_the_run(world, as_user, monkeypatch):
    import api.main as main
    _fake_kb(main, monkeypatch)
    alice = world["alice"]
    _save_during_pick(main, monkeypatch, alice, 1, "flu", {"UMLS": "C_HUMAN"})

    r = as_user(alice).post("/api/sessions/s1/auto-annotate")
    assert r.status_code == 200, r.text
    assert (r.json()["annotated"], r.json()["skipped"]) == (2, 1)
    assert _layer(world["root"], "s1", alice)["UMLS"].tolist() == ["C_COLD", "C_HUMAN", "C_VIRUS"]
    assert [d.actor for d in decisions.list_decisions("s1") if d.row_idx == 1] == ["human"]
    latest = decisions.latest_human_or_script("s1")[(alice.id, 1)]
    assert (latest.actor, latest.kb_values) == ("human", {"UMLS": "C_HUMAN"})


def test_auto_annotate_writes_only_kbs_still_blank(auth_env, make_user, as_user, monkeypatch):
    import api.main as main
    _make_session(auth_env, "s1", kbs=[
        {"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query"},
        {"name": "MESH", "endpoint_url": "http://127.0.0.1:8766/query"}])
    alice = make_user("alice@x.org", name="Alice")
    userdb.add_member("s1", alice.id)
    _fake_kb(main, monkeypatch)
    # while UMLS is being picked for row 0, the human saves MESH on that row
    _save_during_pick(main, monkeypatch, alice, 0, "cold", {"MESH": "M_HUMAN"})

    assert as_user(alice).post("/api/sessions/s1/auto-annotate").status_code == 200
    layer = _layer(auth_env, "s1", alice)
    assert (layer.at[0, "UMLS"], layer.at[0, "MESH"]) == ("C_COLD", "M_HUMAN")
    auto_row0 = [d for d in decisions.list_decisions("s1")
                 if d.row_idx == 0 and d.actor == "auto_annotate"]
    # every decision holds the row's complete state across all session KBs
    assert [(d.kb_values, d.previous) for d in auto_row0] == [
        ({"UMLS": "C_COLD", "MESH": "M_HUMAN"}, {"UMLS": "", "MESH": "M_HUMAN"})]
    human_row0 = [d for d in decisions.list_decisions("s1")
                  if d.row_idx == 0 and d.actor == "human"]
    assert [(d.kb_values, d.previous) for d in human_row0] == [
        ({"UMLS": "", "MESH": "M_HUMAN"}, {"UMLS": "", "MESH": ""})]


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t"])
def test_decisions_csv_neutralises_formulas(world, as_user, make_user, prefix):
    evil = make_user("evil@x.org", name=f"{prefix}HYPERLINK(1)")
    userdb.add_member("s1", evil.id)
    _save(as_user(evil), "C1", llm_used=f"{prefix}1+1")
    r = as_user(world["admin"]).get("/api/admin/decisions.csv?session=s1")
    (row,) = [dict(zip(CSV_HEADER, cells)) for cells in csv.reader(io.StringIO(r.text))][1:]
    assert row["llm_used"] == f"'{prefix}1+1"
    assert row["user_name"] == f"'{prefix}HYPERLINK(1)"
    assert row["user_id"] == str(evil.id) and row["row_idx"] == "0"
    assert json.loads(row["kb_values"]) == {"UMLS": "C1"}


def test_numeric_looking_llm_used_round_trips_as_text(world, as_user):
    _save(as_user(world["alice"]), "C1", llm_used="123")
    (d,) = decisions.list_decisions("s1")
    assert d.llm_used == "123"
