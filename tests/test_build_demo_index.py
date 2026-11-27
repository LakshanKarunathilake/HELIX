"""The demo index builder writes an index the search server can load."""
import importlib.util
import os, sys
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kb"))

with warnings.catch_warnings():  # FAISS's SWIG bindings warn on import
    warnings.filterwarnings("ignore", "builtin type [Ss]wig", DeprecationWarning)
    import faiss
import numpy as np
import pytest
import torch
import transformers

import search_sapbert_index as ssi

_spec = importlib.util.spec_from_file_location(
    "build_demo_index",
    Path(__file__).resolve().parent.parent / "scripts" / "build_demo_index.py",
)
bdi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bdi)

from eltool.mesh import read_descriptors  # noqa: E402  (the script puts the root on sys.path)

FIX = Path(__file__).parent / "fixtures" / "mesh_mini.xml"
MODEL_REVISION, build_index = bdi.MODEL_REVISION, bdi.build_index

_positions: dict[str, int] = {}


def fake_embed(texts):
    """One-hot per distinct text, so the fixture may hold at most 16 terms."""
    rows = np.zeros((len(texts), 16), dtype=np.float32)
    for row, text in zip(rows, texts):
        row[_positions.setdefault(text, len(_positions))] = 1
    return rows


def load(prefix):
    s = ssi.SAPBERTIndexSearcher()
    s.load_index(str(prefix))
    return s


def test_build_writes_a_loadable_index(tmp_path):
    prefix = tmp_path / "mesh-diseases"
    cfg = build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026", batch_size=2)
    assert (cfg["index_type"], cfg["use_adapter"], cfg["distinct_entities"]) == ("Flat", False, True)
    assert (cfg["model_revision"], cfg["mesh_version"], cfg["max_length"]) == (MODEL_REVISION, "2026", 25)
    s = load(prefix)
    s._load_model = lambda: None
    s._generate_single_embedding = lambda text, n: fake_embed([text])[0]
    top = s.search("Bronchial Asthma", k=3)[0]
    assert (top["entity_id"], top["primary_alias"], top["processed_text"]) == \
           ("D001249", "Asthma", "Bronchial Asthma")
    assert cfg["num_entities"] == len(s.metadata) == s.index.ntotal


def test_config_is_complete_and_matches_the_file(tmp_path):
    prefix = tmp_path / "mesh-diseases"
    cfg = build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026")
    assert load(prefix).index_config == cfg
    assert cfg.pop("created_at")
    assert cfg == {
        "index_type": "Flat",
        "model_name": "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
        "model_revision": MODEL_REVISION,
        "use_adapter": False,
        "adapter_name": None,
        "adapter_path": None,
        "embedding_dim": 16,
        "num_entities": 6,
        "max_length": 25,
        "normalized": True,
        "metric": "inner_product",
        "pooling": "cls",
        "distinct_entities": True,
        "mesh_version": "2026",
    }


def test_one_entry_per_term():
    rows = bdi.entries(read_descriptors(FIX))
    assert [r["processed_text"] for r in rows] == [
        "Respiratory Tract Diseases", "Respiratory Tract Disease",
        "Bronchial Diseases", "Bronchial Disease",
        "Asthma", "Bronchial Asthma",
    ]
    assert [r["index_id"] for r in rows] == list(range(6))
    assert rows[5] == {
        "entity_id": "D001249",
        "primary_alias": "Asthma",
        "all_aliases": ["Asthma", "Bronchial Asthma"],
        "original_aliases": "Asthma; Bronchial Asthma",
        "processed_text": "Bronchial Asthma",
        "index_id": 5,
        "semantic_groups": "",
        "semantic_types": "",
        "definition": "A disease of the airways with recurring narrowing of the bronchi.",
    }


def test_vectors_are_embedded_in_batches_and_normalised(tmp_path):
    sizes = []

    def long_embed(texts):
        sizes.append(len(texts))
        return 3 * fake_embed(texts).astype(np.float64)

    prefix = tmp_path / "x"
    build_index(read_descriptors(FIX), long_embed, prefix, mesh_version="2026", batch_size=4)
    assert sizes == [4, 2]
    index = faiss.read_index(f"{prefix}.faiss")
    np.testing.assert_allclose(np.linalg.norm(index.reconstruct_n(0, index.ntotal), axis=1), 1)


def test_missing_output_directory_is_created(tmp_path):
    prefix = tmp_path / "data" / "out" / "mesh.2026"  # a dot must not be read as a suffix
    build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026")
    assert sorted(p.name for p in prefix.parent.iterdir()) == [
        "mesh.2026.faiss", "mesh.2026_config.json", "mesh.2026_metadata.pkl",
    ]


def test_failed_rebuild_leaves_nothing_loadable(tmp_path):
    prefix = tmp_path / "mesh-diseases"
    build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026")
    def boom(texts): raise RuntimeError("stop")
    with pytest.raises(RuntimeError):
        build_index(read_descriptors(FIX), boom, prefix, mesh_version="2026")
    assert not Path(f"{prefix}_config.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_truncated_input_leaves_nothing_loadable(tmp_path):
    prefix = tmp_path / "mesh-diseases"
    build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026")
    xml = FIX.read_bytes()
    cut = tmp_path / "cut.xml"
    cut.write_bytes(xml[:xml.index(b"D001249</DescriptorUI>")])  # two whole records precede
    with pytest.raises(ET.ParseError):
        build_index(read_descriptors(cut), fake_embed, prefix, mesh_version="2026")
    with pytest.raises(FileNotFoundError):
        load(prefix)


@pytest.mark.parametrize("name", ["mesh-diseases.faiss", "mesh-diseases_metadata.pkl",
                                  "mesh-diseases_config.json"])
def test_failure_while_writing_leaves_nothing_loadable(tmp_path, monkeypatch, name):
    prefix = tmp_path / "mesh-diseases"
    build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026")
    replace = Path.replace

    def failing_replace(self, target):
        if Path(target).name == name:
            raise OSError("disk full")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    with pytest.raises(OSError):
        build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026")
    assert not Path(f"{prefix}_config.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_empty_input_is_refused(tmp_path):
    with pytest.raises(ValueError):
        build_index([], fake_embed, tmp_path / "x", mesh_version="2026")


@pytest.mark.parametrize("batch_size", [0, -1])
def test_batch_size_below_one_is_refused_before_touching_the_index(tmp_path, batch_size):
    prefix = tmp_path / "x"
    build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026")
    with pytest.raises(ValueError, match="batch_size"):
        build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026",
                    batch_size=batch_size)
    assert Path(f"{prefix}_config.json").exists()


@pytest.fixture
def one_torch_thread():
    """On macOS FAISS and torch each load an OpenMP runtime; a multi-threaded
    torch forward pass in that process segfaults."""
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(threads)


def test_embedder_pins_the_revision_and_matches_the_searcher(
        tmp_path, monkeypatch, one_torch_thread):
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("\n".join(
        ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "bronchial", "asthma", "disease"]))
    tokenizer = transformers.BertTokenizer(str(vocab))
    torch.manual_seed(0)
    model = transformers.BertModel(transformers.BertConfig(  # tiny, random, no download
        vocab_size=8, hidden_size=8, num_hidden_layers=1,
        num_attention_heads=2, intermediate_size=16))
    model.train()  # dropout stays on unless the embedder calls eval()
    calls = []

    def fake(result):
        return lambda name, **kw: calls.append((name, kw.get("revision"))) or result

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", fake(tokenizer))
    monkeypatch.setattr(transformers.AutoModel, "from_pretrained", fake(model))
    # Five tokens force the longer text to be truncated, as the searcher would.
    embed = bdi.sapbert_embedder("m", "abc123", 5)
    texts = ["asthma", "bronchial asthma disease asthma disease"]
    vectors = embed(texts)
    assert calls == [("m", "abc123"), ("m", "abc123")]
    assert vectors.shape == (2, 8)
    searcher = ssi.SAPBERTIndexSearcher()
    searcher.tokenizer, searcher.model, searcher.device = tokenizer, model, model.device
    for text, vector in zip(texts, vectors):
        np.testing.assert_allclose(
            vector, searcher._generate_single_embedding(text, 5), rtol=1e-5, atol=1e-6)


def test_progress_is_printed_every_n_batches(tmp_path, capsys):
    build_index(read_descriptors(FIX), fake_embed, tmp_path / "x", mesh_version="2026",
                batch_size=1, progress_every=2)
    assert capsys.readouterr().out.splitlines() == [
        "Embedded 2 of 6 terms", "Embedded 4 of 6 terms", "Embedded 6 of 6 terms"]


def test_progress_counts_batches_not_terms(tmp_path, capsys):
    build_index(read_descriptors(FIX), fake_embed, tmp_path / "x", mesh_version="2026",
                batch_size=4, progress_every=2)  # two batches, the second one short
    assert capsys.readouterr().out.splitlines() == ["Embedded 6 of 6 terms"]


@pytest.mark.parametrize("directory", [False, True])
def test_main_refuses_a_missing_mesh_file_before_anything_else(
        tmp_path, monkeypatch, directory):
    prefix = tmp_path / "mesh-diseases"
    build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    def loaded(*args): raise AssertionError("the model was loaded")
    monkeypatch.setattr(bdi, "sapbert_embedder", loaded)
    mesh = tmp_path if directory else tmp_path / "desc2026.gz"
    with pytest.raises(SystemExit) as stop:
        bdi.main(["--mesh", str(mesh), "--out", str(prefix)])
    message = str(stop.value)
    assert str(mesh) in message and "\n" not in message
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_main_builds_from_the_pinned_model(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(bdi, "sapbert_embedder", lambda *a: calls.append(a) or fake_embed)
    prefix = tmp_path / "mesh-diseases"
    bdi.main(["--mesh", str(FIX), "--out", str(prefix)])
    assert calls == [(bdi.MODEL_NAME, MODEL_REVISION, 25)]
    assert load(prefix).index_config["mesh_version"] == "2026"
    assert "6 terms" in capsys.readouterr().out
