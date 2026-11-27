"""Streaming reader for NLM's MeSH descriptor file (``desc<year>.xml``/``.gz``).

The file is hundreds of megabytes uncompressed, so records are parsed one at a
time and dropped once read.
"""
from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Descriptor:
    ui: str
    name: str                       # preferred name
    terms: tuple[str, ...]          # name first, then entry terms; case-insensitively unique
    tree_numbers: tuple[str, ...]
    definition: str                 # preferred concept's ScopeNote, stripped, "" if none
    see_also: tuple[str, ...]       # SeeRelatedDescriptor UIs


def read_descriptors(path: Path, branch: str = "C") -> Iterator[Descriptor]:
    """Yield, in file order, descriptors with a tree number starting ``branch``."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        events = ET.iterparse(stream, events=("start", "end"))
        _, root = next(events)
        for event, element in events:
            if event != "end" or element.tag != "DescriptorRecord":
                continue
            tree_numbers = _texts(element, "TreeNumberList/TreeNumber")
            if any(number.startswith(branch) for number in tree_numbers):
                yield _descriptor(element, tree_numbers)
            # Clearing the root, not just the record, leaves no empty shells behind.
            root.clear()


def _descriptor(record: ET.Element, tree_numbers: tuple[str, ...]) -> Descriptor:
    name = record.findtext("DescriptorName/String", "").strip()
    unique: dict[str, str] = {}
    for term in (name, *_texts(record, "ConceptList/Concept/TermList/Term/String")):
        unique.setdefault(term.casefold(), term)
    return Descriptor(
        ui=record.findtext("DescriptorUI", "").strip(),
        name=name,
        terms=tuple(unique.values()),
        tree_numbers=tree_numbers,
        definition=record.findtext(
            "ConceptList/Concept[@PreferredConceptYN='Y']/ScopeNote", ""
        ).strip(),
        see_also=_texts(
            record,
            "SeeRelatedList/SeeRelatedDescriptor/DescriptorReferredTo/DescriptorUI",
        ),
    )


def _texts(record: ET.Element, path: str) -> tuple[str, ...]:
    """Stripped text of every element at ``path``, skipping empty ones."""
    stripped = ((element.text or "").strip() for element in record.findall(path))
    return tuple(text for text in stripped if text)
