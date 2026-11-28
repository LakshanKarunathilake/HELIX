# HELIX Hosted Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Host HELIX for invited evaluators on one Linux server as five Docker containers, with a small MeSH disease index, a matching graph and a ready demo session.

**Architecture:** The existing hosted deployment (`deploy/hosted`: caddy, web, api) gains `kb` and `neo4j` containers that publish no ports. Three scripts build the demo data from NLM's MeSH descriptor file and run inside the containers. Small changes let the search server load a plain SapBERT index.

**Tech Stack:** FastAPI, Flask + waitress, FAISS, transformers, Neo4j 5 Community, Docker Compose, React, pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-tool-kit-design.md`

## Global Constraints

- Demo data comes only from NLM's MeSH 2026 descriptor file. Never read UMLS files; never use `graph/build_mesh_graph.py` here.
- Scope: descriptors with a tree number starting `C`.
- Index: flat (exact) FAISS, model `cambridgeltl/SapBERT-from-PubMedBERT-fulltext`, no adapter, one pinned revision, `max_length` 25.
- Indexes whose config lacks `distinct_entities` behave exactly as before.
- `kb` and `neo4j` publish no ports. Only `caddy` publishes (80, 443).
- Allow-list entries: `kb:8765`, `neo4j:7687`. Accounts stay invite-only (`HELIX_AUTH=1`, `HELIX_HOSTED=1`).
- Attribution shown to evaluators: includes "Courtesy of the U.S. National Library of Medicine", names MeSH 2026, says the data may not be the most current, does not imply NLM endorsement.
- Release 1 target: x86 Linux, CPU only.
- No file download from NLM, no image or package download for a build, and nothing on the owner's server without the owner's go-ahead at that time.
- Branch `feature/hosted-demo` from `main` **after pull request 7 is merged**. If it is not merged, stop and ask. Commit the spec, this plan and `docs/architecture/` first.
- Every commit message ends with the session's `Co-Authored-By` attribution line.
- Tests that import `kb/` modules set `KMP_DUPLICATE_LIB_OK=TRUE` before importing faiss or torch. Tests download no model.

## Review Focus

1. Re-running the seed script after evaluators have saved answers: input rows and answers stay byte-identical (Task 5).
2. Re-running the graph builder: same node and relationship counts, no duplicates (Task 4).
3. An index build that fails part-way: the server must not load a mix of old and new files (Task 3).
4. A distinct-concept search on an index with fewer than `k` concepts: returns what exists and terminates (Task 1).
5. A missing or short Neo4j password: a clear message, not a driver traceback (Tasks 4 and 5).

---

### Task 1: Search server loads a plain index

**Files:**
- Modify: `kb/search_sapbert_index.py` (`_load_model`, `search`; add `warm_up`)
- Modify: `kb/server.py` (`main`)
- Test: `tests/test_kb_search.py`

**Interfaces:**
- Produces: index config keys `model_revision: str | None` (passed as `revision=` to both `from_pretrained` calls) and `distinct_entities: bool`; `SAPBERTIndexSearcher.warm_up() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
import os, sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kb"))

import faiss
import numpy as np
import pytest
import torch

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
```

- [ ] **Step 2: Run** `pytest tests/test_kb_search.py -v` — expect failures on `adapter_summary`, revision, distinct and `warm_up` (the "unchanged behaviour" test passes).
- [ ] **Step 3: Implement.** Call `adapter_summary()` only when `self.use_adapter`. With `distinct_entities`, request `min(4*k, ntotal)` neighbours and widen fourfold until `k` concepts or `ntotal` is reached, then cut to `k` and rank. `warm_up` runs `self.search("warm up", k=1)`; `main` calls it right after `load_index`.
- [ ] **Step 4: Run** `pytest tests/test_kb_search.py tests/test_kb_auth.py -v` — all pass.
- [ ] **Step 5: Commit** `fix(kb): load plain indexes, pin revision, distinct concepts, warm up`

---

### Task 2: MeSH descriptor reader

**Files:**
- Create: `eltool/mesh.py`
- Create: `tests/fixtures/mesh_mini.xml`
- Test: `tests/test_mesh.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class Descriptor:
    ui: str
    name: str                       # preferred name
    terms: tuple[str, ...]          # name first, then entry terms; case-insensitively unique
    tree_numbers: tuple[str, ...]
    definition: str                 # preferred concept's ScopeNote, stripped, "" if none
    see_also: tuple[str, ...]       # SeeRelatedDescriptor UIs

def read_descriptors(path: Path, branch: str = "C") -> Iterator[Descriptor]
```

Reads `.xml` or `.gz` with `iterparse`, clearing each record. Element paths: `DescriptorRecord/DescriptorUI`, `DescriptorName/String`, `TreeNumberList/TreeNumber`, `ConceptList/Concept[@PreferredConceptYN='Y']/ScopeNote`, `ConceptList/Concept/TermList/Term/String`, `SeeRelatedList/SeeRelatedDescriptor/DescriptorReferredTo/DescriptorUI`.

- [ ] **Step 1: Write the fixture** with a DOCTYPE line like the real file and four records: `D012140` "Respiratory Tract Diseases" (`C08`); `D001982` "Bronchial Diseases" (`C08.127`); `D001249` "Asthma" (`C08.127.108`, ScopeNote with surrounding whitespace, terms `Asthma`, `Bronchial Asthma`, `bronchial asthma` split over two concepts, see-related `D001982`); `D000001` "Calcimycin" (`D03.633` only).
- [ ] **Step 2: Write the failing tests**

```python
FIX = Path(__file__).parent / "fixtures" / "mesh_mini.xml"

def test_reads_only_the_branch():
    assert [d.ui for d in read_descriptors(FIX)] == ["D012140", "D001982", "D001249"]

def test_descriptor_fields():
    asthma = {d.ui: d for d in read_descriptors(FIX)}["D001249"]
    assert asthma.name == "Asthma"
    assert asthma.terms == ("Asthma", "Bronchial Asthma")
    assert asthma.tree_numbers == ("C08.127.108",)
    assert asthma.definition == asthma.definition.strip() != ""
    assert asthma.see_also == ("D001982",)

def test_reads_gzip(tmp_path):
    gz = tmp_path / "d.gz"
    gz.write_bytes(gzip.compress(FIX.read_bytes()))
    assert len(list(read_descriptors(gz))) == 3
```

- [ ] **Step 3: Run** `pytest tests/test_mesh.py -v` — fails (module missing).
- [ ] **Step 4: Implement `eltool/mesh.py`**, run again — pass.
- [ ] **Step 5: Commit** `feat(demo): read MeSH disease descriptors`

---

### Task 3: Demo index builder

**Files:**
- Create: `scripts/build_demo_index.py`
- Test: `tests/test_build_demo_index.py`

**Interfaces:**
- Consumes: `read_descriptors`, Task 1 config keys.
- Produces:

```python
MODEL_NAME = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
MODEL_REVISION: str          # 40-hex commit, looked up in Step 1
MAX_LENGTH = 25
def entries(descriptors) -> list[dict]
def build_index(descriptors, embed: Callable[[list[str]], np.ndarray], out_prefix: Path,
                *, mesh_version: str, batch_size: int = 64) -> dict   # the written config
def sapbert_embedder(model_name: str, revision: str, max_length: int) -> Callable
def main(argv: list[str] | None = None) -> None      # --mesh --out --mesh-version(2026) --batch-size
```

Metadata, one per term: `entity_id`, `primary_alias` (preferred name), `all_aliases` (list of terms), `original_aliases` (`"; ".join`), `processed_text`, `index_id`, `semantic_groups` `""`, `semantic_types` `""`, `definition`. Config: `index_type` `"Flat"`, `model_name`, `model_revision`, `use_adapter` False, `adapter_name`/`adapter_path` None, `embedding_dim`, `num_entities` (entry count), `max_length` 25, `normalized` True, `metric` `"inner_product"`, `pooling` `"cls"`, `distinct_entities` True, `mesh_version`, `created_at`.

- [ ] **Step 1: Look up the revision and licence** from `https://huggingface.co/api/models/cambridgeltl/SapBERT-from-PubMedBERT-fulltext` (`sha`, `cardData.license` or the licence tag). Put `sha` in `MODEL_REVISION`; report the licence. If there is none, or it forbids this use, stop and ask.
- [ ] **Step 2: Write the failing tests.** Use the import preamble of Task 1 (`ssi`) and `FIX` from Task 2. `fake_embed(texts)` returns a float32 one-hot row of dimension 16 per text, position assigned on first sight (same text, same vector).

```python
def test_build_writes_a_loadable_index(tmp_path):
    prefix = tmp_path / "mesh-diseases"
    cfg = build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026", batch_size=2)
    assert (cfg["index_type"], cfg["use_adapter"], cfg["distinct_entities"]) == ("Flat", False, True)
    assert (cfg["model_revision"], cfg["mesh_version"], cfg["max_length"]) == (MODEL_REVISION, "2026", 25)
    s = ssi.SAPBERTIndexSearcher()
    s.load_index(str(prefix))
    s._load_model = lambda: None
    s._generate_single_embedding = lambda text, n: fake_embed([text])[0]
    top = s.search("Bronchial Asthma", k=3)[0]
    assert (top["entity_id"], top["primary_alias"], top["processed_text"]) == \
           ("D001249", "Asthma", "Bronchial Asthma")
    assert cfg["num_entities"] == len(s.metadata) == s.index.ntotal

def test_failed_rebuild_leaves_nothing_loadable(tmp_path):
    prefix = tmp_path / "mesh-diseases"
    build_index(read_descriptors(FIX), fake_embed, prefix, mesh_version="2026")
    def boom(texts): raise RuntimeError("stop")
    with pytest.raises(RuntimeError):
        build_index(read_descriptors(FIX), boom, prefix, mesh_version="2026")
    assert not Path(f"{prefix}_config.json").exists()
    assert not list(tmp_path.glob("*.tmp"))

def test_empty_input_is_refused(tmp_path):
    with pytest.raises(ValueError):
        build_index([], fake_embed, tmp_path / "x", mesh_version="2026")
```

- [ ] **Step 3: Run** `pytest tests/test_build_demo_index.py -v` — fails.
- [ ] **Step 4: Implement.** Embed and `index.add` batch by batch, L2-normalised; `sapbert_embedder` takes the CLS vector with `padding="max_length"`, matching the searcher. Remove an existing config first, write each file as `*.tmp`, rename, config last. Put the repo root on `sys.path`.
- [ ] **Step 5: Run tests — pass. Commit** `feat(demo): build the MeSH disease index`

---

### Task 4: Demo graph builder

**Files:**
- Create: `scripts/build_demo_graph.py`
- Test: `tests/test_build_demo_graph.py`

**Interfaces:**
- Consumes: `read_descriptors`.
- Produces:

```python
def graph_rows(descriptors) -> tuple[list[dict], list[dict]]   # {"cui","concept"}, {"start","end","rel"}
def load_graph(driver, nodes, rels, *, batch: int = 5000) -> tuple[int, int]
def main(argv=None) -> None    # --mesh, --uri (bolt://neo4j:7687), --user (neo4j); env NEO4J_PASSWORD
```

Schema is HELIX's default: `(:Concept {cui, concept})-[:RELATES_TO {rel}]->()`, `cui` = descriptor UI. A parent is the descriptor owning the tree number minus its last part. Per pair: child→parent `rel="parent"`, parent→child `rel="child"`. See-related: one `rel="see_related"` per unordered pair, both ends in the node set.

- [ ] **Step 1: Write the failing tests**

```python
def test_rows_from_hierarchy_and_see_also():
    nodes, rels = graph_rows(read_descriptors(FIX))
    assert {"cui": "D001249", "concept": "Asthma"} in nodes and len(nodes) == 3
    assert {(r["start"], r["rel"], r["end"]) for r in rels} == {
        ("D001982", "parent", "D012140"), ("D012140", "child", "D001982"),
        ("D001249", "parent", "D001982"), ("D001982", "child", "D001249"),
        ("D001249", "see_related", "D001982")}
```

Add, with a fake driver that records every query and its parameters:
- `test_load_clears_then_creates`: order is uniqueness constraint on `:Concept(cui)`, delete in limited batches until a batch deletes 0, node creation, relationship creation; returns `(3, 5)`.
- `test_second_load_issues_the_same_queries` (running twice never creates without clearing first).
- `test_password_required`: `main` with `NEO4J_PASSWORD` unset, and set to `"short"`, raises `SystemExit` naming `NEO4J_PASSWORD` and the 8-character minimum, before any driver is made.

- [ ] **Step 2: Run** `pytest tests/test_build_demo_graph.py -v` — fails.
- [ ] **Step 3: Implement** with `UNWIND $rows` batches. Wait up to 120 s for Neo4j to accept connections, then give a one-line error.
- [ ] **Step 4: Run tests — pass. Commit** `feat(demo): build the MeSH disease graph`

---

### Task 5: Demo session seeding

**Files:**
- Create: `scripts/seed_demo_session.py`
- Create: `scripts/demo_data/documents.json`
- Test: `tests/test_seed_demo_session.py`

**Interfaces:**
- Produces:

```python
class SeedError(RuntimeError): ...
def load_documents() -> list[dict]                 # {"id","text","mentions":[str]}
def build_rows(documents) -> list[dict]            # {"mention","document_id","span"}; span "start-end", end exclusive
def seed(documents, *, graph_password: str, kb_url="http://kb:8765/query",
         graph_uri="bolt://neo4j:7687", update_config=False) -> Path      # the session folder
def main(argv=None) -> None                        # --update-config; env NEO4J_PASSWORD
```

Session `demo`: annotator `demo`; `input.csv`; `label_column="mention"`, `doc_id_col="document_id"`, `span_col="span"`; context in `context/<id>.txt`; one KB `MeSH` with `response_path="results"`; `enable_graph=True` with a `GraphConnection` (user `neo4j`); LLM re-rank off. Paths come from `eltool.session.session_dir`.

- [ ] **Step 1: Write the texts.** Original ASCII text written for this demo: 8–12 documents, 40–60 mentions of diseases, including at least 5 abbreviations (such as COPD, MI) and some ambiguous names. Each mention occurs verbatim in its document.
- [ ] **Step 2: Write the failing tests** (set `eltool.session.SESSIONS_DIR` to `tmp_path`):

```python
def test_demo_texts():
    docs = load_documents()
    mentions = [m for d in docs for m in d["mentions"]]
    assert 8 <= len(docs) <= 12 and 40 <= len(mentions) <= 60
    assert all(m in d["text"] and d["text"].isascii() for d in docs for m in d["mentions"])
    assert len({m for m in mentions if re.fullmatch(r"[A-Z]{2,6}", m)}) >= 5

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

def test_reseed_never_touches_answers(sessions):
    path = seed(load_documents(), graph_password="password-1")
    (path / "annotations").mkdir()
    (path / "annotations" / "7.csv").write_text("kept")
    before = {p: p.read_bytes() for p in path.rglob("*") if p.is_file()}
    with pytest.raises(SeedError):
        seed(load_documents(), graph_password="password-2")
    assert before == {p: p.read_bytes() for p in path.rglob("*") if p.is_file()}
    seed([], graph_password="password-2", update_config=True)
    changed = [p for p, b in before.items() if p.read_bytes() != b]
    assert changed == [path / "session.json"]

@pytest.mark.parametrize("password", ["", "short"])
def test_seed_needs_a_usable_password(sessions, password):
    with pytest.raises(SeedError, match="NEO4J_PASSWORD"):
        seed(load_documents(), graph_password=password)
    assert not (sessions / "demo").exists()
```

- [ ] **Step 3: Run** `pytest tests/test_seed_demo_session.py -v` — fails.
- [ ] **Step 4: Implement**; `main` prints `SeedError` as one line and exits non-zero.
- [ ] **Step 5: Run tests — pass. Commit** `feat(demo): seed the demo session`

---

### Task 6: Show the data source to evaluators

**Files:**
- Modify: `api/main.py` (`get_info`), `web/src/types/index.ts` (`AppInfo`), `web/src/App.tsx`
- Create: `web/src/components/SourceNotice.tsx`
- Test: `tests/test_source_notice.py`

**Interfaces:**
- Produces: `GET /api/info` adds `notice: str | None` from `HELIX_NOTICE` (stripped; empty is null). `SourceNotice` renders a small footer on every page, sign-in included, only when `notice` is set.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("value, expected", [(None, None), ("  ", None), (" MeSH 2026 ", "MeSH 2026")])
def test_info_notice(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("HELIX_NOTICE", raising=False)
    else:
        monkeypatch.setenv("HELIX_NOTICE", value)
    assert TestClient(app).get("/api/info").json()["notice"] == expected
```

- [ ] **Step 2: Run** — fails. **Step 3: Implement** (`useQuery({queryKey: ['info'], queryFn: getInfo, staleTime: Infinity})`).
- [ ] **Step 4: Run** the test (pass) and `npm run build --prefix web` (clean). Check in the browser preview with the variable set: footer on the home and sign-in pages, no sideways scroll at 375 px.
- [ ] **Step 5: Commit** `feat(web): show a data source notice`

---

### Task 7: Reproducible container images

**Files:**
- Create: `kb/config.container.yaml`, `requirements-api.lock`, `requirements-kb.lock`
- Modify: `docker/Dockerfile.api`, `docker/Dockerfile.kb`, `docker/Dockerfile.web`, `.dockerignore`, `.gitignore`
- Test: `tests/test_container_files.py`

**Interfaces:**
- Produces: images that contain `scripts/`; `kb` reads a neutral `/app/kb/config.yaml` (host `0.0.0.0`, port 8765, index `/data/index/mesh-diseases`, adapter `path`/`name` null, lexical off). The local `docker-compose.yml` still mounts the user's own config over it.

- [ ] **Step 1: Write the failing tests** — assert: every `FROM` line in the three Dockerfiles has `@sha256:`; `Dockerfile.kb` has `COPY kb/config.container.yaml ./kb/config.yaml`, installs torch from `https://download.pytorch.org/whl/cpu`, and installs `requirements-kb.lock`; `Dockerfile.api` installs `requirements-api.lock`; both copy `scripts/`; the container config has no `/Users/` and a null adapter; `.dockerignore` lists `kb/config.yaml`, `**/.env`, `deploy/hosted/demo-index`, `deploy/hosted/mesh`; each non-comment lock line is `name==version`; no lock line contains `+cu` or starts with `nvidia`.
- [ ] **Step 2: Run** `pytest tests/test_container_files.py -v` — fails.
- [ ] **Step 3: Ask the owner for the go-ahead** to pull the base images and Python packages (name them and say the torch stack is several hundred MB).
- [ ] **Step 4: Pin and lock.** Digests from `docker buildx imagetools inspect <tag>` (`python:3.11-slim`, `node:20-alpine`, `nginx:alpine`). Generate each lock with `pip freeze` inside a `linux/amd64` container of the pinned Python image (kb: CPU torch first), dropping the `+cpu` suffix. `Dockerfile.kb` installs the locked torch version from the CPU index, then the lock file.
- [ ] **Step 5: Run** the tests (pass), then `docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env.example build api web` and `docker build --platform linux/amd64 -f docker/Dockerfile.kb -t helix-kb:check .` — both succeed; `docker run --rm helix-kb:check pip list` shows no `nvidia-` package.
- [ ] **Step 6: Commit** `build: pin images, lock versions, CPU torch, neutral kb config`

---

### Task 8: Compose services and documentation

**Files:**
- Modify: `deploy/hosted/docker-compose.yml`, `deploy/hosted/.env.example`, `deploy/hosted/README.md`
- Test: `tests/test_hosted_compose.py`

**Interfaces:**
- Produces: services `kb` (env `KB_INDEX_PATH=/data/index/mesh-diseases`; volumes `${DEMO_INDEX_DIR:-./demo-index}:/data/index:ro`, `hf_cache:/root/.cache/huggingface`) and `neo4j` (a pinned `neo4j:5` community tag with digest; `NEO4J_AUTH=neo4j/${NEO4J_PASSWORD:?...}`; heap max 1G; volume `neo4j_data`). `api` adds `HELIX_URL_ALLOWLIST: kb:8765,neo4j:7687,${HELIX_URL_ALLOWLIST:-}`, `NEO4J_PASSWORD`, and `HELIX_NOTICE` defaulting to: "Demo data: Medical Subject Headings (MeSH) 2026. Courtesy of the U.S. National Library of Medicine. It may not reflect the most current data available from NLM. NLM does not endorse this service."

- [ ] **Step 1: Write the failing tests** (`yaml.safe_load` the compose file): services are exactly `api, web, caddy, kb, neo4j`; only `caddy` has `ports`; the allow-list starts `kb:8765,neo4j:7687`; the index volume ends `:ro`; the neo4j image contains `community` and `@sha256:`; the notice contains "Courtesy of the U.S. National Library of Medicine", "2026", "most current" and "does not endorse"; `.env.example` has `NEO4J_PASSWORD=` and `DEMO_INDEX_DIR=`.
- [ ] **Step 2: Run** — fails. **Step 3: Implement.**
- [ ] **Step 4: Write the README** section "Demo data", in this order: set `NEO4J_PASSWORD` (8+ characters); download `desc2026.gz` from <https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/> into `deploy/hosted/mesh/`; build images; build the index with `compose run --rm --no-deps` on `kb`, mounting `mesh` read-only and `demo-index` read-write at `/data/out` (`--out /data/out/mesh-diseases`); `up -d`; `compose run --rm` on `api` for the graph builder (mesh mounted), then the seed script; invite evaluators to session `demo`. Update the intro (five containers), server size (to be measured), the smoke checklist (no published port beyond 80/443) and the note that a changed Neo4j password needs `--update-config`.
- [ ] **Step 5: Run** the tests and `NEO4J_PASSWORD=check-only docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env.example config -q` (the example file leaves the password empty on purpose) — pass. Run the full `pytest` — pass.
- [ ] **Step 6: Commit** `feat(deploy): add search, graph and demo data to the hosted server`

---

### Task 9: Run it for real

Needs the owner: the go-ahead for each download, a Firebase project, and the Linux server.

- [ ] **Step 1: Ask** for the go-ahead to download `desc2026.gz` (state its size from the server first), the Neo4j image and the model.
- [ ] **Step 2: Local trial** on this Mac with `HELIX_DOMAIN=localhost`, following the README. If the MeSH element paths differ from Task 2, fix the reader with a fixture that shows the difference.
- [ ] **Step 3: Check and record:** descriptor and entry counts; index size; build time; `docker stats --no-stream` memory; `ps` shows ports only on `caddy`; from the `api` container every demo mention returns 10 distinct candidates and the graph has the expected counts. List mentions whose top candidates look wrong and fix the demo texts if a term is not in MeSH's disease branch.
- [ ] **Step 4: On the x86 server** (owner's go-ahead), follow the README from a clean checkout. Owner signs in, invites an evaluator; the evaluator sees candidates and graph information and saves; a reviewer decides and exports. Confirm ports 8765 and 7687 are closed from outside and that no UMLS file is on the server.
- [ ] **Step 5: Write measured memory and disk needs** into the README and commit `docs(hosted): measured server size`. Anything not run is stated as not run.
