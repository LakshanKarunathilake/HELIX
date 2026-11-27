"""The seed script creates the ``demo`` session that invited evaluators annotate.

Every test writes to a temporary sessions folder.
"""
import importlib.util
import re
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "seed_demo_session",
    Path(__file__).resolve().parent.parent / "scripts" / "seed_demo_session.py",
)
sds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sds)

import api.main as api_main  # noqa: E402
from eltool.config import SessionConfig  # noqa: E402
from eltool.store import load_input  # noqa: E402

SeedError, load_documents = sds.SeedError, sds.load_documents
build_rows, seed = sds.build_rows, sds.seed


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("eltool.session.SESSIONS_DIR", tmp_path)
    return tmp_path


def _snapshot(folder: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def _doc(text, mentions, doc_id="d1"):
    return {"id": doc_id, "text": text, "mentions": mentions}


# ── the demo texts ───────────────────────────────────────────────────────────

def test_demo_texts():
    docs = load_documents()
    mentions = [m for d in docs for m in d["mentions"]]
    assert 8 <= len(docs) <= 12 and 40 <= len(mentions) <= 60
    assert all(m in d["text"] and d["text"].isascii() for d in docs for m in d["mentions"])
    assert len({m for m in mentions if re.fullmatch(r"[A-Z]{2,6}", m)}) >= 5


def test_demo_spans_are_in_reading_order_and_never_overlap():
    docs = load_documents()
    ids = [d["id"] for d in docs]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"[a-z0-9\-]+", i) for i in ids)  # used as file names
    rows = build_rows(docs)
    assert [r["mention"] for r in rows] == [m for d in docs for m in d["mentions"]]
    for doc_id in ids:
        spans = [tuple(map(int, r["span"].split("-")))
                 for r in rows if r["document_id"] == doc_id]
        assert all(end <= start for (_, end), (start, _) in zip(spans, spans[1:]))


# ── build_rows ───────────────────────────────────────────────────────────────

def test_build_rows_matches_whole_words_only():
    text = "AMI and MIX are not it; MI is. COPD2 neither, but (COPD) is."
    rows = build_rows([_doc(text, ["MI", "COPD"])])
    assert rows == [
        {"mention": "MI", "document_id": "d1", "span": "24-26"},
        {"mention": "COPD", "document_id": "d1", "span": "51-55"},
    ]
    assert [text[24:26], text[51:55]] == ["MI", "COPD"]


def test_build_rows_gives_repeats_successive_occurrences_per_document():
    docs = [_doc("gout, then gout again", ["gout", "gout"]),
            _doc("more gout", ["gout"], doc_id="d2")]
    assert [(r["document_id"], r["span"]) for r in build_rows(docs)] == [
        ("d1", "0-4"), ("d1", "11-15"), ("d2", "5-9")]


@pytest.mark.parametrize("text, mentions", [
    ("no disease here", ["gout"]),
    ("only AMI here", ["MI"]),            # inside another word
    ("gout once", ["gout", "gout"]),      # listed more often than it occurs
    ("Gout", ["gout"]),                   # not verbatim
    ("any text", [""]),
])
def test_build_rows_rejects_a_mention_without_an_occurrence(text, mentions):
    with pytest.raises(SeedError, match="d1"):
        build_rows([_doc(text, mentions)])


# ── seeding ──────────────────────────────────────────────────────────────────

def test_seed_passes_hosted_checks(sessions, monkeypatch):
    path = seed(load_documents(), graph_password="s3cret pw!\"'")
    monkeypatch.setenv("HELIX_HOSTED", "1")
    monkeypatch.setenv("HELIX_URL_ALLOWLIST", "kb:8765,neo4j:7687")
    cfg = SessionConfig.load(path / "session.json")
    api_main._check_confined(cfg, "demo")
    api_main._check_hosted_urls(cfg)
    assert (cfg.kbs[0].endpoint_url, cfg.kbs[0].response_path) == ("http://kb:8765/query", "results")
    assert cfg.enable_graph and cfg.graph.uri == "bolt://neo4j:7687" and cfg.graph.password == "s3cret pw!\"'"
    for row in load_input(Path(cfg.input_path), cfg.delimiter)[0].to_dict("records"):
        text = (path / "context" / f"{row['document_id']}.txt").read_text()
        a, b = map(int, row["span"].split("-"))
        assert text[a:b] == row["mention"]


def test_seed_writes_the_demo_session(sessions):
    path = seed(load_documents(), graph_password="password-1")
    assert path == sessions / "demo"
    cfg = SessionConfig.load(path / "session.json")
    assert (cfg.session_name, cfg.annotator) == ("demo", "demo")
    assert (cfg.label_column, cfg.doc_id_col, cfg.span_col) == ("mention", "document_id", "span")
    assert [k.name for k in cfg.kbs] == ["MeSH"]
    assert (cfg.graph.user, cfg.enable_llm_rerank) == ("neo4j", False)
    df = load_input(path / "input.csv", cfg.delimiter)[0]
    assert df.to_dict("records") == build_rows(load_documents())


def test_row_endpoint_shows_the_document_and_span(sessions, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    seed(load_documents(), graph_password="password-1")
    row = api_main.get_row("demo", 0, user=None)
    start, end = map(int, row["entity_span"].split("-"))
    assert row["context_text"][start:end] == row["label"] == load_documents()[0]["mentions"][0]


def test_reseed_never_touches_answers(sessions):
    path = seed(load_documents(), graph_password="password-1")
    (path / "annotations").mkdir()
    (path / "annotations" / "7.csv").write_text("kept")
    before = _snapshot(path)
    with pytest.raises(SeedError):
        seed(load_documents(), graph_password="password-2")
    assert before == _snapshot(path)
    seed([], graph_password="password-2", update_config=True)
    changed = [p for p, b in before.items() if p.read_bytes() != b]
    assert changed == [path / "session.json"]
    assert set(_snapshot(path)) == set(before)  # nothing added or removed
    assert SessionConfig.load(path / "session.json").graph.password == "password-2"


@pytest.mark.parametrize("password", ["", "short"])
def test_seed_needs_a_usable_password(sessions, password):
    with pytest.raises(SeedError, match="NEO4J_PASSWORD"):
        seed(load_documents(), graph_password=password)
    assert not (sessions / "demo").exists()


def test_short_password_leaves_an_existing_session_alone(sessions):
    path = seed(load_documents(), graph_password="password-1")
    before = _snapshot(path)
    with pytest.raises(SeedError, match="NEO4J_PASSWORD"):
        seed(load_documents(), graph_password="short", update_config=True)
    assert before == _snapshot(path)


def test_bad_documents_leave_nothing_behind(sessions):
    with pytest.raises(SeedError):
        seed([_doc("no disease here", ["gout"])], graph_password="password-1")
    assert not (sessions / "demo").exists()


def test_a_seed_that_stops_part_way_can_be_run_again(sessions, monkeypatch):
    def disk_full(self, path):
        Path(path).write_text("{half")
        raise OSError("No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(SessionConfig, "save", disk_full)
        with pytest.raises(OSError):
            seed(load_documents(), graph_password="password-1")
    folder = sessions / "demo"
    assert not list(folder.glob("session.json*"))  # no half-written config

    path = seed(load_documents(), graph_password="password-1")
    assert SessionConfig.load(path / "session.json").session_name == "demo"


@pytest.mark.parametrize("answers", ["annotations/7.csv", "annotated.csv"])
def test_seed_keeps_input_when_answers_outlive_the_config(sessions, answers):
    path = seed(load_documents(), graph_password="password-1")
    (path / answers).parent.mkdir(exist_ok=True)
    (path / answers).write_text("kept")
    (path / "session.json").unlink()
    before = _snapshot(path)
    with pytest.raises(SeedError, match="answers"):
        seed([_doc("other gout text", ["gout"])], graph_password="password-1")
    assert before == _snapshot(path)


def test_update_config_needs_an_existing_session(sessions):
    with pytest.raises(SeedError, match="without --update-config"):
        seed(load_documents(), graph_password="password-1", update_config=True)
    assert not (sessions / "demo").exists()


def test_update_config_leaves_a_session_it_did_not_make(sessions):
    folder = sessions / "demo"
    folder.mkdir()
    (folder / "session.json").write_text("someone else's")
    before = _snapshot(folder)
    with pytest.raises(SeedError, match="input.csv"):
        seed(load_documents(), graph_password="password-1", update_config=True)
    assert before == _snapshot(folder)


# ── command line ─────────────────────────────────────────────────────────────

def test_main_seeds_then_updates_the_config(sessions, monkeypatch, capsys):
    monkeypatch.setenv("NEO4J_PASSWORD", "password-1")
    sds.main([])
    assert str(sessions / "demo") in capsys.readouterr().out
    monkeypatch.setenv("NEO4J_PASSWORD", "password-2")
    sds.main(["--update-config"])
    out = capsys.readouterr().out
    assert "session.json" in out and "password-2" not in out
    cfg = SessionConfig.load(sessions / "demo" / "session.json")
    assert cfg.graph.password == "password-2"


@pytest.mark.parametrize("password", [None, "short"])
def test_main_reports_a_missing_or_short_password_in_one_line(sessions, monkeypatch, password):
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    if password is not None:
        monkeypatch.setenv("NEO4J_PASSWORD", password)
    with pytest.raises(SystemExit) as stop:
        sds.main([])
    message = stop.value.code
    assert isinstance(message, str) and "\n" not in message  # printed, exit status 1
    assert "NEO4J_PASSWORD" in message and "short" not in message.split()
    assert not (sessions / "demo").exists()


def test_main_reports_an_existing_session_in_one_line(sessions, monkeypatch):
    monkeypatch.setenv("NEO4J_PASSWORD", "password-1")
    sds.main([])
    with pytest.raises(SystemExit) as stop:
        sds.main([])
    assert "already exists" in stop.value.code and "\n" not in stop.value.code


def test_main_reports_an_unwritable_folder_in_one_line(sessions, monkeypatch):
    monkeypatch.setenv("NEO4J_PASSWORD", "password-1")
    (sessions / "demo").write_text("a file where the folder should be")
    with pytest.raises(SystemExit) as stop:
        sds.main([])
    assert "demo" in stop.value.code and "\n" not in stop.value.code
