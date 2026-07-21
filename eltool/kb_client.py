from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from .config import KBConfig


@dataclass
class Candidate:
    id: str
    label: str
    score: float | None = None
    matched_alias: str = ""
    aliases: list[str] = None
    semantic_groups: str = ""
    semantic_types: str = ""
    definition: str = ""

    def __post_init__(self):
        if self.aliases is None:
            self.aliases = []


def _dotted_get(obj: Any, path: str) -> Any:
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _render_template(template: str, label: str, top_k: int) -> str:
    return template.replace("{label}", json.dumps(label)[1:-1]).replace("{top_k}", str(top_k))


class KBClient:
    def __init__(self, cfg: KBConfig, timeout: float = 15.0):
        self.cfg = cfg
        self.timeout = timeout

    def _headers(self) -> dict:
        return json.loads(self.cfg.headers or "{}")

    def query(self, label: str) -> list[Candidate]:
        rendered = _render_template(self.cfg.request_template, label, self.cfg.top_k)
        try:
            payload = json.loads(rendered)
        except json.JSONDecodeError as e:
            raise ValueError(f"request_template did not render to valid JSON: {e}") from e

        if self.cfg.method == "POST":
            resp = requests.post(
                self.cfg.endpoint_url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        else:
            resp = requests.get(
                self.cfg.endpoint_url,
                params=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        resp.raise_for_status()
        data = resp.json()

        candidates_raw = _dotted_get(data, self.cfg.response_path)
        if not isinstance(candidates_raw, list):
            raise ValueError(
                f"Expected a list at response_path '{self.cfg.response_path}', got {type(candidates_raw).__name__}."
            )

        out: list[Candidate] = []
        for item in candidates_raw:
            cid    = _dotted_get(item, self.cfg.id_field)
            clabel = _dotted_get(item, self.cfg.label_field)
            cscore = _dotted_get(item, self.cfg.score_field)
            if cid is None:
                continue
            try:
                score = float(cscore) if cscore is not None else None
            except (TypeError, ValueError):
                score = None
            # pass-through extra disambiguation fields when the server returns them
            aliases          = item.get("aliases") or []
            matched_alias    = item.get("matched_alias") or item.get("primary_alias") or ""
            out.append(Candidate(
                id=str(cid),
                label=str(clabel) if clabel is not None else "",
                score=score,
                matched_alias=str(matched_alias),
                aliases=[str(a) for a in aliases] if aliases else [],
                semantic_groups=str(item.get("semantic_groups") or ""),
                semantic_types=str(item.get("semantic_types") or ""),
                definition=str(item.get("definition") or ""),
            ))
        return out
