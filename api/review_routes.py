"""Reviewer endpoints: every user's decisions per row, agreement, adjudication, final export.

The decision log (``decisions.latest_human_or_script``) is the source of truth,
not the ``annotations/*.csv`` layers: a user counts only through their logged
decisions, and a removed member's decisions still count.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from eltool import decisions, userdb
from eltool.agreement import final_rows, pairwise_agreement
from eltool.auth import require_reviewer, session_access
from eltool.config import SessionConfig
from eltool.hosting import auth_enabled
from eltool.store import load_input, spreadsheet_safe_cell

router = APIRouter()


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


def _user_name(user_id: int, cache: dict[int, str]) -> str:
    if user_id not in cache:
        user = userdb.get_user(user_id)
        cache[user_id] = user.name if user else f"user {user_id}"
    return cache[user_id]


def _kb_tuple(values: dict, kb_names: list[str]) -> tuple[str, ...]:
    return tuple(values.get(kb, "") for kb in kb_names)


def _human_values(session_name: str) -> tuple[dict[int, dict[int, dict]], set[int], int]:
    """Human latest decisions as user -> row -> kb_values, the rows that only
    have script decisions, and how many (user, row) latest decisions are script."""
    human: dict[int, dict[int, dict]] = {}
    script_rows: set[int] = set()
    script_count = 0
    for (user_id, row), d in decisions.latest_human_or_script(session_name).items():
        if d.actor == "human":
            human.setdefault(user_id, {})[row] = d.kb_values
        else:
            script_rows.add(row)
            script_count += 1
    human_rows = {row for rows in human.values() for row in rows}
    return human, script_rows - human_rows, script_count


def _adjudication_json(adj: dict, names: dict[int, str]) -> dict:
    return {"row_idx": adj["row_idx"], "kb_values": adj["kb_values"], "note": adj["note"],
            "reviewer_id": adj["reviewer_id"],
            "reviewer_name": _user_name(adj["reviewer_id"], names), "ts": adj["ts"]}


@router.get("/api/sessions/{name}/review", dependencies=[Depends(reviewer_access)])
def review(name: str):
    cfg, df, kb_names = _session(name)
    names: dict[int, str] = {}
    by_row: dict[int, list] = {}
    for (user_id, row), d in sorted(decisions.latest_human_or_script(name).items(),
                                    key=lambda item: item[1].id):
        by_row.setdefault(row, []).append(d)
    adjudications = decisions.current_adjudications(name)
    rows = []
    for idx in sorted(by_row.keys() | adjudications.keys()):
        if not 0 <= idx < len(df):
            continue
        ds = by_row.get(idx, [])
        human = {_kb_tuple(d.kb_values, kb_names) for d in ds if d.actor == "human"}
        adj = adjudications.get(idx)
        rows.append({
            "idx": idx,
            "label": str(df.iloc[idx][cfg.label_column]),
            "decisions": [{"user_id": d.user_id, "user_name": _user_name(d.user_id, names),
                           "actor": d.actor, "script": d.actor != "human",
                           "kb_values": d.kb_values, "llm_used": d.llm_used, "ts": d.ts}
                          for d in ds],
            "disagree": len(human) > 1,
            "adjudication": _adjudication_json(adj, names) if adj else None,
        })
    return {"kb_names": kb_names, "rows": rows}


@router.get("/api/sessions/{name}/agreement", dependencies=[Depends(reviewer_access)])
def agreement(name: str):
    _, _, kb_names = _session(name)
    human, _, script_count = _human_values(name)
    by_kb = {}
    for kb in kb_names:
        values = {}
        for user_id, rows in human.items():
            picked = {row: kv[kb] for row, kv in rows.items() if kb in kv}
            if picked:
                values[user_id] = picked
        by_kb[kb] = pairwise_agreement(values)
    return {"by_kb": by_kb, "excluded_script_rows": script_count}


class AdjudicationRequest(BaseModel):
    row_idx: int
    kb_values: dict[str, str]
    note: str = ""


@router.post("/api/sessions/{name}/adjudications")
def adjudicate(name: str, body: AdjudicationRequest,
               reviewer: userdb.User = Depends(reviewer_access)):
    _, df, kb_names = _session(name)
    if not 0 <= body.row_idx < len(df):
        raise HTTPException(404, "Row out of range")
    unknown = sorted(set(body.kb_values) - set(kb_names))
    if unknown:
        raise HTTPException(400, f"Unknown KB: {', '.join(unknown)}")
    adj = decisions.add_adjudication(name, body.row_idx, reviewer.id, body.kb_values,
                                     body.note)
    return _adjudication_json(adj, {})


def final_export(name: str) -> Response:
    """The reviewed set: input columns, each KB's final value, ``_needs_review``
    and ``_review_reason`` (rules in ``agreement.final_rows``). The caller checks access."""
    _, df, kb_names = _session(name)
    human, script_only, _ = _human_values(name)
    adjudications = {row: a["kb_values"]
                     for row, a in decisions.current_adjudications(name).items()}
    final = final_rows(human, adjudications, len(df), kb_names, frozenset(script_only))
    columns = list(df.columns) + [kb for kb in kb_names if kb not in df.columns] + \
        ["_needs_review", "_review_reason"]
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow([spreadsheet_safe_cell(c) for c in columns])
    for idx, record in enumerate(df.to_dict("records")):
        fr = final[idx]
        record.update(fr.values)
        record["_needs_review"] = "1" if fr.needs_review else ""
        record["_review_reason"] = fr.reason
        out.writerow([spreadsheet_safe_cell(record[c]) for c in columns])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}_final.csv"'})
