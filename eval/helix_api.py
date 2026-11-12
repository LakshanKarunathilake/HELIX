"""Thin client for the HELIX HTTP API, used by the AI annotator runner."""
from __future__ import annotations

import requests


class HelixAPI:
    def __init__(self, base: str = "http://127.0.0.1:8000", timeout: float = 120.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._cfg: dict[str, dict] = {}

    def _url(self, session: str, suffix: str) -> str:
        return f"{self.base}/api/sessions/{session}{suffix}"

    def _get(self, url: str) -> dict:
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, url: str, body: dict) -> dict:
        r = requests.post(url, json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def config(self, session: str) -> dict:
        if session not in self._cfg:
            self._cfg[session] = self._get(self._url(session, ""))
        return self._cfg[session]

    def row(self, session: str, idx: int) -> dict:
        return self._get(self._url(session, f"/rows/{idx}"))

    def candidates(self, session: str, label: str, kb: str, context: str | None) -> dict:
        kbs = {k["name"]: k for k in self.config(session).get("kbs", [])}
        k = kbs.get(kb, {})
        body = {"label": label, "kb_name": kb,
                "enable_dense": k.get("enable_dense", True),
                "enable_lexical": k.get("enable_lexical", False),
                "context": context}
        return self._post(self._url(session, "/candidates"), body)

    def graph(self, session: str, ids: list[str]) -> dict:
        return self._post(self._url(session, "/graph/candidates"), {"ids": ids})
