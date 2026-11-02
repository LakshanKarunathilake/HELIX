"""Which candidate concept types fit a mention's given type.

The setting gives each mention a type and nothing else about its concept, so a
candidate is kept only when one of its UMLS semantic types (TUIs) fits that type.

  - MedMentions ST21pv: mention types are 21 UMLS semantic types. A candidate fits
    when one of its TUIs is the mention's TUI or a descendant of it in the UMLS
    semantic network (tree numbers from MRSTY, e.g. B2.2.1 -> B2.2.1.2.1).
  - BC5CDR: mention types are Chemical / Disease. A candidate fits when one of its
    TUIs is in the CHEM / DISO semantic group (SemGroups.txt). MeSH ids get their
    TUIs through UMLS: MRCONSO (SAB=MSH, CODE=MeSH id) -> CUI -> MRSTY.

Only the UMLS semantic network and the vocabulary are used; no gold labels.
"""
from __future__ import annotations

from pathlib import Path

BC5CDR_GROUPS = {"Chemical": {"CHEM"}, "Disease": {"DISO"}}


def parse_tuis(s: str | None) -> set[str]:
    """'T116:Amino Acid, ...||T121:Pharmacologic Substance' -> {'T116', 'T121'}."""
    if not s:
        return set()
    return {part.split(":", 1)[0].strip() for part in s.split("||") if part.strip()}


def load_tui_tree(mrsty: str | Path) -> dict[str, str]:
    """TUI -> semantic-network tree number (STN), read from MRSTY.RRF."""
    tree: dict[str, str] = {}
    with open(mrsty, encoding="utf-8") as f:
        for line in f:
            cols = line.split("|")
            tree.setdefault(cols[1], cols[2])
    return tree


def subtree(tui: str, tree: dict[str, str]) -> set[str]:
    """The TUI itself plus every TUI below it in the semantic network."""
    root = tree.get(tui)
    if root is None:
        return set()
    return {t for t, stn in tree.items() if stn == root or stn.startswith(root + ".")}


def load_cui_tuis(mrsty: str | Path, cuis: set[str] | None = None) -> dict[str, set[str]]:
    """CUI -> its TUIs, optionally only for the given CUIs."""
    out: dict[str, set[str]] = {}
    with open(mrsty, encoding="utf-8") as f:
        for line in f:
            cui, tui = line.split("|", 2)[:2]
            if cuis is None or cui in cuis:
                out.setdefault(cui, set()).add(tui)
    return out


def load_semgroups(path: str | Path) -> dict[str, str]:
    """TUI -> semantic group abbreviation, read from SemGroups.txt."""
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            cols = line.rstrip("\n").split("|")
            if len(cols) >= 3:
                out[cols[2]] = cols[0]
    return out


def load_mesh_tuis(mrconso: str | Path, mrsty: str | Path) -> dict[str, set[str]]:
    """MeSH id (D...... / C......) -> TUIs, through the UMLS concepts holding MSH atoms."""
    mesh_cuis: dict[str, set[str]] = {}
    with open(mrconso, encoding="utf-8") as f:
        for line in f:
            cols = line.split("|")
            if cols[11] == "MSH":
                mesh_cuis.setdefault(cols[13], set()).add(cols[0])
    cui_tuis = load_cui_tuis(mrsty, {c for cs in mesh_cuis.values() for c in cs})
    out: dict[str, set[str]] = {}
    for code, cs in mesh_cuis.items():
        tuis = set().union(*(cui_tuis.get(c, set()) for c in cs))
        if tuis:
            out[code] = tuis
    return out


class TypeScope:
    """Mention type -> the candidate TUIs it admits."""

    def __init__(self, allowed: dict[str, set[str]]):
        self._allowed = allowed

    @classmethod
    def medmentions(cls, tree: dict[str, str], mention_types) -> "TypeScope":
        return cls({t: subtree(t, tree) for t in set(mention_types)})

    @classmethod
    def bc5cdr(cls, semgroups: dict[str, str]) -> "TypeScope":
        return cls({mt: {t for t, g in semgroups.items() if g in groups}
                    for mt, groups in BC5CDR_GROUPS.items()})

    def allowed(self, mention_type: str) -> set[str] | None:
        """None when the mention type is unknown: then nothing is filtered."""
        return self._allowed.get(mention_type)

    def compatible(self, mention_type: str, cand_tuis: set[str]) -> bool:
        """A candidate is dropped only when its known types all miss: an unknown mention
        type, or a candidate with no known types (an OMIM-only dictionary entry), is kept."""
        allowed = self.allowed(mention_type)
        return True if allowed is None or not cand_tuis else bool(allowed & cand_tuis)
