"""The search server can load and serve a plain (adapter-free) index."""
import os, sys
import warnings
from argparse import Namespace
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kb"))

with warnings.catch_warnings():  # FAISS's SWIG bindings warn on import
    warnings.filterwarnings("ignore", "builtin type [Ss]wig", DeprecationWarning)
    import faiss
import numpy as np
import pytest

import search_sapbert_index as ssi


class FakeModel:                      # a plain model: no adapter_summary()
    def to(self, device): return self
    def eval(self): return self
    def parameters(self): return []


@pytest.mark.parametrize("config, revision", [({"model_revision": "abc123"}, "abc123"), ({}, None)])
def test_plain_model_loads_and_pins_revision(monkeypatch, config, revision):
    calls = []
    def tok(name, **kw): calls.append((name, kw.get("revision"))); return object()
    def mod(name, **kw): calls.append((name, kw.get("revision"))); return FakeModel()
    monkeypatch.setattr(ssi.AutoTokenizer, "from_pretrained", tok)
    monkeypatch.setattr(ssi.AutoModel, "from_pretrained", mod)
    s = ssi.SAPBERTIndexSearcher()
    s.index_config = {"model_name": "m", "use_adapter": False, **config}
    s._load_model()
    assert calls == [("m", revision), ("m", revision)]


def searcher_with(ids_scores, **config):
    s = ssi.SAPBERTIndexSearcher()
    vecs = np.array([[sc, np.sqrt(1 - sc * sc), 0, 0] for _, sc in ids_scores], dtype=np.float32)
    s.index = faiss.IndexFlatIP(4)
    s.index.add(vecs)
    s.metadata = [{"entity_id": i, "all_aliases": [i]} for i, _ in ids_scores]
    s.index_config = {"max_length": 25, **config}
    s._load_model = lambda: None
    s._generate_single_embedding = lambda *a: np.array([1, 0, 0, 0], dtype=np.float32)
    return s


ROWS = [("A", 1.0)] * 30 + [("B", 0.9), ("C", 0.8)]


def ids(results): return [r["entity_id"] for r in results]


def test_distinct_entities_fills_k():
    assert ids(searcher_with(ROWS, distinct_entities=True).search("x", k=3)) == ["A", "B", "C"]
    assert ids(searcher_with(ROWS, distinct_entities=True).search("x", k=2)) == ["A", "B"]


def test_without_flag_behaviour_is_unchanged():
    assert ids(searcher_with(ROWS).search("x", k=3)) == ["A"]


def test_distinct_stops_when_index_has_fewer_concepts():
    assert ids(searcher_with(ROWS, distinct_entities=True).search("x", k=10)) == ["A", "B", "C"]


def test_warm_up_loads_the_model_and_embeds_a_query():
    s = searcher_with(ROWS)
    events = []
    embed = s._generate_single_embedding
    s._load_model = lambda: events.append("load_model")
    s._generate_single_embedding = lambda text, *a: events.append(text) or embed(text, *a)
    s.warm_up()
    assert events == ["load_model", "warm up"]


def test_server_warms_up_before_serving(monkeypatch, tmp_path):
    import waitress
    from kb import server
    events = []

    class Fake:
        metadata, use_adapter = [], False
        def __init__(self, **kw): pass
        def load_index(self, path): events.append("load_index"); return {}
        def warm_up(self): events.append("warm_up")

    cfg = tmp_path / "c.yaml"
    cfg.write_text("lexical:\n  enabled: false\n")
    monkeypatch.setattr(server, "SAPBERTIndexSearcher", Fake)
    monkeypatch.setattr(server, "_parse_args", lambda: Namespace(config=str(cfg)))
    monkeypatch.setattr(server, "_resolve_config", lambda a: dict(
        index_path="x", adapter_path=None, adapter_name=None, host="127.0.0.1", port=1))
    monkeypatch.setattr(waitress, "serve", lambda *a, **k: events.append("serve"))
    monkeypatch.delenv("KB_AUTH_TOKEN", raising=False)
    server.main()
    assert events == ["load_index", "warm_up", "serve"]
