"""Looking a KB id up in the index.

A manually typed id is currently accepted verbatim, so a typo becomes the
annotation. Resolving it against the index turns that box into a check — and
tells the annotator when a concept simply is not indexed, which on MedMentions
is 40% of gold answers.
"""

from __future__ import annotations

import pytest

from api.main import _entity_url


@pytest.mark.parametrize(
    "base,expected",
    [
        ("http://127.0.0.1:8765/query", "http://127.0.0.1:8765/entity/C1"),
        ("http://127.0.0.1:8765/query/", "http://127.0.0.1:8765/entity/C1"),
        ("http://host:8765", "http://host:8765/entity/C1"),
        ("http://host:8765/", "http://host:8765/entity/C1"),
        ("https://kb.example.com/api/query", "https://kb.example.com/api/entity/C1"),
    ],
)
def test_entity_url_is_derived_from_the_query_endpoint(base, expected):
    assert _entity_url(base, "C1") == expected


def test_ids_are_url_escaped():
    """Composite dictionary ids contain '|', which is not URL-safe."""
    assert _entity_url("http://h/query", "601367|D002544") == "http://h/entity/601367%7CD002544"


def test_path_traversal_in_an_id_cannot_escape_the_entity_route():
    assert "/entity/" in _entity_url("http://h/query", "../../admin")
    assert ".." not in _entity_url("http://h/query", "../../admin")
