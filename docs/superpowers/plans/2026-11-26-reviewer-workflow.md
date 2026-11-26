# Reviewer Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the review page into the reviewer's judging workflow: compare multi-concept answers as sets, accept or combine them, record how each final answer was reached, export only decided rows, and stop reviewers from annotating.

**Architecture:** Two pure modules hold the rules (`eltool/answers.py`: comparing answers and deriving the basis; `eltool/adjudication.py`: row status, ticked options, export reason). `adjudications` stays append-only and gains three columns; a review transaction makes each decision atomic with the annotator answers it was based on. The web page renders what the server computed and echoes a row version back, so a stale screen cannot decide a row.

**Tech Stack:** FastAPI, stdlib `sqlite3`, pytest `TestClient`; React 18 + react-router + react-query + Tailwind. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-11-26-reviewer-workflow-design.md`](../specs/2026-11-26-reviewer-workflow-design.md). Read it first.

**Baseline (2026-11-26):** `KMP_DUPLICATE_LIB_OK=TRUE /Users/lakshankarunathilake/miniconda3/bin/python -m pytest tests -q` → 617 passed; `cd web && npx tsc --noEmit` → clean. Both must stay green after every task. Web tasks are verified with `npx tsc --noEmit` and `npm run build` (no web test runner).

## Global Constraints

- With `HELIX_AUTH` unset nothing changes; review routes stay 404 and no `helix.db` is created.
- An answer for one KB is a set of concept ids: split on `|`, trim, drop blanks and duplicates. `NIL` is single-valued and never combined with a concept. Two answers are equal when their sets are equal in every KB.
- Only human decisions count; script-written rows stay excluded.
- Row statuses, exactly: `disagree`, `single`, `agreed`, `changed`, `decided`, `waiting`.
- Basis values, exactly: `accepted`, `combined`, `override`, `bulk_agreed`, plus `legacy` for decisions saved before this change. The server derives `basis` and `source_users`; the browser cannot set them.
- Export reasons, exactly: `disagree`, `single_annotator`, `awaiting_review`, `changed_since_review`, `script_only`, `no_decisions`. Only `decided` rows carry KB values.
- `adjudications` and `decisions` are append-only: no endpoint updates or deletes them.
- A session the caller may not see is 404, not 403. Reviewers get 403 on every annotation write.
- Nothing on the review screen takes one person's answer by default.
- No automatic resolution rules, weighting, AI tie-breaking, blind names, sampling, graph hints or send-back.
- Single uvicorn worker. Every commit message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Builder decisions the spec leaves open

- A decision request carries the row's `version` as the reviewer saw it; a stale one is 409 (bulk: skipped).
- A stored decision that lacks a value for a current KB (KB added later) does not decide the row.
- `waiting` rows are counted but not listed, and cannot be decided.
- Agreement skips a (user, row) whose answer for that KB is blank.
- Stored final answers are written with ids sorted.
- Reviewers do not get the per-layer export menu on the session list.

## Review Focus

1. An annotator changes an answer while the reviewer has the row open → the confirm is refused (409) or bulk-skipped, nothing stored (Task 3).
2. The same concepts in another order, with spaces or duplicates → `agreed`, and a re-save in another order does not make a decided row `changed` (Tasks 1, 2, 3).
3. An existing `helix.db` with old decisions, opened by several requests at once → columns added once, old rows read as `legacy` and `decided` (Task 2).
4. Two reviewers decide one row; the second works from the old screen → 409 (Task 3).
5. `-1` as a concept id and a formula-like reviewer name in the final export → `-1` unchanged, the name guarded (Task 3).

## File Structure

| File | Responsibility |
|---|---|
| `eltool/answers.py` (new) | parse and compare answers, derive basis |
| `eltool/adjudication.py` (new) | row state, version, ticked options, export reason |
| `eltool/agreement.py` (modify) | set-based agreement with overlap; `final_rows` removed |
| `eltool/userdb.py` (modify) | schema v2 and migration |
| `eltool/decisions.py` (modify) | new adjudication fields, review transaction |
| `api/review_routes.py` (rewrite) | review, summary, row context, agreement, decide, bulk, final export |
| `eltool/auth.py`, `api/main.py` (modify) | `annotator_access` on annotation writes |
| `web/src/pages/Review.tsx` (rewrite) | page state, filters, queue |
| `web/src/components/review/*` (new) | `AgreementPanel`, `RowList`, `DecisionCard`, `ConceptName` |
| `web/src/components/ContextCard.tsx` (new, moved) | document context with highlighted mention |
| `web/src/pages/Home.tsx`, `web/src/App.tsx` (modify) | reviewer session list, `/annotate` redirect |
| `README.md`, `deploy/hosted/README.md` (modify) | describe the workflow |

---

### Task 1: Comparing answers and agreement

**Files:**
- Create: `eltool/answers.py`, `tests/test_answers.py`
- Modify: `eltool/agreement.py` (only `pairwise_agreement`; leave `final_rows` for Task 3), `tests/test_agreement.py`

**Interfaces — produces:**

```python
NIL = "NIL"
Answer = dict[str, tuple[str, ...]]          # KB name -> sorted concept ids

def parse_ids(value: str | None) -> tuple[str, ...]: ...
    # split "|", strip, drop blanks, any-case "nil" -> "NIL", dedupe, sort;
    # NIL beside a concept is dropped
def mixes_nil(value: str | None) -> bool: ...        # raw value names NIL and a concept
def join_ids(ids: Iterable[str]) -> str: ...         # sorted, unique, "|"-joined
def normalise(kb_values: Mapping[str, str], kb_names: Sequence[str]) -> Answer: ...
def is_blank(answer: Answer) -> bool: ...            # no KB has an id
def is_complete(answer: Answer) -> bool: ...         # every KB has an id
def basis_for(final: Answer, answers: Mapping[int, Answer]) -> tuple[str, list[int]]: ...
```

`basis_for`: if any annotator's answer equals `final` → `("accepted", those users)`. Otherwise sources are users sharing at least one id with `final` in the same KB; if every final id was proposed by someone for that KB → `"combined"`, else `"override"`. User lists sorted.

`pairwise_agreement(values: dict[int, dict[int, str]])` keeps its signature. It compares `parse_ids` sets, ignores rows where either user's set is empty, runs kappa on `join_ids` strings, and adds `"overlap"`: mean Jaccard over shared rows in 0-100 (None when n = 0).

- [ ] **Step 1: Write failing tests**

`tests/test_answers.py`:

```python
def test_parse_ids_trims_dedupes_sorts():
    assert parse_ids(" C2 |C1||C2 ") == ("C1", "C2")
    assert parse_ids("") == parse_ids(None) == parse_ids(" | ") == ()

def test_nil_is_single_valued():
    assert parse_ids("nil") == ("NIL",)
    assert parse_ids("NIL|C1") == ("C1",)
    assert mixes_nil("NIL|C1") and not mixes_nil("NIL") and not mixes_nil("C1|C2")

def test_minus_one_is_an_ordinary_id():
    assert parse_ids("-1") == ("-1",)

def test_order_does_not_matter():
    assert normalise({"U": "C1|C2"}, ["U"]) == normalise({"U": " C2 | C1 "}, ["U"])

def test_blank_and_complete():
    a = normalise({"U": "C1"}, ["U", "M"])
    assert not is_blank(a) and not is_complete(a)
    assert is_blank(normalise({}, ["U", "M"]))

def _a(u, m="D1"):
    return normalise({"U": u, "M": m}, ["U", "M"])

def test_basis_accepted_lists_every_matching_user():
    assert basis_for(_a("C1"), {2: _a("C1"), 1: _a("C1"), 3: _a("C2")}) == ("accepted", [1, 2])

def test_basis_combined_from_two_users():
    assert basis_for(_a("C1|C2"), {1: _a("C1"), 2: _a("C2")}) == ("combined", [1, 2])

def test_basis_combined_for_a_subset():
    assert basis_for(_a("C1"), {1: _a("C1|C2"), 2: _a("C3", "D2")}) == ("combined", [1])

def test_basis_override_keeps_partial_sources():
    assert basis_for(_a("C1|C9"), {1: _a("C1"), 2: _a("C2", "D2")}) == ("override", [1])

def test_basis_concept_proposed_for_another_kb_is_override():
    assert basis_for(_a("D1"), {1: _a("C1")})[0] == "override"

def test_basis_nil_nobody_chose_is_override():
    assert basis_for(_a("NIL"), {1: _a("C1")})[0] == "override"
```

Add to `tests/test_agreement.py`:

```python
def test_selection_order_is_agreement():
    (p,) = pairwise_agreement({1: {0: "C1|C2"}, 2: {0: "C2|C1"}})
    assert (p["percent"], p["overlap"]) == (100.0, 100.0)

def test_partial_overlap_is_reported():
    (p,) = pairwise_agreement({1: {0: "C1|C2", 1: "C3"}, 2: {0: "C1", 1: "C3"}})
    assert (p["n"], p["percent"], p["overlap"]) == (2, 50.0, 75.0)

def test_blank_values_are_not_shared_rows():
    (p,) = pairwise_agreement({1: {0: "", 1: "a"}, 2: {0: "", 1: "a"}})
    assert (p["n"], p["percent"]) == (1, 100.0)
```

and assert `overlap is None` in `test_kappa_none_for_under_two_shared_rows` for the `n == 0` case.

- [ ] **Step 2:** `python -m pytest tests/test_answers.py tests/test_agreement.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement the interfaces above.
- [ ] **Step 4:** Same command passes; full suite passes.
- [ ] **Step 5:** Commit `feat(review): compare answers as concept sets`.

---

### Task 2: Row state and decision storage

**Files:**
- Create: `eltool/adjudication.py`, `tests/test_adjudication.py`
- Modify: `eltool/userdb.py`, `eltool/decisions.py`, `tests/test_userdb.py`, `tests/test_decisions.py`

**Interfaces — consumes:** everything from `eltool.answers` (Task 1).

**Interfaces — produces (`eltool/adjudication.py`):**

```python
STATUSES = ("disagree", "single", "agreed", "changed", "decided", "waiting")

@dataclass
class RowState:
    status: str
    answers: dict[int, Answer]   # non-blank human answers, by user id
    decision: dict | None        # the stored decision, only when it covers every current KB
    pending: int                 # annotator_ids without a complete answer
    bulk_eligible: bool
    version: str                 # changes when an answer, the KB list or the decision changes

def snapshot(answers: Mapping[int, Answer]) -> dict[str, dict[str, str]]: ...
    # the stored `based_on` form: {"<user id>": {kb: "C1|C2"}}
def row_state(raw: Mapping[int, Mapping[str, str]], decision: dict | None,
              kb_names: Sequence[str], annotator_ids: Collection[int]) -> RowState: ...
def options(answers: Mapping[int, Answer], kb_names: Sequence[str]) -> dict[str, list[dict]]: ...
    # {kb: [{"id", "user_ids", "preticked"}]} sorted by id
def export_reason(status: str, script_only: bool) -> str: ...
```

Rules:
- Status with a covering decision: `based_on is None` (legacy) → `decided`; else `decided` if `snapshot(answers)` equals `based_on` after normalising `based_on` over `kb_names`, else `changed`.
- Without one: 0 answers `waiting`, 1 `single`, all equal `agreed`, else `disagree`.
- `bulk_eligible`: status `agreed`, `pending == 0`, and the shared answer is complete.
- `preticked`: two or more answers and every one of them has that id for that KB.
- `version`: first 16 hex of SHA-256 over sorted-key JSON of `[snapshot, list(kb_names), decision id or None]` (stored decision id, covering or not).
- `export_reason`: `decided` → `""`, `disagree` → `disagree`, `single` → `single_annotator`, `agreed` → `awaiting_review`, `changed` → `changed_since_review`, `waiting` → `script_only` if `script_only` else `no_decisions`.

**Interfaces — produces (storage):**
- `userdb`: schema version 2; `adjudications` gains `basis TEXT`, `source_users TEXT`, `based_on TEXT`. A new database is created at v2. A v1 database is migrated inside `BEGIN IMMEDIATE`, re-reading `user_version` and `PRAGMA table_info` under the lock.
- `decisions` adjudication dicts: `id, ts, row_idx, reviewer_id, kb_values, note, basis` (`"legacy"` when NULL), `source_users` (`[]` when NULL), `based_on` (None when NULL).
- `decisions.add_adjudication(session_name, row_idx, reviewer_id, kb_values, note="", *, basis=None, source_users=None, based_on=None) -> dict`
- `decisions.review_transaction(session_name)`: context manager holding one `BEGIN IMMEDIATE` transaction; yields an object with `latest() -> dict[tuple[int, int], Decision]` (same meaning as `latest_human_or_script`), `adjudications() -> dict[int, dict]`, and `add(row_idx, reviewer_id, kb_values, note, *, basis, source_users, based_on) -> dict`. Commits on clean exit, rolls back on exception.

- [ ] **Step 1: Write failing tests**

`tests/test_adjudication.py` (with `KB = ["U"]`):

```python
def _d(based_on, kb_values=None, id=1):
    return {"id": id, "kb_values": kb_values or {"U": "C1"}, "based_on": based_on}

@pytest.mark.parametrize("raw,status", [
    ({}, "waiting"),
    ({1: {"U": ""}}, "waiting"),
    ({1: {"U": "C1"}}, "single"),
    ({1: {"U": "C1|C2"}, 2: {"U": "C2 | C1"}}, "agreed"),
    ({1: {"U": "C1"}, 2: {"U": "C1|C2"}}, "disagree"),
])
def test_status_without_decision(raw, status):
    assert row_state(raw, None, KB, []).status == status

def test_decided_until_an_answer_changes_appears_or_disappears():
    d = _d({"1": {"U": "C1|C2"}, "2": {"U": "C1"}})
    same = {1: {"U": "C2|C1"}, 2: {"U": "C1"}}
    assert row_state(same, d, KB, []).status == "decided"
    for raw in ({1: {"U": "C3"}, 2: {"U": "C1"}},
                {**same, 3: {"U": "C1"}},
                {1: {"U": "C1|C2"}}, {}):
        assert row_state(raw, d, KB, []).status == "changed", raw

def test_legacy_decision_is_never_changed():
    assert row_state({1: {"U": "C9"}}, _d(None), KB, []).status == "decided"

def test_decision_missing_a_current_kb_does_not_decide():
    s = row_state({1: {"U": "C1", "M": "D1"}}, _d(None), ["U", "M"], [])
    assert (s.status, s.decision) == ("single", None)

def test_bulk_needs_every_annotator_complete():
    raw = {1: {"U": "C1"}, 2: {"U": "C1"}}
    assert row_state(raw, None, KB, [1, 2]).bulk_eligible
    waiting = row_state(raw, None, KB, [1, 2, 3])
    assert (waiting.pending, waiting.bulk_eligible) == (1, False)
    blank_kb = {1: {"U": "C1", "M": ""}, 2: {"U": "C1", "M": ""}}
    s = row_state(blank_kb, None, ["U", "M"], [])
    assert (s.status, s.bulk_eligible) == ("agreed", False)

def test_version_ignores_order_and_follows_answers_and_decision():
    v = lambda raw, d=None: row_state(raw, d, KB, []).version
    assert v({1: {"U": "C1|C2"}}) == v({1: {"U": "C2|C1"}})
    assert v({1: {"U": "C1"}}) != v({1: {"U": "C2"}})
    assert v({1: {"U": "C1"}}) != v({1: {"U": "C1"}}, _d({"1": {"U": "C1"}}))

def test_only_unanimous_concepts_are_preticked():
    two = row_state({1: {"U": "C1|C2"}, 2: {"U": "C1"}}, None, KB, []).answers
    assert options(two, KB)["U"] == [
        {"id": "C1", "user_ids": [1, 2], "preticked": True},
        {"id": "C2", "user_ids": [1], "preticked": False}]
    one = row_state({1: {"U": "C1"}}, None, KB, []).answers
    assert options(one, KB)["U"][0]["preticked"] is False

def test_export_reasons():
    assert [export_reason(s, False) for s in
            ("decided", "disagree", "single", "agreed", "changed", "waiting")] == [
        "", "disagree", "single_annotator", "awaiting_review",
        "changed_since_review", "no_decisions"]
    assert export_reason("waiting", True) == "script_only"
```

Storage tests: in `tests/test_userdb.py`, `test_v1_database_gains_decision_columns` — build a v1 `helix.db` by hand (the old `CREATE TABLE adjudications` without the three columns, one user, one adjudication row, `PRAGMA user_version=1`), open it with `userdb.connect()` from 8 threads at once, then assert no exception, `user_version == 2`, the three columns exist once, and `decisions.current_adjudications("s1")[0]` has `basis == "legacy"`, `source_users == []`, `based_on is None`. In `tests/test_decisions.py`, `test_adjudication_stores_basis_sources_and_based_on` (round trip) and `test_review_transaction_rolls_back` (`add` then raise → no row).

- [ ] **Step 2:** `python -m pytest tests/test_adjudication.py tests/test_userdb.py tests/test_decisions.py -q` → FAIL.
- [ ] **Step 3:** Implement. In `decisions.py`, share the row-reading SQL between the existing functions and the transaction so both mean the same thing.
- [ ] **Step 4:** Same command passes; full suite passes.
- [ ] **Step 5:** Commit `feat(review): row states and decision records`.

---

### Task 3: Review API and final export

**Files:**
- Modify: `api/review_routes.py`, `eltool/agreement.py` (delete `FinalRow`, `final_rows`; update docstring), `tests/test_review_api.py`, `tests/test_agreement.py` (delete `final_*` tests), `tests/test_decision_integrity.py`, `tests/test_pilot_workflow_e2e.py`, `tests/test_auth_hardening.py`

**Interfaces — consumes:** Tasks 1 and 2.

**Interfaces — produces** (all under `reviewer_access`; the web task relies on these shapes):

| Route | Returns |
|---|---|
| `GET /api/sessions/{name}/review` | `{kb_names, total, has_context, counts, rows}` |
| `GET /api/sessions/{name}/review/summary` | `{total, to_decide, agreed, decided, waiting}` |
| `GET /api/sessions/{name}/review/rows/{idx}` | `{idx, label, doc_id, info, context_text, entity_span}`; 404 out of range; writes nothing |
| `GET /api/sessions/{name}/agreement` | as before; each pair also has `overlap` |
| `POST /api/sessions/{name}/adjudications` | body `{row_idx, kb_values, note="", version}` → decision |
| `POST /api/sessions/{name}/adjudications/bulk` | body `{rows: [{row_idx, version}]}` → `{confirmed: [idx], skipped: [{row_idx, reason}]}` |

- `counts` = `{to_decide, agreed, decided, waiting}`; `to_decide` = `disagree` + `single` + `changed`.
- `rows`: every row whose status is not `waiting`, by index: `{idx, label, doc_id, status, version, pending, bulk_eligible, answers, options, decision}`. `answers`: `[{user_id, user_name, kb_ids: {kb: [id]}, complete, ts}]` ordered by user name then id. `doc_id` is `""` without `doc_id_col`.
- decision: `{row_idx, kb_values, kb_ids, note, basis, source_users, reviewer_id, reviewer_name, ts}`.
- `has_context` = `context_folder` and `doc_id_col` both set. Context is read with `api.main._read_context`.
- Annotator ids for `pending` = active members of the session whose role is `annotator`.

Decide, in this order:
1. 404 `Row out of range`.
2. 400 KB coverage and 400 blank value (keep today's two messages; blank means `parse_ids` is empty).
3. 400 `NIL cannot be combined with a concept (<KB list>).`
4. Inside `review_transaction`: 409 `The answers for this row changed since you opened it. Check it again.` when `version` differs.
5. 409 `No annotator has answered this row yet.` when there are no answers.
6. `basis_for`; 400 `Add a note: this answer is an override (it includes a concept no annotator chose).` for an override with a blank note.
7. Store `kb_values` as `join_ids`, with `basis`, `source_users`, `based_on=snapshot(answers)`.

Bulk, one `review_transaction`, each distinct `row_idx` once, first failing reason wins: `out_of_range`, `changed` (version differs), `not_agreed` (status is not `agreed`), `pending` (not bulk eligible). Confirmed rows store the shared answer with `basis="bulk_agreed"`, all answering users as sources, empty note.

Final export columns: input columns, KB columns not already present, then `_basis`, `_decided_by`, `_needs_review`, `_review_reason` (no duplicate headers). `decided` rows: KB values from the decision, its basis, reviewer name, the last two blank. All others: KB columns, `_basis`, `_decided_by` blank, `_needs_review="1"`, reason from `export_reason`. Keep `spreadsheet_safe_cell` on every cell.

- [ ] **Step 1: Write the failing tests** in `tests/test_review_api.py`, with helpers:

```python
def _row(client, idx, name="s1"):
    body = client.get(f"/api/sessions/{name}/review").json()
    return next(r for r in body["rows"] if r["idx"] == idx)

def _decide(client, idx, values, note="", name="s1", version=None):
    version = version or _row(client, idx, name)["version"]
    return client.post(f"/api/sessions/{name}/adjudications", json={
        "row_idx": idx, "kb_values": values, "note": note, "version": version})
```

Tests (names and what they assert):

```python
def test_selection_order_is_agreed(world, as_user):
    _save(as_user(world["alice"]), "C1|C2"); _save(as_user(world["bob"]), "C2|C1")
    rev = as_user(world["rev"])
    row = _row(rev, 0)
    assert (row["status"], row["bulk_eligible"]) == ("agreed", True)
    assert [o["preticked"] for o in row["options"]["UMLS"]] == [True, True]
    assert rev.get("/api/sessions/s1/agreement").json()["by_kb"]["UMLS"][0]["percent"] == 100.0

def test_decide_records_basis_and_sources(world, as_user):
    alice, bob = world["alice"], world["bob"]
    _save(as_user(alice), "C1"); _save(as_user(bob), "C2")
    rev = as_user(world["rev"])
    r = _decide(rev, 0, {"UMLS": "C2|C1"})
    assert r.status_code == 200, r.text
    assert (r.json()["basis"], r.json()["source_users"], r.json()["kb_values"]) == \
        ("combined", sorted([alice.id, bob.id]), {"UMLS": "C1|C2"})
    assert _row(rev, 0)["status"] == "decided"
    assert _decide(rev, 0, {"UMLS": "C2"}).json()["basis"] == "accepted"

def test_override_needs_a_note(world, as_user):
    _save(as_user(world["alice"]), "C1")
    rev = as_user(world["rev"])
    r = _decide(rev, 0, {"UMLS": "C9"})
    assert r.status_code == 400 and "Add a note" in r.json()["detail"]
    assert decisions.current_adjudications("s1") == {}
    assert _decide(rev, 0, {"UMLS": "C9"}, note="wrong sense").json()["basis"] == "override"

def test_browser_cannot_set_basis(world, as_user):
    # extra "basis"/"source_users" in the body are ignored: an unproposed concept stays an override
    ...  # post with basis="accepted", no note -> 400

def test_nil_with_a_concept_and_pipe_only_values_are_rejected(world, as_user):
    # {"UMLS": "NIL|C1"} -> 400 "NIL cannot be combined"; {"UMLS": "|"} -> 400 "Enter a value"

def test_stale_screen_is_refused(world, as_user):
    # rev reads version; alice re-saves "C2" -> 409, nothing stored.
    # alice re-saving the same ids in another order keeps the old version valid -> 200.

def test_second_reviewer_with_old_screen_is_refused(world, as_user):
    # rev and admin read the same version; rev decides -> 200; admin posts that version -> 409.

def test_waiting_row_cannot_be_decided(world, as_user):
    # script-only row 1: not in rows, counts["waiting"] counts it; POST with any version
    # equal to row_state's for no answers -> 409. Use row_state({}, None, ["UMLS"], []).version.

def test_answer_change_marks_decided_row_changed(world, as_user):
    # decide, alice saves another id -> status "changed", row["decision"] still shown,
    # export reason "changed_since_review", KB blank.

def test_bulk_confirms_only_qualifying_rows(world, as_user, make_user):
    # a third annotator member Carol. Row 0: all three agree. Row 1: they differ.
    # Row 2: alice and bob agree, Carol has not answered.
    # Call 1: row 0 with version "stale" -> skipped "changed", nothing stored.
    # Call 2: rows 0, 1, 2, 99 -> confirmed [0] with basis "bulk_agreed"; skipped exactly
    # (1, "not_agreed"), (2, "pending"), (99, "out_of_range"); only row 0 is stored.

def test_bulk_skips_a_row_changed_since_it_was_ticked(world, as_user):
    # read version, both annotators change to a new agreed id -> skipped "changed"

def test_final_export_only_decided_rows_have_values(world, as_user):
    # row 0 decided, row 2 agreed undecided:
    # header == ["entity_text","document_id","UMLS","_basis","_decided_by",
    #            "_needs_review","_review_reason"]
    # row0 -> ("C1","accepted","Rev","",""), row1 -> ("","","","1","no_decisions"),
    # row2 -> ("","","","1","awaiting_review")

def test_final_export_keeps_minus_one_and_guards_reviewer_name(world, as_user, make_user):
    # reviewer member named "=cmd", answers "-1", decision -> UMLS == "-1",
    # _decided_by == "'=cmd"

def test_row_context_is_readable_and_writes_nothing(world, as_user):
    # set context_folder + doc_id_col in session.json, write <ctx>/d1.txt;
    # GET review/rows/0 -> context_text, label "cold", doc_id "d1";
    # idx 3 -> 404; no annotations/<rev.id>.csv created

def test_summary_counts(world, as_user):
    # {total: 3, to_decide, agreed, decided, waiting} for one disagree, one agreed row
```

Write the bodies for the commented ones from those comments. Update `test_annotator_gets_403_…` and `test_non_member_reviewer_gets_404` to include `review/summary`, `review/rows/0` and `adjudications/bulk`; `test_review_routes_are_404_with_auth_off` likewise (POST bodies must be valid: include `version`). The adjudication-route listing assertion becomes the two POST routes.

Update the older tests to the new rules rather than deleting their intent: replace `row["disagree"]` with `status`; where a test expected an agreed value in the export without a reviewer, assert `awaiting_review` and blank, then decide and assert the value; add `version` to every adjudication POST. The removed `test_adjudication_is_append_only_latest_wins` "adjudicated row with no decisions" part is now the waiting-row refusal.

- [ ] **Step 2:** `python -m pytest tests/test_review_api.py -q` → new tests FAIL.
- [ ] **Step 3:** Implement the routes.
- [ ] **Step 4:** Full suite passes.
- [ ] **Step 5:** Commit `feat(review): decide, bulk confirm and export only reviewed rows`.

---

### Task 4: Reviewers cannot annotate

**Files:**
- Modify: `eltool/auth.py`, `api/main.py`; any existing test that has a reviewer save annotations
- Create: `tests/test_reviewer_permissions.py`

**Interfaces — produces:** `auth.annotator_access(name: str, request: Request) -> userdb.User | None`: runs `session_access` (404 first), then 403 `Reviewers cannot annotate` for role `reviewer`. Passes with auth off.

Use it in place of `session_access` on exactly: `POST rows/batch`, `POST rows/{idx}`, `POST rows/{idx}/feedback`, `PUT reflection`, `POST auto-annotate`, `POST graph/optimize`, `POST graph/optimize-all`, `POST snapshot`. Reads stay on `session_access`.

- [ ] **Step 1: Write failing tests**
  - `test_reviewer_gets_403_on_every_annotation_write`: parametrised over the eight routes with a valid body or query each; assert 403, no `annotations/<rev.id>.csv`, no row in `decisions`.
  - `test_annotator_and_admin_are_not_refused`: the same calls return something other than 403 (row save returns 200).
  - `test_reviewer_who_is_not_a_member_gets_404`.
  - `test_auth_off_is_unchanged`: row save 200.
  - `test_answers_saved_before_becoming_reviewer_still_count`: annotator saves, `userdb.set_role(id, "reviewer")`, the review row still lists that answer.
  - `test_every_session_write_route_is_guarded`: for each route of `app.routes` whose path starts `/api/sessions/{name}` with a POST/PUT/DELETE method, the dependant's direct dependency calls include `annotator_access`, `require_admin` or `reviewer_access`, unless the path is one of the read-only POSTs `candidates`, `graph/candidates`, `graph/document`.
- [ ] **Step 2:** `python -m pytest tests/test_reviewer_permissions.py -q` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Full suite passes.
- [ ] **Step 5:** Commit `feat(auth): the reviewer role cannot annotate`.

---

### Task 5: Review screen

**Files:**
- Modify: `web/src/types/index.ts`, `web/src/api/client.ts`, `web/src/pages/Review.tsx` (rewrite), `web/src/pages/Annotate.tsx` (import the moved context card)
- Create: `web/src/components/ContextCard.tsx`, `web/src/components/review/AgreementPanel.tsx`, `RowList.tsx`, `DecisionCard.tsx`, `ConceptName.tsx`

**Interfaces — consumes:** the Task 3 routes. Types mirror them: `ReviewStatus` (the five listed statuses), `Basis`, `ReviewAnswer`, `ReviewOption`, `Adjudication`, `ReviewRow`, `ReviewCounts`, `ReviewData`, `ReviewSummary`, `ReviewRowContext`, `BulkResult`; `PairAgreement` gains `overlap: number | null`. Client: `getReview`, `getReviewSummary`, `getReviewRow(name, idx)`, `getAgreement`, `postAdjudication(name, {row_idx, kb_values, note, version})`, `postBulkAdjudications(name, rows)`, `finalExportUrl`.

**Behaviour**

- Page: back link, title, **Export final**, agreement panel (exact %, kappa, overlap %, n; excluded script rows).
- Filters with counts: To decide (default), Agreed, Decided, All; beside them "N rows waiting for annotators" when `waiting > 0`. All = every listed row.
- Row list: 50 per page; row number, mention, document id, status, each annotator's concept names; a row opens its card. In Agreed: a checkbox on eligible rows (unticked by default), "N annotators pending" on the others, "Select all on this page", **Confirm selected (n)**. The selection stores each row's `version` at tick time and clears on filter or page change. After the call show how many were confirmed and each skip reason in plain words.
- Opening a row freezes the current filtered order as the queue for Prev/Next; the card replaces the list ("Back to list"). The open row stays shown even after it leaves the filter.
- Card: mention, document id, position in queue, session info columns, document context (`ContextCard`, only when the row context has text); a pending note; for `changed` the line "Answers changed since this decision" with the old decision; for `decided` the current decision with basis and reviewer.
- One line per annotator: name, concept names per KB, **Accept**. Accept saves that whole answer at once (with the note, if typed). Disabled while saving or when `complete` is false (say which KB has no value).
- Final answer per KB: a tickable chip per option (showing who chose it), plus added concepts and a `NIL` chip. Ticking NIL unticks that KB's concepts and the reverse. Initial ticks: `decided` → the decision's ids; every other status → options with `preticked`.
- Add concept: id field and button per KB; Enter adds; input containing `|` is split. The added chip is ticked and looked up; "not in the index" or "could not check" shows as a warning, never blocks.
- Override = a ticked id that is in no annotator's answer for that KB. Then show "This is an override. Add a note." and require it. **Confirm** is enabled when every KB has a tick and the override rule is met.
- After a successful save, open the next row of the queue; after the last, return to the list. On failure keep the card open and show the server message; the message lives in the page so it survives the card resetting. The card is keyed by `idx` + `version`, so new answers reset its ticks.
- Every save or bulk call (success or failure) invalidates `review`, `agreement` and `review-summary` queries.
- `ConceptName`: react-query `['entity', session, kb, id]`, `staleTime: Infinity`, `retry: false`; `NIL` renders "NIL (no match)" without a lookup; found → name with the id in muted text; otherwise the id.
- Existing Tailwind classes; works at phone width; buttons and checkboxes have accessible names; errors use `role="alert"`.

- [ ] **Step 1:** Types and client functions.
- [ ] **Step 2:** Move `highlightSpan` and `ContextCard` to `components/ContextCard.tsx` unchanged; import it in `Annotate.tsx`.
- [ ] **Step 3:** Build the four review components and rewrite `Review.tsx` (page state and mutations only).
- [ ] **Step 4:** `cd web && npx tsc --noEmit && npm run build` → both succeed; Python suite still passes.
- [ ] **Step 5:** Commit `feat(web): review rows by accepting or combining answers`.

---

### Task 6: Session list, redirect and docs

**Files:**
- Modify: `web/src/pages/Home.tsx`, `web/src/App.tsx`, `README.md`, `deploy/hosted/README.md`

**Interfaces — consumes:** `api.getReviewSummary` and `ReviewSummary` (Task 5).

- For a signed-in `reviewer` each session card shows a bar "D / T decided" from `['review-summary', name]`, the Complete badge when `D == T`, and one primary **Review** button. No Annotate button, no export menu, no progress request.
- Admins keep Review, configure, export and Annotate; annotators and auth-off are unchanged.
- `/annotate/:sessionName` for a signed-in reviewer redirects (replace) to `/review/:sessionName`.
- Docs: rewrite the reviewer, Review page and final export paragraphs of `deploy/hosted/README.md` (statuses, accept or combine, override note, bulk confirm rule, export columns and reasons, reviewers cannot annotate, old decisions shown as `legacy`, back up `helix.db` before upgrading) and the role sentence in `README.md`.

- [ ] **Step 1:** Home and App changes.
- [ ] **Step 2:** `cd web && npx tsc --noEmit && npm run build` → succeed.
- [ ] **Step 3:** Docs.
- [ ] **Step 4:** Commit `feat(web): reviewers review only; docs`.

---

## Final verification (controller)

- Browser pass in auth mode with a seeded session, two annotators and a reviewer: accept, combine, override with note, bulk confirm, a changed row, export download, reviewer session list and redirect, phone width.
- Whole-branch review, then push to `feature/user-management` (PR #7) and update its description.
