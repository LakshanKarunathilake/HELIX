"""Comparing annotator answers as concept sets, and how a final answer was reached.

Pure functions; no database or web imports.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

NIL = "NIL"

Answer = dict[str, tuple[str, ...]]  # KB name -> sorted concept ids


def _raw_ids(value: str | None) -> set[str]:
    """Distinct non-blank ids named by a stored value, with any-case "nil" as NIL."""
    parts = (part.strip() for part in (value or "").split("|"))
    return {NIL if part.upper() == NIL else part for part in parts if part}


def parse_ids(value: str | None) -> tuple[str, ...]:
    """Sorted concept ids of a pipe-joined value; NIL beside a concept is dropped."""
    ids = _raw_ids(value)
    if len(ids) > 1:
        ids.discard(NIL)
    return tuple(sorted(ids))


def mixes_nil(value: str | None) -> bool:
    """Whether the raw value names both NIL and a concept."""
    ids = _raw_ids(value)
    return NIL in ids and len(ids) > 1


def join_ids(ids: Iterable[str]) -> str:
    """Ids as one pipe-joined value, sorted and without duplicates."""
    return "|".join(sorted(set(ids)))


def normalise(kb_values: Mapping[str, str], kb_names: Sequence[str]) -> Answer:
    """The answer for exactly ``kb_names``; a missing KB has no ids."""
    return {kb: parse_ids(kb_values.get(kb)) for kb in kb_names}


def is_blank(answer: Answer) -> bool:
    """No KB has an id."""
    return not any(answer.values())


def is_complete(answer: Answer) -> bool:
    """Every KB has an id."""
    return all(answer.values())


def basis_for(final: Answer, answers: Mapping[int, Answer]) -> tuple[str, list[int]]:
    """How ``final`` follows from the annotators' answers, and from whose.

    ``("accepted", users)`` when it equals those users' whole answer. Otherwise
    the users are those sharing an id with it in the same KB, and the basis is
    ``"combined"`` when every final id was proposed for its KB, else
    ``"override"``.
    """
    accepted = sorted(user for user, answer in answers.items() if answer == final)
    if accepted:
        return "accepted", accepted
    sources: set[int] = set()
    all_proposed = True
    for kb, ids in final.items():
        for concept in ids:
            proposers = {user for user, answer in answers.items()
                         if concept in answer.get(kb, ())}
            sources |= proposers
            all_proposed = all_proposed and bool(proposers)
    return ("combined" if all_proposed else "override"), sorted(sources)
