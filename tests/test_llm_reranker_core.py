import hashlib
import json
from types import SimpleNamespace

import pytest

from eltool import llm_reranker
from eltool.llm_reranker import (
    DEFAULT_SYSTEM_PROMPT,
    LLMRerankError,
    LLMReranker,
    _candidate_dict,
)


@pytest.fixture
def fake_cands():
    return [_candidate_dict({"id": f"C{i}", "label": f"label {i}"}) for i in (1, 2, 3)]


def test_rerank_cache_key_is_unchanged(fake_cands):
    r = LLMReranker(model="m", provider="openai", base_url="http://x/v1", api_key="k")
    legacy = hashlib.sha256(json.dumps({
        "provider": "openai", "base_url": "http://x/v1", "model": "m",
        "prompt": DEFAULT_SYSTEM_PROMPT, "mention": "lung", "context": "ctx",
        "candidates": [{"id": c["id"], "label": c["label"]} for c in fake_cands],
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert r._cache_key("lung", "ctx", fake_cands) == legacy


def test_namespace_and_extra_change_the_key(fake_cands):
    class NS(LLMReranker):
        CACHE_NAMESPACE = "x"

    a = LLMReranker(model="m", api_key="k")._cache_key("lung", "", fake_cands)
    b = NS(model="m", api_key="k")._cache_key("lung", "", fake_cands)
    c = NS(model="m", api_key="k")._cache_key("lung", "", fake_cands, extra={"s": "C1"})
    assert len({a, b, c}) == 3


def test_cache_only_miss_raises(tmp_path, monkeypatch, fake_cands):
    monkeypatch.setattr(llm_reranker, "_CACHE_DIR", tmp_path)
    r = LLMReranker(model="m", api_key="k", cache_only=True)
    with pytest.raises(LLMRerankError, match="cache_only"):
        r.rank("lung", "", fake_cands)


def test_temperature_reaches_openai_client(fake_cands):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        call = SimpleNamespace(function=SimpleNamespace(
            arguments=json.dumps({"entity_id": "C2", "confidence": 0.9, "rationale": "r"})))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(tool_calls=[call]))])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    r = LLMReranker(model="m", provider="openai", api_key="k", temperature=0, use_cache=False)
    r._client = fake
    res = r.rank("lung", "", fake_cands)
    assert res.entity_id == "C2"
    assert captured["temperature"] == 0
    assert captured["tool_choice"]["function"]["name"] == "select_entity"

    captured.clear()
    r2 = LLMReranker(model="m", provider="openai", api_key="k", use_cache=False)
    r2._client = fake
    r2.rank("lung", "", fake_cands)
    assert "temperature" not in captured
