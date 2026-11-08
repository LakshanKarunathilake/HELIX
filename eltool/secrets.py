"""Write-only secrets: the LLM key, KB headers and the Neo4j password.

The API masks them on GET and keeps the stored value on PUT when the browser
sends them blank. A stored secret is only reused while it would still go to
the same address, so it cannot be redirected to a host the caller chose.
All functions return copies; inputs are never mutated.
"""

from __future__ import annotations

from typing import Any

from eltool.config import SessionConfig


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def mask_session(cfg: SessionConfig) -> dict:
    d = cfg.model_dump()
    d["llm_api_key_set"] = bool(d.get("llm_api_key"))
    d["llm_api_key"] = ""
    for kb in d.get("kbs") or []:
        kb["headers_set"] = (kb.get("headers") or "") not in ("", "{}")
        kb["headers"] = ""
    g = d.get("graph")
    if g:
        g["password_set"] = bool(g.get("password"))
        g["password"] = ""
    return d


def fill_kb_secret(kb: dict, existing: SessionConfig | None) -> dict:
    kb = dict(kb)
    kb.pop("headers_set", None)
    if _blank(kb.get("headers")):
        kb["headers"] = "{}"
        if existing is not None:
            for old in existing.kbs:
                if old.name == kb.get("name") and old.endpoint_url == kb.get("endpoint_url"):
                    kb["headers"] = old.headers
                    break
    return kb


def fill_graph_secret(graph: dict | None, existing: SessionConfig | None) -> dict | None:
    if graph is None:
        return None
    graph = dict(graph)
    graph.pop("password_set", None)
    if _blank(graph.get("password")):
        graph["password"] = ""
        old = existing.graph if existing is not None else None
        if old is not None and old.uri == graph.get("uri") and old.user == graph.get("user"):
            graph["password"] = old.password
    return graph


def merge_secrets(body: dict, existing: SessionConfig) -> dict:
    body = dict(body)
    body.pop("llm_api_key_set", None)
    if _blank(body.get("llm_api_key")):
        body["llm_api_key"] = existing.llm_api_key
    if isinstance(body.get("kbs"), list):
        body["kbs"] = [fill_kb_secret(k, existing) if isinstance(k, dict) else k
                       for k in body["kbs"]]
    if isinstance(body.get("graph"), dict):
        body["graph"] = fill_graph_secret(body["graph"], existing)
    return body
