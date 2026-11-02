"""Abbreviations defined in a document, and mentions rewritten with their long forms.

Schwartz & Hearst (2003), "A simple algorithm for identifying abbreviation
definitions in biomedical text": a definition is "long form (SF)" or, when the
parenthesis holds the long form, "SF (long form)". The long form is found by
matching the short form's characters right to left inside the words before the
parenthesis; the short form's first character must start a word.

Only the document text is used, so this fits the setting: no concept ids, and no
assumption that two mentions are the same entity.
"""
from __future__ import annotations

import re

_PAREN = re.compile(r"\(([^()]*)\)")
_SENTENCE_END = re.compile(r"[.!?]\s")


def _valid_short_form(sf: str) -> bool:
    """Schwartz & Hearst's shape test, plus: a capital or a digit, and short or mostly
    capitals -- so ordinary words in parentheses ("(rapamycin)", "(Taxol)") are not taken."""
    if not (2 <= len(sf) <= 10 and len(sf.split()) <= 2 and sf[0].isalnum()
            and any(c.isalpha() for c in sf)):
        return False
    upper, digit = sum(c.isupper() for c in sf), any(c.isdigit() for c in sf)
    return (upper or digit) and (len(sf) <= 4 or upper >= 2 or digit)


def best_long_form(sf: str, lf: str) -> str | None:
    """The shortest tail of lf whose words spell sf, or None (Schwartz & Hearst)."""
    s, l = len(sf) - 1, len(lf) - 1
    while s >= 0:
        c = sf[s].lower()
        if not c.isalnum():
            s -= 1
            continue
        while l >= 0 and (lf[l].lower() != c or (s == 0 and l > 0 and lf[l - 1].isalnum())):
            l -= 1
        if l < 0:
            return None
        l -= 1
        s -= 1
    start = lf.rfind(" ", 0, l + 1) + 1
    out = lf[start:].strip()
    if len(out) <= len(sf) or re.search(rf"(?<!\w){re.escape(sf)}(?!\w)", out):
        return None
    return out


def _window(text_before: str, sf: str) -> str:
    """Words before the parenthesis, inside the sentence, at most min(|sf|+5, 2|sf|) of them."""
    ends = list(_SENTENCE_END.finditer(text_before))
    if ends:
        text_before = text_before[ends[-1].end():]
    words = text_before.split()
    return " ".join(words[-min(len(sf) + 5, 2 * len(sf)):])


def find_abbreviations(text: str) -> dict[str, str]:
    """Short form -> long form for every definition in text; the first definition wins."""
    out: dict[str, str] = {}
    for m in _PAREN.finditer(text):
        inner = re.split(r"[;,]", m.group(1))[0].strip()
        before = text[:m.start()].rstrip()
        if not inner or not before:
            continue
        if _valid_short_form(inner):                      # long form (SF)
            sf, lf = inner, best_long_form(inner, _window(before, inner))
        else:                                             # SF (long form)
            sf = before.split()[-1]
            if not _valid_short_form(sf):
                continue
            lf = best_long_form(sf, inner)
            if lf != inner:                               # the whole parenthesis must be the long form
                lf = None
        if lf and sf not in out:
            out[sf] = lf
    return out


def _token_pattern(sfs) -> re.Pattern | None:
    sfs = sorted(sfs, key=len, reverse=True)
    if not sfs:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(map(re.escape, sfs)) + r")(?![A-Za-z0-9])")


def expand_mention(mention: str, abbrevs: dict[str, str]) -> str | None:
    """The mention with defined short forms replaced by their long forms, or None if unchanged.

    A definition parenthesis inside the mention ("Pseudomonas aeruginosa (Pa)
    infection") is dropped; long forms that themselves hold a short form are
    expanded once ("chronic Pa infection" -> "chronic Pseudomonas aeruginosa infection").
    """
    if not abbrevs:
        return None
    text = mention
    for sf in abbrevs:
        text = re.sub(rf"\s*\(\s*{re.escape(sf)}\s*\)", "", text)
    long_forms = {}
    for sf, lf in abbrevs.items():
        inner = _token_pattern([s for s in abbrevs if s != sf])
        long_forms[sf] = inner.sub(lambda m: abbrevs[m.group(1)], lf) if inner else lf
    text = _token_pattern(abbrevs).sub(lambda m: long_forms[m.group(1)], text)
    text = " ".join(text.split())
    return text if text != mention else None
