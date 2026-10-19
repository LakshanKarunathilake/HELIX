# AI in the Annotation Flow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a local LLM (served from the DGX Spark) go through the human annotation protocol as an annotator (mode A), assist humans as a new study arm C4 (mode B), and pre-annotate rows so humans review only a queue (mode C).

**Architecture:** The existing OpenAI-compatible `LLMReranker` is refactored so one request path serves two output schemas: the current re-rank answer and a new *protocol* answer (entity or NIL, hint, confidence level, rationale). Batch scripts in `eval/` drive the running HELIX API to get exactly the candidates a human would see, call the model, and write rows into ordinary session folders using the existing provenance/feedback columns. Human-facing sessions only ever read pre-computed LLM answers from the cache.

**Tech Stack:** Python 3 / FastAPI / pandas / pytest; `openai` SDK against vLLM; React + TypeScript (Vite) frontend.

**Spec:** `docs/superpowers/specs/2026-10-16-ai-annotation-design.md` (Sections 6–7 define behaviour; Sections 3–5 are the DGX/connection runbook, done by hand, not by this plan).

## Global Constraints

- No AI-side code reads `eval/out/study_gold.csv` or any `gold_cui` column. Only `eval/analyze_study.py` joins gold.
- AI-written rows: `annotator = "llm:<model>"`; `_method` ∈ {`llm`, `llm_no_candidates`, `llm_auto`}. A failed model call leaves `_method` and the KB cell empty and puts the message in `_llm_error`, so the next run retries the row.
- AI hint codes = `eltool.feedback.HINTS` codes minus `external_lookup`. Confidence levels = `sure`, `fairly_sure`, `unsure`. Entity answer may be any retrieved id or `"NIL"`.
- Protocol requests use temperature `0`. The protocol cache namespace is `"helix-protocol-v1"`; bump it whenever the protocol prompt or schema changes.
- Existing re-rank cache keys must not change (the reranker's namespace is empty and adds nothing to the key).
- Sessions without the new flags keep their current column layout and behaviour (`collect_feedback`, `enable_llm_rerank`, `llm_cache_only`, `review_mode` all default off).
- Human sessions that show AI output set `llm_cache_only: true`; a cache miss degrades to the plain candidate list and never blocks.
- C4 settings: `auto_select_secs=5, enable_graph=False, enable_auto_select=True, enable_llm_rerank=True, llm_cache_only=True`. Four-condition order: `C1,C2,C3,C4,C4,C3,C2,C1`.
- C4-only hint: code `ai_suggestion`, label `"The AI suggestion and its explanation"`.
- Review routing: `auto` iff level == `sure` and pick == dense rank-1 and pick != `NIL` and not sampled; sampled share = `review_spotcheck_rate` (default `0.1`, fixed seed); else `review`.
- Local LLM base URL on the laptop: `http://127.0.0.1:18000/v1` (port 8000 is the HELIX API).

## Review Focus

1. **Prewarm and the live API build different cache keys** (context string or candidate order differ by a character) → every C4 row silently shows no AI. Expected: prewarm then a cache-only API call returns the cached pick. Test: Task 7 `test_prewarm_then_cache_only_api_hits`.
2. **Tunnel/model dies mid-run** → rerunning must skip finished rows, not redo or duplicate them. Test: Task 4 `test_rerun_skips_rows_already_written`.
3. **Model output fails validation** (unknown hint, id outside the list) → the message goes to `_llm_error`, the row stays open for a retry, the run continues. Test: Task 4 `test_model_error_is_recorded_and_run_continues`.
4. **Mention with zero retrieved candidates** → `NIL`, `_method=llm_no_candidates`, no model call. Test: Task 4 `test_no_candidates_writes_nil_without_calling_model`.
5. **Spot-check rows leak the AI answer to the human** → the row payload for a `spotcheck` row must not contain the AI pick. Test: Task 8 `test_spotcheck_rows_hide_the_ai_pick`.

---

### Task 1: Reranker core — one request path, cache namespace, temperature, cache-only

**Files:**
- Modify: `eltool/llm_reranker.py` (`__init__`, `rank`, `_call_api`/`_call_anthropic`/`_call_openai`, `_cache_key`)
- Test: `tests/test_llm_reranker_core.py`

**Interfaces:**
- Produces:
  - `LLMReranker.__init__(..., temperature: float | None = None, cache_only: bool = False)`
  - class attribute `LLMReranker.CACHE_NAMESPACE: str = ""`
  - `LLMReranker._request(user_text: str, schema: dict, tool_name: str = "select_entity") -> dict` — provider dispatch; returns the tool-call arguments as a dict; raises `LLMRerankError` on any failure. Passes `temperature` to the API only when not None.
  - `LLMReranker._cache_key(mention: str, ctx: str, cands: list[dict], extra: dict | None = None) -> str` — payload gains `"ns": CACHE_NAMESPACE` only when non-empty and `"extra": extra` only when given.
  - `LLMReranker._cached_call(key: str, call: Callable[[], dict]) -> tuple[dict, bool]` — returns (value, cached); on miss with `cache_only=True` raises `LLMRerankError("cache miss (cache_only)")`.
  - `rank()` keeps its signature and behaviour.

- [ ] **Step 1: Write the failing tests**

```python
def test_rerank_cache_key_is_unchanged(fake_cands):
    r = LLMReranker(model="m", provider="openai", base_url="http://x/v1", api_key="k")
    legacy = hashlib.sha256(json.dumps({
        "provider": "openai", "base_url": "http://x/v1", "model": "m",
        "prompt": DEFAULT_SYSTEM_PROMPT, "mention": "lung", "context": "ctx",
        "candidates": [{"id": c["id"], "label": c["label"]} for c in fake_cands],
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert r._cache_key("lung", "ctx", fake_cands) == legacy

def test_namespace_and_extra_change_the_key(fake_cands):
    class NS(LLMReranker): CACHE_NAMESPACE = "x"
    a = LLMReranker(model="m", api_key="k")._cache_key("lung", "", fake_cands)
    b = NS(model="m", api_key="k")._cache_key("lung", "", fake_cands)
    c = NS(model="m", api_key="k")._cache_key("lung", "", fake_cands, extra={"s": "C1"})
    assert len({a, b, c}) == 3

def test_cache_only_miss_raises(tmp_path, monkeypatch, fake_cands):
    monkeypatch.setattr(llm_reranker, "_CACHE_DIR", tmp_path)
    r = LLMReranker(model="m", api_key="k", cache_only=True)
    with pytest.raises(LLMRerankError, match="cache_only"):
        r.rank("lung", "", fake_cands)

def test_temperature_reaches_openai_client(monkeypatch, fake_cands):
    # fake client whose chat.completions.create records kwargs and returns one tool call
    ...
    LLMReranker(model="m", provider="openai", api_key="k", temperature=0, use_cache=False).rank("lung", "", fake_cands)
    assert captured["temperature"] == 0
    assert captured["tool_choice"]["function"]["name"] == "select_entity"
```

`fake_cands` fixture: three dicts `{"id": "C1".."C3", "label": ...}` passed through `_candidate_dict`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_llm_reranker_core.py -v`
Expected: FAIL (`CACHE_NAMESPACE`, `cache_only`, `temperature` unknown).

- [ ] **Step 3: Implement** the interfaces above. `rank()` becomes: build `cands`/`ctx`, `key = self._cache_key(...)`, `data, cached = self._cached_call(key, lambda: self._request(self._user_text(...), self._json_schema(ids)))`, then `_finalize(data, ids)`. Cache the **finalized** dict as today so existing cache files stay valid.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q tests`
Expected: all pass (70 existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add eltool/llm_reranker.py tests/test_llm_reranker_core.py
git commit -m "refactor(llm): shared request path, cache namespace, temperature, cache-only"
```

---

### Task 2: Protocol annotator (`LLMAnnotator`)

**Files:**
- Create: `eltool/llm_annotator.py`
- Test: `tests/test_llm_annotator.py`

**Interfaces:**
- Consumes: Task 1 (`_request`, `_cache_key(extra=)`, `_cached_call`, `CACHE_NAMESPACE`); `eltool.feedback.HINTS`, `CONFIDENCE`.
- Produces:
  - `PROTOCOL_VERSION = "helix-protocol-v1"`
  - `AI_HINT_CODES: list[str]` (HINTS order, without `external_lookup`)
  - `@dataclass AnnotationDecision(entity_id: str, hint: str, confidence_level: str, confidence: float, rationale: str, model: str, provider: str, latency_ms: int, cached: bool)` with `to_dict()`
  - `class LLMAnnotator(LLMReranker)`, `CACHE_NAMESPACE = PROTOCOL_VERSION`, default `temperature=0`
  - `LLMAnnotator.annotate(mention: str, context: str | None, candidates: Sequence, *, suggested_id: str | None = None, graph_evidence: str = "") -> AnnotationDecision`
  - `LLMAnnotator.reflect(decisions: list[AnnotationDecision]) -> dict` → `{"ranking": list[str], "comment": str}`; ranking covers all 9 `HINTS` codes (model ranks the 8 AI codes; `external_lookup` appended last), so it passes `feedback.validate_reflection_ranking`.

Prompt rules (constant `PROTOCOL_SYSTEM_PROMPT`): you are one annotator in a study; answer from the listed candidates or `NIL` if none fits; report the single main hint that decided it, using these codes with the human-facing labels; confidence level plus a number in [0,1]; one-sentence rationale; you have no external tools. Candidates are rendered like the UI: rank, id, label, score, types, aliases, definition; a line `Suggested by the tool: [k] <id>` only when `suggested_id` is given; a `Graph links:` section only when `graph_evidence` is non-empty. `latency_ms` is measured around `_request` (0 when cached). Cache `extra` = `{"suggested": suggested_id or "", "graph": graph_evidence}`.

- [ ] **Step 1: Write the failing tests** (monkeypatch `LLMAnnotator._request` to return canned dicts and record `user_text`/`schema`)

```python
def test_schema_allows_nil_and_excludes_external_lookup(ann, cands):
    ann.annotate("lung", "", cands)
    s = seen["schema"]["properties"]
    assert s["entity_id"]["enum"] == ["C1", "C2", "C3", "NIL"]
    assert "external_lookup" not in s["hint"]["enum"] and len(s["hint"]["enum"]) == 8
    assert s["confidence_level"]["enum"] == ["sure", "fairly_sure", "unsure"]

def test_suggestion_and_graph_appear_only_when_given(ann, cands):
    ann.annotate("lung", "", cands)
    assert "Suggested by the tool" not in seen["user_text"] and "Graph links" not in seen["user_text"]
    ann.annotate("lung", "", cands, suggested_id="C1", graph_evidence="C1 —[isa]— C2")
    assert "Suggested by the tool: [1] C1" in seen["user_text"] and "C1 —[isa]— C2" in seen["user_text"]

def test_unknown_hint_is_rejected(ann, cands):
    reply.update(hint="vibes")
    with pytest.raises(LLMRerankError):
        ann.annotate("lung", "", cands)

def test_nil_answer_is_accepted(ann, cands):
    reply.update(entity_id="NIL")
    assert ann.annotate("lung", "", cands).entity_id == "NIL"

def test_different_condition_view_is_cached_separately(ann, cands):
    ann.annotate("lung", "", cands); ann.annotate("lung", "", cands, suggested_id="C1")
    assert calls["n"] == 2

def test_reflection_ranking_is_valid_for_the_api(ann, decisions):
    out = ann.reflect(decisions)
    validate_reflection_ranking(out["ranking"])          # does not raise
    assert out["ranking"][-1] == "external_lookup"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_llm_annotator.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `eltool/llm_annotator.py`** per the interfaces. Validation in a `_finalize_protocol(data, ids) -> AnnotationDecision` raising `LLMRerankError` for: id not in `ids + ["NIL"]`, hint not in `AI_HINT_CODES`, level not in the 3 levels; clamp numeric confidence to [0,1]. `reflect` sends a summary table (hint → count, accuracy unknown) and a schema `{ranking: array of AI_HINT_CODES (enum, all distinct), comment: string}`; reject and raise if the ranking is not a permutation of `AI_HINT_CODES`. Reflection is not cached.

- [ ] **Step 4: Run** `python -m pytest -q tests` → all pass.

- [ ] **Step 5: Commit** `git commit -m "feat(llm): protocol annotator with hint, confidence and reflection"`

---

### Task 3: Graph evidence as text (for C3)

**Files:**
- Create: `eltool/llm_evidence.py`
- Test: `tests/test_llm_evidence.py`

**Interfaces:**
- Consumes: graph payload shape from `POST /api/sessions/{name}/graph/candidates` → `{"enabled": bool, "nodes": [{"id", "label", ...}], "edges": [{"source", "target", "rel"}]}`.
- Produces: `render_graph_evidence(graph: dict | None, candidate_ids: list[str], max_lines: int = 30) -> str`

Lines are `"<src> (<name>) —[<rel or 'related'>]— <tgt> (<name>)"`; edges where **both** ends are candidates come first, then candidate→neighbour edges; names from `nodes[].label`, falling back to the id; duplicates removed; at most `max_lines`; `""` for `None`, disabled, or no edges.

- [ ] **Step 1: Failing tests:** `test_candidate_to_candidate_edges_come_first`, `test_names_fall_back_to_id`, `test_empty_or_disabled_graph_gives_empty_string`, `test_output_is_capped_at_max_lines` (60 edges → 30 lines).
- [ ] **Step 2:** `python -m pytest tests/test_llm_evidence.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `python -m pytest -q tests` → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(llm): render candidate graph links as prompt text"`

---

### Task 4: Mode A runner — `eval/run_ai_annotator.py`

**Files:**
- Create: `eval/run_ai_annotator.py`, `eval/helix_api.py`
- Modify: `eltool/store.py` (add `AI_COLS = ["_llm_confidence", "_rationale", "_llm_error"]`)
- Test: `tests/test_run_ai_annotator.py`

**Interfaces:**
- Consumes: Task 2 `LLMAnnotator.annotate/reflect`, Task 3 `render_graph_evidence`; `eltool.store.init_output/write_row/write_feedback/output_columns`; `eltool.session.session_dir/output_csv_path/reflection_path`.
- Produces:
  - `class HelixAPI(base: str = "http://127.0.0.1:8000")` with `row(session: str, idx: int) -> dict`, `candidates(session: str, label: str, kb: str, context: str | None) -> dict` (same body the UI sends: `enable_dense`/`enable_lexical` from the KB config), `graph(session: str, ids: list[str]) -> dict`.
  - `ai_session_name(model: str, source: str) -> str` → `"ai-" + slug(model) + "-" + source`, slug = lowercase, runs of non-`[a-z0-9]` → `-` (`"openai/gpt-oss-120b"` → `"openai-gpt-oss-120b"`).
  - `make_ai_session(source: str, model: str) -> str` — copies the source `session.json` and input file into the new folder; sets `annotator="llm:<model>"`, `collect_feedback=True`, `enable_llm_rerank=False`, keeps `study_condition` (or `"DEV"` when empty); idempotent.
  - `decision_columns(d: AnnotationDecision | None, dense_ids: list[str], condition: str, method: str) -> dict[str, str]` — fills `_method, _elapsed_ms (latency_ms), _selected_rank (1-based in dense_ids or ""), _top1_id (dense_ids[0] or ""), _condition, _revisions "0", _debug_gold_shown "0", _panels "none", _hint, _confidence (level), _llm_confidence (numeric, 3 dp), _rationale`.
  - `run_session(source: str, annotator: LLMAnnotator, api: HelixAPI, limit: int | None = None) -> Counter` — per row of the source session, **skipping rows whose KB column is already filled in the AI session**: fetch row + candidates **from the source session** (so retrieval settings match the human's), dense ids; C1 → no suggestion; C2/C3/DEV → `suggested_id=dense[0]`; C3 → also graph evidence. Write via `write_row` (provenance dict + `annotator="llm:<model>"`) and `write_feedback`. Returns counts per `_method`. After the last row, if no `reflection.json` exists, write `annotator.reflect(...)` in the same JSON shape the API writes.
  - CLI: `python eval/run_ai_annotator.py --prefix study2 --model openai/gpt-oss-120b --base-url http://127.0.0.1:18000/v1 [--provider openai] [--api-key-file ~/.helix_llm_key] [--limit N] [--helix http://127.0.0.1:8000]`; prints the Counter per session.

- [ ] **Step 1: Failing tests** (fake `HelixAPI` returning fixed rows/candidates/graph; fake annotator returning scripted decisions or raising; sessions dir monkeypatched to `tmp_path` like `tests/test_feedback.py`)

```python
def test_writes_every_row_with_protocol_columns(...):
    stats = run_session("study2-1-c2", fake_ann, fake_api)
    out = saved("ai-m-study2-1-c2")
    assert stats["llm"] == 2
    assert list(out["annotator"]) == ["llm:m", "llm:m"]
    assert out.at[0, "_hint"] == "definition" and out.at[0, "_confidence"] == "sure"
    assert out.at[0, "_top1_id"] == "C1" and out.at[0, "_condition"] == "C2"

def test_condition_controls_what_the_model_sees(...):
    # C1: suggested_id None, graph ""; C2: suggested "C1", graph ""; C3: suggested "C1", graph non-empty
    ...

def test_rerun_skips_rows_already_written(...):
    run_session(src, fake_ann, api, limit=1); run_session(src, fake_ann, api)
    assert fake_ann.calls == 2            # not 3

def test_model_error_is_recorded_and_run_continues(...):
    # first annotate() raises LLMRerankError("boom")
    out = saved(...)
    assert "boom" in out.at[0, "_llm_error"] and out.at[0, "_method"] == "" and out.at[0, "UMLS"] == ""
    assert out.at[1, "_method"] == "llm"
    run_session(src, ok_ann, api)                   # rerun retries row 0 only
    assert saved(...).at[0, "_method"] == "llm" and saved(...).at[0, "_llm_error"] == ""

def test_no_candidates_writes_nil_without_calling_model(...):
    assert out.at[0, "UMLS"] == "NIL" and out.at[0, "_method"] == "llm_no_candidates" and fake_ann.calls == 0

def test_reflection_written_once_at_the_end(...):
    assert json.loads(reflection_path("ai-m-study2-1-c2").read_text())["annotator"] == "llm:m"
```

Errors: write only `_llm_error` (directly on the dataframe, then `save_output`) — never `write_row` — because `write_row` freezes provenance as soon as `_method` is non-empty. A successful retry clears `_llm_error`.

- [ ] **Step 2:** `python -m pytest tests/test_run_ai_annotator.py -v` → FAIL.
- [ ] **Step 3: Implement** both files.
- [ ] **Step 4:** `python -m pytest -q tests` → pass.
- [ ] **Step 5: Smoke run against the real stack** (KB on :8765, HELIX API on :8000, tunnel on :18000): `python eval/run_ai_annotator.py --prefix study2-1 --model openai/gpt-oss-120b --base-url http://127.0.0.1:18000/v1 --api-key-file ~/.helix_llm_key --limit 3` → prints `Counter({'llm': 3})`; open `ai-openai-gpt-oss-120b-study2-1-c1` in the UI and check the 3 rows and feedback chips render.
- [ ] **Step 6: Commit** `git commit -m "feat(eval): AI annotator runner (mode A)"`

---

### Task 5: Dev set for the model bake-off

**Files:**
- Modify: `eval/build_study_sessions.py`
- Test: `tests/test_study_blocks.py`

**Interfaces:**
- Produces: `extra_blocks(df: pd.DataFrame, used_docs: set[str], sizes: list[int], seed: int) -> list[list[str]]` — `select_blocks`-style selection over documents not in `used_docs`, one block per entry in `sizes`. CLI `--dev-size N` (default 0) builds session `dev-llm` (`study_condition=""`, `collect_feedback=False`, gold appended to `GOLD_FILE`), from documents outside all study blocks, the debug session and `SEEN_DOCUMENTS`.

- [ ] **Step 1: Failing tests:** `test_extra_blocks_avoid_used_documents`, `test_extra_blocks_hit_requested_sizes` (sizes `[40, 200]` on a 30×20 corpus → ≥40, ≥200), `test_adding_extra_blocks_does_not_change_study_blocks` (study blocks for seed S identical with and without extras).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** `python -m pytest -q tests` → pass.
- [ ] **Step 5:** `python eval/build_study_sessions.py --size 40 --prefix study2 --dev-size 200 --dry-run` prints a `dev-llm` line with ≥200 mentions and the six study lines unchanged from the existing sessions.
- [ ] **Step 6: Commit** `git commit -m "feat(eval): disjoint dev set for LLM bake-off"`

---

### Task 6: Human vs AI analysis

**Files:**
- Modify: `eval/analyze_study.py`
- Test: `tests/test_analyze_study.py`

**Interfaces:**
- Consumes: AI sessions from Task 4 (same columns as human study sessions plus `AI_COLS`).
- Produces:
  - `cohen_kappa(a: list[str], b: list[str]) -> float` (1.0 for identical; 0.0 when expected agreement is 1).
  - `compare_human_ai(human: pd.DataFrame, ai: pd.DataFrame, kb_col: str) -> dict[str, dict]` keyed by condition, joined on `mention_uid` (both frames already merged with gold): `n, human_acc1, ai_acc1, retrieval_acc1, agreement, ai_only_right, human_only_right, hint_kappa (rows where both hints set), ai_by_confidence, human_by_confidence` (each `{level: {"n", "acc1"}}`).
  - CLI flag `--ai-prefix ai-<slug>-study2`: prints a second table with these columns. `--prefix ai-<slug>-dev` (existing `summarise`) scores the bake-off.

- [ ] **Step 1: Failing tests:** `test_kappa_identical_is_one`, `test_kappa_known_value` (a=[x,x,y,y], b=[x,y,y,y] → 0.5), `test_compare_counts_ai_only_and_human_only_right`, `test_compare_joins_on_mention_uid_not_row_order` (shuffled AI frame gives same result), `test_ai_rows_still_failing_are_excluded_and_counted` (non-empty `_llm_error`, empty KB cell).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** `python -m pytest -q tests` → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(eval): compare AI annotator with humans per condition"`

---

### Task 7: Mode B — condition C4, cache-only AI in human sessions, prewarm

**Files:**
- Modify: `eltool/config.py` (`llm_cache_only: bool = False`), `api/main.py` (`_get_reranker` key + `cache_only`, `AnnotateRequest`, `_provenance_for`, `feedback_options`, `save_feedback`), `eltool/store.py` (`LLM_PROVENANCE_COLS = ["_llm_id", "_llm_confidence"]`, `output_columns(..., llm=False)`, frozen with provenance), `eltool/feedback.py` (`AI_SUGGESTION`, `options(include_ai: bool = False)`, `hint_codes(include_ai: bool) -> set[str]`), `eval/build_study_sessions.py` (C4 settings, `--conditions`, `--llm-*` args), `web/src/api/client.ts`, `web/src/pages/Annotate.tsx`, `web/src/types/index.ts`
- Create: `eval/prewarm_llm.py`
- Test: `tests/test_c4.py`, `tests/test_study_blocks.py`

**Interfaces:**
- Consumes: Task 1 `cache_only`; `api.main._fuse_candidates`, `_get_reranker`; Task 4 `HelixAPI`.
- Produces:
  - `AnnotateRequest.llm_id: str | None`, `AnnotateRequest.llm_confidence: float | None`; written as `_llm_id`/`_llm_confidence` when `study_condition` and `enable_llm_rerank` are set.
  - `GET /api/feedback/options?ai=true` includes `ai_suggestion` last; `save_feedback` accepts it only when the session has `enable_llm_rerank`.
  - `condition_order(conditions: list[str]) -> list[str]` → `conditions + reversed(conditions)`.
  - `prewarm_session(session: str, api: HelixAPI) -> Counter` — for every row: `api.row` → `api.candidates` (gives the fused list's inputs), builds the reranker with the session's LLM settings but `cache_only=False`, calls `rank(label, row["context_text"], _fuse_candidates(...))` with exactly the arguments the `/candidates` endpoint uses. Returns `{"cached": n, "computed": n, "failed": n}`. CLI `python eval/prewarm_llm.py --prefix study3`.
  - Frontend: `getFeedbackOptions(ai: boolean)`; `RowProvenance.llm_id?: string; llm_confidence?: number` sent from `candidates[kb].llm` (when no error).

- [ ] **Step 1: Failing tests**

```python
def test_c4_rows_record_what_the_human_was_shown(client, tmp_path): ...
    # save with llm_id="C2", llm_confidence=0.8 → _llm_id "C2", _llm_confidence "0.8"
def test_non_llm_study_session_has_no_llm_columns(client, tmp_path): ...
def test_ai_hint_only_accepted_in_llm_sessions(client, tmp_path): ...   # 422 in C2, 200 in C4
def test_options_with_ai_flag_has_ten_hints(client): ...
def test_cache_only_session_returns_candidates_and_llm_error_on_miss(client, tmp_path, fake_kb): ...
def test_prewarm_then_cache_only_api_hits(client, tmp_path, fake_kb, fake_llm): ...
    # prewarm_session(...) then POST /candidates with the same label+context → response["llm"]["cached"] is True
def test_condition_order_is_abcd_dcba(): assert condition_order(["C1","C2","C3","C4"]) == ["C1","C2","C3","C4","C4","C3","C2","C1"]
```

`fake_kb`: monkeypatch `KBClient.query` to return fixed `Candidate`s. `fake_llm`: monkeypatch `LLMReranker._request`.

- [ ] **Step 2:** `python -m pytest tests/test_c4.py tests/test_study_blocks.py -v` → FAIL.
- [ ] **Step 3: Implement backend + builder.** Builder applies `enable_llm_rerank=False` **before** `CONDITION_SETTINGS[condition]` so C4 can turn it on; `--conditions C1,C2,C3` keeps today's default order exactly.
- [ ] **Step 4: Implement frontend** (options query key includes the flag; provenance fields; no other UI change — the existing `LLMPickBanner` and LLM-first auto-select already give C4 its UI).
- [ ] **Step 5: Verify:** `python -m pytest -q tests` → pass; `cd web && npx tsc --noEmit -p .` → no output.
- [ ] **Step 6: Manual check:** build one C4 session against a stub or the real model, run prewarm, open it: the purple banner shows on every row, the AI pick is pre-selected, the feedback prompt lists 10 hints; stop the tunnel and reload — rows still show the banner (cache), a new uncached label shows the amber "LLM re-rank unavailable" notice and the annotator can continue.
- [ ] **Step 7: Commit** `git commit -m "feat(study): C4 AI-assisted condition with prewarmed, cache-only LLM"`

---

### Task 8: Mode C — pre-annotation and review queue (backend)

**Files:**
- Create: `eltool/review.py`, `eval/preannotate.py`
- Modify: `eltool/config.py` (`review_mode: bool = False`, `review_spotcheck_rate: float = 0.1`), `eltool/store.py` (`ROUTE_COL = "_route"`), `api/main.py` (`get_progress` adds `queue`, `get_row` adds `review`)
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: Task 2 `LLMAnnotator.annotate`, Task 4 `HelixAPI`, `decision_columns`.
- Produces:
  - `route(level: str, llm_id: str, top1_id: str, rng: random.Random, spotcheck_rate: float) -> str` ∈ {`auto`, `spotcheck`, `review`} per Global Constraints (draw `rng.random()` only for auto-eligible rows, so the sample is reproducible from the seed).
  - `preannotate(session: str, annotator: LLMAnnotator, api: HelixAPI, seed: int = 20261016) -> Counter` — C2 view; writes `_llm_id`, `_llm_confidence` (level), `_route`, `_rationale` for every row; `auto` rows also get the KB column = pick, `annotator="llm:<model>"`, `_method="llm_auto"`; `review`/`spotcheck` rows keep the KB column empty and `_method` empty (so the human's save sets provenance normally). Resumable: rows with `_route` set are skipped.
  - `GET /progress` → adds `"queue": list[int]` (rows with route in {review, spotcheck}, input order) when `review_mode`, and `resume_idx` = first queue row with an empty KB column.
  - `GET /rows/{idx}` → adds `"review": {"route", "llm_id", "level", "rationale"}` for `review` rows, `{"route": "spotcheck"}` only for spotcheck rows, `None` otherwise.

- [ ] **Step 1: Failing tests:** `test_route_auto_requires_sure_and_top1_and_not_nil`, `test_route_spotcheck_rate_is_reproducible` (same seed → same rows; ~10% of 1000 eligible within 7–13%), `test_preannotate_fills_only_auto_rows`, `test_preannotate_is_resumable`, `test_queue_lists_review_and_spotcheck_rows`, `test_spotcheck_rows_hide_the_ai_pick`, `test_non_review_session_has_no_queue`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** `python -m pytest -q tests` → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(review): LLM pre-annotation with auto/spotcheck/review routing"`

---

### Task 9: Mode C — review queue in the UI + review analysis

**Files:**
- Modify: `web/src/types/index.ts` (`Progress.queue?: number[]`, `RowData.review?: ReviewInfo | null`), `web/src/pages/Annotate.tsx`, `web/src/pages/Configure.tsx` (review_mode checkbox + spot-check rate), `eval/analyze_study.py`
- Test: `tests/test_analyze_study.py`

**Interfaces:**
- Consumes: Task 8 API fields.
- Produces:
  - Annotate: when `progress.queue` exists, Next/Prev/finish move along `queue` (next queue index after `rowIdx`; finish after the last), the header shows `Queue i / n`; on a `review` row the AI pick is pre-selected (no countdown) with a banner "AI suggested <id> (<level>) — <rationale>"; on a `spotcheck` row nothing AI-related is shown.
  - `summarise_review(df: pd.DataFrame, kb_col: str) -> dict` → `n_auto, n_review, n_spotcheck, auto_acc1 (vs gold), spotcheck_agreement (human answer == _llm_id), review_acc1, human_minutes, est_minutes_saved (n_auto × median human secs over review+spotcheck rows)`; CLI `--review-prefix`.

- [ ] **Step 1: Failing tests:** `test_review_summary_counts_routes`, `test_auto_accuracy_uses_gold`, `test_minutes_saved_uses_median_human_time`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement analysis, then UI. **Step 4:** `python -m pytest -q tests` → pass; `cd web && npx tsc --noEmit -p .` → clean.
- [ ] **Step 5: Manual e2e** (headless Chrome via `playwright-core` with `channel: 'chrome'`, as used for the feedback feature): a 6-row review session with routes `[auto, review, auto, spotcheck, review, auto]` → the annotator lands on row 2, Next goes 2→4→5→finish, row 4 shows no AI banner, row 2 shows it pre-selected, and the CSV ends with all 6 KB cells filled.
- [ ] **Step 6: Commit** `git commit -m "feat(review): review-queue navigation and review analysis"`

---

## Out of code (tracked in the spec, not this plan)

DGX setup, vLLM container and the SSH tunnel (spec §4–5); IT request; model bake-off run (spec §6.3, uses Tasks 4–6); ethics amendment and Annotation Guide update (spec §8); decisions D1–D5 (spec §7).
