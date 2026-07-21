from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SLUG_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class KBConfig(BaseModel):
    name: str = Field(..., description="Output column name; must be slug-safe and unique.")
    endpoint_url: str
    method: Literal["GET", "POST"] = "POST"
    top_k: int = 10
    request_template: str = Field(
        default='{"label": "{label}", "top_k": {top_k}}',
        description="For POST: JSON body with {label} and {top_k} placeholders. For GET: JSON of query params.",
    )
    response_path: str = Field(
        default="",
        description="Dotted path to the candidate array within the JSON response. Empty = root.",
    )
    id_field: str = "id"
    label_field: str = "label"
    score_field: str = "score"
    headers: str = Field(default="{}", description="Optional JSON object of HTTP headers.")
    enable_dense: bool = True
    enable_lexical: bool = True

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("KB name must contain only letters, digits, '-' or '_'.")
        return v

    @field_validator("endpoint_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("endpoint_url must start with http:// or https://")
        return v

    @field_validator("headers")
    @classmethod
    def _headers_json(cls, v: str) -> str:
        try:
            obj = json.loads(v or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"headers must be valid JSON: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError("headers JSON must be an object.")
        return v

    @field_validator("top_k")
    @classmethod
    def _top_k_pos(cls, v: int) -> int:
        if v <= 0 or v > 100:
            raise ValueError("top_k must be between 1 and 100.")
        return v


class SessionConfig(BaseModel):
    session_name: str
    annotator: str
    input_path: str
    delimiter: str = ","
    label_column: str
    info_columns: list[str] = []
    kbs: list[KBConfig] = []
    # Optional folder of plain-text files for showing document context in the annotator.
    # Each file must be named <doc_id>.txt (or just <doc_id>).
    context_folder: str | None = None
    doc_id_col: str | None = None   # column in main input whose value is the filename stem
    span_col: str | None = None     # column in main input with char span e.g. "4-11"
    # Auto-select behaviour
    auto_select_secs: int = 5       # countdown before committing the top candidate
    score_tie_threshold: float = 0.02  # candidates within this Δscore are considered tied
    entity_type_col: str | None = None  # info column whose value is matched against semantic_groups
    # LLM re-ranking (RAG disambiguation over the retrieved candidate set)
    enable_llm_rerank: bool = False
    llm_model: str = "claude-haiku-4-5"
    llm_top_n: int = 10             # how many fused candidates to hand the LLM
    llm_prompt: str | None = None   # custom system prompt; None = built-in default
    # Optional per-session API key. Stored in the session config on disk — prefer
    # the ANTHROPIC_API_KEY server env var unless you need per-session keys.
    # Never returned to the browser (write-only; masked on GET).
    llm_api_key: str | None = None
    # Neo4j graph neighborhood. Connection + schema live in neo4j.yaml (see
    # neo4j.example.yaml); this flag just turns the feature on for the session.
    enable_graph: bool = False

    @field_validator("session_name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("session_name must be slug-safe.")
        return v

    @field_validator("annotator")
    @classmethod
    def _annot(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("annotator name is required.")
        return v

    @model_validator(mode="after")
    def _unique_kbs(self) -> "SessionConfig":
        names = [k.name for k in self.kbs]
        if len(names) != len(set(names)):
            raise ValueError("KB names must be unique.")
        if self.label_column in self.info_columns:
            raise ValueError("label_column cannot also be an info column.")
        return self

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> "SessionConfig":
        return cls.model_validate_json(Path(path).read_text())
