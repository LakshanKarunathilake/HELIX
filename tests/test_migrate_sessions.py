import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from eltool.config import SessionConfig

_spec = importlib.util.spec_from_file_location(
    "migrate_sessions_for_hosting",
    Path(__file__).resolve().parent.parent / "scripts" / "migrate_sessions_for_hosting.py",
)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

SECRET = "sk-ant-SECRET-VALUE-123"


def make_session(src, name, *, ctx=None, endpoints=("http://127.0.0.1:8765/query",),
                 api_key=None, graph=None, input_name=None, raw=None):
    d = src / name
    d.mkdir(parents=True)
    input_name = input_name or f"input_{name}.tsv"
    (d / input_name).write_text("label\nfoo\n")
    (d / "annotated.csv").write_text("a,b\n")
    if raw is not None:
        (d / "session.json").write_text(raw)
        return d
    cfg = {
        "session_name": name,
        "annotator": "me",
        "input_path": f"/Users/someone/sessions/{name}/{input_name}",
        "delimiter": "\t",
        "label_column": "label",
        "kbs": [
            {"name": f"kb{i}", "endpoint_url": e, "headers": '{"Authorization": "Bearer HDR-SECRET"}'}
            for i, e in enumerate(endpoints)
        ],
        "context_folder": str(ctx) if ctx else None,
        "llm_api_key": api_key,
    }
    if graph:
        cfg["graph"] = graph
    (d / "session.json").write_text(json.dumps(cfg))
    return d


def digest(root):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def test_rewrites_paths_and_endpoints(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make_session(src, "s1")
    warnings = mig.migrate(src, dest, {"http://127.0.0.1:8765": "https://kb.example.org"})
    cfg = SessionConfig.load(dest / "s1" / "session.json")
    assert cfg.input_path == str(dest / "s1" / "input_s1.tsv")
    assert (dest / "s1" / "input_s1.tsv").exists()
    assert cfg.kbs[0].endpoint_url == "https://kb.example.org/query"
    assert warnings == ["session s1: stores KB request headers"]
    assert (dest / "s1" / "annotated.csv").exists()


def test_copies_context_texts(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "123.txt").write_text("hello")
    (ctx / "456").write_text("bare")
    (ctx / "skip.json").write_text("{}")
    src, dest = tmp_path / "src", tmp_path / "dest"
    make_session(src, "s1", ctx=ctx)
    mig.migrate(src, dest, {"http://127.0.0.1:8765": "https://kb.example.org"})
    cfg = SessionConfig.load(dest / "s1" / "session.json")
    assert cfg.context_folder == str(dest / "s1" / "context")
    c = dest / "s1" / "context"
    assert (c / "123.txt").read_text() == "hello"
    assert (c / "456").read_text() == "bare"
    assert not (c / "skip.json").exists()


def test_unmapped_endpoint_warns(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make_session(src, "s1", endpoints=("http://127.0.0.1:8766/query",))
    warnings = mig.migrate(src, dest, {"http://127.0.0.1:8765": "https://kb.example.org"})
    assert any("s1" in w and "8766" in w for w in warnings)
    cfg = SessionConfig.load(dest / "s1" / "session.json")
    assert cfg.kbs[0].endpoint_url == "http://127.0.0.1:8766/query"


def test_missing_context_folder_warns(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make_session(src, "s1", ctx=tmp_path / "nope")
    warnings = mig.migrate(src, dest, {"http://127.0.0.1:8765": "https://x"})
    assert any("s1" in w and "context" in w for w in warnings)
    assert SessionConfig.load(dest / "s1" / "session.json").context_folder is None


def test_refuses_existing_dest(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    with pytest.raises(FileExistsError):
        mig.migrate(src, dest, {})


def test_secrets_never_printed(tmp_path, capsys):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make_session(src, "s1", api_key=SECRET,
                 graph={"uri": "bolt://localhost:7687", "password": "GRAPH-PW-SECRET"})
    warnings = mig.migrate(src, dest, {"http://127.0.0.1:8765": "https://x"})
    assert any("s1" in w and "LLM API key" in w for w in warnings)
    text = "\n".join(warnings)
    for s in (SECRET, "HDR-SECRET", "GRAPH-PW-SECRET"):
        assert s not in text
    rc = mig.main(["--src", str(src), "--dest", str(tmp_path / "d2"),
                   "--kb-map", "http://127.0.0.1:8765=https://x"])
    out = capsys.readouterr()
    assert rc == 0
    for s in (SECRET, "HDR-SECRET", "GRAPH-PW-SECRET"):
        assert s not in out.out + out.err


def test_localhost_graph_warns(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make_session(src, "s1", graph={"uri": "bolt://127.0.0.1:7687"})
    make_session(src, "s2", graph={"uri": "neo4j+s://db.example.org:7687"})
    warnings = mig.migrate(src, dest, {"http://127.0.0.1:8765": "https://x"})
    assert any("s1" in w and "graph" in w for w in warnings)
    assert not any("s2" in w and "graph" in w for w in warnings)
    assert SessionConfig.load(dest / "s1" / "session.json").graph.uri == "bolt://127.0.0.1:7687"


def test_invalid_session_warns_and_continues(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    make_session(src, "bad", raw='{"session_name": "bad"}')
    make_session(src, "good")
    (src / "nojson").mkdir()
    (src / "nojson" / "x.txt").write_text("x")
    warnings = mig.migrate(src, dest, {"http://127.0.0.1:8765": "https://x"})
    assert any("bad" in w for w in warnings)
    assert not any("nojson" in w for w in warnings)
    assert (dest / "nojson" / "x.txt").exists()
    assert (dest / "bad" / "session.json").read_text() == '{"session_name": "bad"}'
    assert SessionConfig.load(dest / "good" / "session.json").kbs[0].endpoint_url.startswith("https://x")


def test_src_unchanged(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "1.txt").write_text("t")
    src, dest = tmp_path / "src", tmp_path / "dest"
    make_session(src, "s1", ctx=ctx, api_key=SECRET)
    before = digest(src)
    mig.migrate(src, dest, {"http://127.0.0.1:8765": "https://x"})
    assert digest(src) == before


def test_bad_kb_map_is_cli_error(tmp_path):
    with pytest.raises(SystemExit) as e:
        mig.main(["--src", str(tmp_path), "--dest", str(tmp_path / "d"), "--kb-map", "noequals"])
    assert e.value.code == 2


def test_kb_map_splits_on_first_equals(tmp_path):
    assert mig.parse_kb_map(["http://a:1=https://b/?x=y"]) == {"http://a:1": "https://b/?x=y"}
