"""Where one row stands in review: its status, what the reviewer may tick, and
why it is or is not in the final export.

Pure functions over human answers and the stored decision; no database or web
imports.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from eltool.answers import Answer, is_blank, is_complete, join_ids, normalise

STATUSES = ("disagree", "single", "agreed", "changed", "decided", "waiting")

_EXPORT_REASONS = {
    "decided": "",
    "disagree": "disagree",
    "single": "single_annotator",
    "agreed": "awaiting_review",
    "changed": "changed_since_review",
}


@dataclass
class RowState:
    status: str
    answers: dict[int, Answer]   # non-blank human answers, by user id
    decision: dict | None        # the stored decision, only when it covers every current KB
    pending: int                 # annotator_ids without a complete answer
    bulk_eligible: bool
    version: str                 # changes when an answer, the KB list or the decision changes


def snapshot(answers: Mapping[int, Answer]) -> dict[str, dict[str, str]]:
    """Answers in the stored ``based_on`` form: {"<user id>": {kb: "C1|C2"}}."""
    return {str(user): {kb: join_ids(ids) for kb, ids in answer.items()}
            for user, answer in answers.items()}


def _answers(raw: Mapping, kb_names: Sequence[str]) -> dict:
    """Stored values per user as answers over ``kb_names``, blank ones dropped."""
    normalised = {user: normalise(values, kb_names) for user, values in raw.items()}
    return {user: answer for user, answer in normalised.items() if not is_blank(answer)}


def row_state(raw: Mapping[int, Mapping[str, str]], decision: dict | None,
              kb_names: Sequence[str], annotator_ids: Collection[int]) -> RowState:
    """The state of one row from each user's latest human values (``raw``), the
    latest stored decision and the ids of the session's current annotators.

    A decision with no ids for a current KB (one added to the session since)
    does not decide the row. One without ``based_on`` was saved before changes
    were tracked and is never "changed".
    """
    answers = _answers(raw, kb_names)
    covering = None
    if decision is not None and is_complete(normalise(decision["kb_values"], kb_names)):
        covering = decision

    if covering is not None:
        based_on = covering["based_on"]
        unchanged = based_on is None or \
            snapshot(answers) == snapshot(_answers(based_on, kb_names))
        status = "decided" if unchanged else "changed"
    elif not answers:
        status = "waiting"
    elif len(answers) == 1:
        status = "single"
    else:
        first, *rest = answers.values()
        status = "agreed" if all(answer == first for answer in rest) else "disagree"

    pending = sum(user not in answers or not is_complete(answers[user])
                  for user in set(annotator_ids))
    bulk_eligible = status == "agreed" and pending == 0 \
        and is_complete(next(iter(answers.values())))

    state = [snapshot(answers), list(kb_names),
             None if decision is None else decision["id"]]
    version = hashlib.sha256(
        json.dumps(state, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return RowState(status, answers, covering, pending, bulk_eligible, version)


def options(answers: Mapping[int, Answer], kb_names: Sequence[str]) -> dict[str, list[dict]]:
    """Every proposed concept per KB, sorted by id: who proposed it, and whether
    it starts ticked (only when two or more answered and all of them chose it)."""
    out: dict[str, list[dict]] = {}
    for kb in kb_names:
        proposers: dict[str, list[int]] = {}
        for user in sorted(answers):
            for concept in answers[user].get(kb, ()):
                proposers.setdefault(concept, []).append(user)
        out[kb] = [{"id": concept, "user_ids": users,
                    "preticked": len(answers) >= 2 and len(users) == len(answers)}
                   for concept, users in sorted(proposers.items())]
    return out


def export_reason(status: str, script_only: bool) -> str:
    """The final export's ``_review_reason`` for a row ("" when decided).
    ``script_only``: the row has script decisions but no human ones."""
    if status == "waiting":
        return "script_only" if script_only else "no_decisions"
    return _EXPORT_REASONS[status]
