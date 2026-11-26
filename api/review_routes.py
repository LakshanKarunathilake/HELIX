"""Reviewer endpoints: the rows to review, agreement, the reviewer's decisions and
the final export.

The decision log (``decisions.latest_human_or_script``) is the source of truth,
not the ``annotations/*.csv`` layers: a user counts only through their logged
human decisions, and a removed member's decisions still count. Nothing is final
until a reviewer decides it; the rules are in ``eltool.adjudication``.
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Collection
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from eltool import decisions, userdb
from eltool.adjudication import RowState, export_reason, options, row_state, snapshot
from eltool.agreement import pairwise_agreement
from eltool.answers import (
    Answer, basis_for, is_complete, join_ids, mixes_nil, normalise, parse_ids,
)
from eltool.auth import require_reviewer, session_access
from eltool.config import SessionConfig
from eltool.hosting import auth_enabled
from eltool.store import load_input, spreadsheet_safe_cell

router = APIRouter()

STALE_MSG = "The answers for this row changed since you opened it. Check it again."
NO_ANSWER_MSG = "No annotator has answered this row yet."
OVERRIDE_NOTE_MSG = ("Add a note: this answer is an override (it includes a concept no "
                     "annotator chose).")

# row status -> the count it falls under
_COUNT_OF = {"disagree": "to_decide", "single": "to_decide", "changed": "to_decide",
             "agreed": "agreed", "decided": "decided", "waiting": "waiting"}
_EXPORT_COLUMNS = ("_basis", "_decided_by", "_needs_review", "_review_reason")

HumanRows = dict[int, dict[int, decisions.Decision]]   # row -> user id -> decision


def reviewer_access(name: str, request: Request) -> userdb.User:
    """Dependency: auth mode only (404 otherwise), then membership (404),
    then the reviewer or admin role (403)."""
    if not auth_enabled():
        raise HTTPException(404, "Not found")
    session_access(name, request)
    return require_reviewer(request)


def _session(name: str) -> tuple[SessionConfig, pd.DataFrame, list[str]]:
    from api.main import _load_cfg   # api.main includes this router; import late
    cfg = _load_cfg(name)
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    return cfg, df, [k.name for k in cfg.kbs]


def _check_row(idx: int, df: pd.DataFrame) -> None:
    if not 0 <= idx < len(df):
        raise HTTPException(404, "Row out of range")


def _annotator_ids(session_name: str) -> list[int]:
    """The session's current annotators: those a row can still be waiting on."""
    return [u.id for u in userdb.members(session_name)
            if u.active and u.role == "annotator"]


def _user_names() -> dict[int, str]:
    return {u.id: u.name for u in userdb.list_users()}


def _name_of(user_id: int, names: dict[int, str]) -> str:
    return names.get(user_id, f"user {user_id}")


def _human_rows(latest: dict[tuple[int, int], decisions.Decision]) -> HumanRows:
    """The latest decisions a person made; script-written ones are left out."""
    rows: HumanRows = {}
    for (user_id, row), d in latest.items():
        if d.actor == "human":
            rows.setdefault(row, {})[user_id] = d
    return rows


def _state(idx: int, human: HumanRows, adjudications: dict[int, dict], kb_names: list[str],
           annotator_ids: Collection[int] = ()) -> RowState:
    """One row's state. ``annotator_ids`` only affects ``pending`` and
    ``bulk_eligible``."""
    raw = {user_id: d.kb_values for user_id, d in human.get(idx, {}).items()}
    return row_state(raw, adjudications.get(idx), kb_names, annotator_ids)


def _counts(states: list[RowState]) -> dict[str, int]:
    found = Counter(_COUNT_OF[s.status] for s in states)
    return {key: found[key] for key in ("to_decide", "agreed", "decided", "waiting")}


def _column(df: pd.DataFrame, column: str | None) -> list[str]:
    """A column's values, or "" per row when the session or the file lacks it."""
    if column and column in df.columns:
        return [str(v) for v in df[column]]
    return [""] * len(df)


def _ids_json(answer: Answer) -> dict[str, list[str]]:
    return {kb: list(ids) for kb, ids in answer.items()}


def _joined(answer: Answer) -> dict[str, str]:
    return {kb: join_ids(ids) for kb, ids in answer.items()}


def _decision_json(adj: dict, kb_names: list[str], reviewer_name: str) -> dict:
    return {"row_idx": adj["row_idx"], "kb_values": adj["kb_values"],
            "kb_ids": _ids_json(normalise(adj["kb_values"], kb_names)),
            "note": adj["note"], "basis": adj["basis"], "source_users": adj["source_users"],
            "reviewer_id": adj["reviewer_id"], "reviewer_name": reviewer_name,
            "ts": adj["ts"]}


@router.get("/api/sessions/{name}/review", dependencies=[Depends(reviewer_access)])
def review(name: str):
    cfg, df, kb_names = _session(name)
    human = _human_rows(decisions.latest_human_or_script(name))
    adjudications = decisions.current_adjudications(name)
    annotator_ids = _annotator_ids(name)
    names = _user_names()
    labels = _column(df, cfg.label_column)
    doc_ids = _column(df, cfg.doc_id_col)
    states = [_state(idx, human, adjudications, kb_names, annotator_ids)
              for idx in range(len(df))]
    rows = []
    for idx, state in enumerate(states):
        if state.status == "waiting":
            continue
        users = sorted(state.answers, key=lambda u: (_name_of(u, names), u))
        decision = state.decision
        rows.append({
            "idx": idx,
            "label": labels[idx],
            "doc_id": doc_ids[idx].strip(),
            "status": state.status,
            "version": state.version,
            "pending": state.pending,
            "bulk_eligible": state.bulk_eligible,
            "answers": [{"user_id": u, "user_name": _name_of(u, names),
                         "kb_ids": _ids_json(state.answers[u]),
                         "complete": is_complete(state.answers[u]),
                         "ts": human[idx][u].ts} for u in users],
            "options": options(state.answers, kb_names),
            "decision": None if decision is None else _decision_json(
                decision, kb_names, _name_of(decision["reviewer_id"], names)),
        })
    return {"kb_names": kb_names, "total": len(df),
            "has_context": bool(cfg.context_folder and cfg.doc_id_col),
            "counts": _counts(states), "rows": rows}


@router.get("/api/sessions/{name}/review/summary", dependencies=[Depends(reviewer_access)])
def review_summary(name: str):
    _, df, kb_names = _session(name)
    human = _human_rows(decisions.latest_human_or_script(name))
    adjudications = decisions.current_adjudications(name)
    states = [_state(idx, human, adjudications, kb_names) for idx in range(len(df))]
    return {"total": len(df), **_counts(states)}


@router.get("/api/sessions/{name}/review/rows/{idx}",
            dependencies=[Depends(reviewer_access)])
def review_row(name: str, idx: int):
    """What the reviewer reads to judge one row. Unlike the annotator's row
    endpoint it creates no output layer."""
    from api.main import _read_context
    cfg, df, _ = _session(name)
    _check_row(idx, df)
    row = df.iloc[idx].to_dict()
    doc_id = str(row.get(cfg.doc_id_col, "")).strip() if cfg.doc_id_col else ""
    context_text = None
    if cfg.context_folder and doc_id:
        context_text = _read_context(Path(cfg.context_folder), doc_id)
    return {
        "idx": idx,
        "label": row.get(cfg.label_column, ""),
        "doc_id": doc_id,
        "info": {c: row.get(c, "") for c in cfg.info_columns},
        "context_text": context_text,
        "entity_span": str(row.get(cfg.span_col, "")) if cfg.span_col else None,
    }


@router.get("/api/sessions/{name}/agreement", dependencies=[Depends(reviewer_access)])
def agreement(name: str):
    _, _, kb_names = _session(name)
    latest = decisions.latest_human_or_script(name)
    human = [d for d in latest.values() if d.actor == "human"]
    by_kb = {}
    for kb in kb_names:
        values: dict[int, dict[int, str]] = {}
        for d in human:
            if kb in d.kb_values:
                values.setdefault(d.user_id, {})[d.row_idx] = d.kb_values[kb]
        by_kb[kb] = pairwise_agreement(values)
    return {"by_kb": by_kb, "excluded_script_rows": len(latest) - len(human)}


class AdjudicationRequest(BaseModel):
    """A reviewer's answer for one row. How it was reached is derived here, so
    any ``basis`` or ``source_users`` the client sends is ignored."""
    row_idx: int
    kb_values: dict[str, str]
    note: str = ""
    version: str          # the row's version on the screen the reviewer decided from


def _check_values(kb_values: dict[str, str], kb_names: list[str]) -> None:
    # A partial adjudication would publish blanks as reviewed final values,
    # so it must name exactly the session's KBs.
    unknown = sorted(set(kb_values) - set(kb_names))
    missing = [kb for kb in kb_names if kb not in kb_values]
    if unknown or missing:
        problems = []
        if missing:
            problems.append(f"missing KB: {', '.join(missing)}")
        if unknown:
            problems.append(f"unknown KB: {', '.join(unknown)}")
        raise HTTPException(400, "An adjudication must cover every KB of the session ("
                            + "; ".join(problems) + ").")
    # A blank value would publish an empty cell as a reviewed final value.
    blank = [kb for kb in kb_names if not parse_ids(kb_values[kb])]
    if blank:
        raise HTTPException(400, f"Enter a value for every KB (blank: {', '.join(blank)}); "
                            "use NIL for no match.")
    mixed = [kb for kb in kb_names if mixes_nil(kb_values[kb])]
    if mixed:
        raise HTTPException(400, f"NIL cannot be combined with a concept ({', '.join(mixed)}).")


@router.post("/api/sessions/{name}/adjudications")
def adjudicate(name: str, body: AdjudicationRequest,
               reviewer: userdb.User = Depends(reviewer_access)):
    _, df, kb_names = _session(name)
    _check_row(body.row_idx, df)
    _check_values(body.kb_values, kb_names)
    final = normalise(body.kb_values, kb_names)
    with decisions.review_transaction(name) as tx:
        state = _state(body.row_idx, _human_rows(tx.latest()), tx.adjudications(), kb_names)
        if state.version != body.version:
            raise HTTPException(409, STALE_MSG)
        if not state.answers:
            raise HTTPException(409, NO_ANSWER_MSG)
        basis, source_users = basis_for(final, state.answers)
        if basis == "override" and not body.note.strip():
            raise HTTPException(400, OVERRIDE_NOTE_MSG)
        adj = tx.add(body.row_idx, reviewer.id, _joined(final), body.note, basis=basis,
                     source_users=source_users, based_on=snapshot(state.answers))
    return _decision_json(adj, kb_names, reviewer.name)


class BulkRow(BaseModel):
    row_idx: int
    version: str


class BulkRequest(BaseModel):
    rows: list[BulkRow]


@router.post("/api/sessions/{name}/adjudications/bulk")
def adjudicate_bulk(name: str, body: BulkRequest,
                    reviewer: userdb.User = Depends(reviewer_access)):
    """Confirm the shared answer of each row that still qualifies, and say why
    each other row was skipped."""
    _, df, kb_names = _session(name)
    annotator_ids = _annotator_ids(name)   # read before the transaction takes the lock
    wanted: dict[int, str] = {}
    for row in body.rows:
        wanted.setdefault(row.row_idx, row.version)
    confirmed: list[int] = []
    skipped: list[dict] = []
    with decisions.review_transaction(name) as tx:
        human = _human_rows(tx.latest())
        adjudications = tx.adjudications()
        for idx, version in wanted.items():
            state = None
            if 0 <= idx < len(df):
                state = _state(idx, human, adjudications, kb_names, annotator_ids)
            if state is None:
                reason = "out_of_range"
            elif state.version != version:
                reason = "changed"
            elif state.status != "agreed":
                reason = "not_agreed"
            elif not state.bulk_eligible:
                reason = "pending"
            else:
                shared = next(iter(state.answers.values()))
                tx.add(idx, reviewer.id, _joined(shared), "", basis="bulk_agreed",
                       source_users=sorted(state.answers),
                       based_on=snapshot(state.answers))
                confirmed.append(idx)
                continue
            skipped.append({"row_idx": idx, "reason": reason})
    return {"confirmed": confirmed, "skipped": skipped}


def final_export(name: str) -> Response:
    """The reviewed set: input columns, each KB's value, ``_basis``, ``_decided_by``,
    ``_needs_review`` and ``_review_reason``. Only decided rows carry values; every
    other row is blank and says why. The caller checks access."""
    _, df, kb_names = _session(name)
    latest = decisions.latest_human_or_script(name)
    human = _human_rows(latest)
    script_only = {row for _, row in latest} - human.keys()
    adjudications = decisions.current_adjudications(name)
    names = _user_names()
    columns = list(dict.fromkeys([*df.columns, *kb_names, *_EXPORT_COLUMNS]))
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow([spreadsheet_safe_cell(c) for c in columns])
    for idx, record in enumerate(df.to_dict("records")):
        state = _state(idx, human, adjudications, kb_names)
        record.update(dict.fromkeys([*kb_names, *_EXPORT_COLUMNS], ""))
        record["_review_reason"] = export_reason(state.status, idx in script_only)
        if state.status == "decided":
            decision = state.decision
            record.update({kb: decision["kb_values"].get(kb, "") for kb in kb_names})
            record["_basis"] = decision["basis"]
            record["_decided_by"] = _name_of(decision["reviewer_id"], names)
        else:
            record["_needs_review"] = "1"
        out.writerow([spreadsheet_safe_cell(record[c]) for c in columns])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}_final.csv"'})
