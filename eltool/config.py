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
    enable_lexical: bool = False

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
    llm_provider: str = "anthropic"     # "anthropic" | "openai" (openai covers OpenAI/Gemini/local)
    llm_base_url: str | None = None     # for openai-compatible endpoints (e.g. Gemini, Ollama)
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
    # Graph disambiguation only breaks ties: mentions whose top-1 retrieval score
    # is >= pin_threshold are never moved; among the rest, only candidates within
    # switch_margin of that mention's own top-1 score are eligible.
    graph_pin_threshold: float = 0.90
    graph_switch_margin: float = 0.10
    # Pilot study (Sec. 4.6). Naming a condition (e.g. "C1"/"C2"/"C3") turns on
    # per-decision provenance logging for this session and stamps every row with
    # the arm it belongs to. Empty = ordinary session, original column layout.
    study_condition: str = ""
    # C1 (manual) shows the ranked candidates with nothing pre-picked, so the
    # annotator searches rather than verifies. auto_select_secs only governs the
    # countdown, which is why this needs to be its own flag.
    enable_auto_select: bool = True
    # Annotation Guide Sec. 8. After every saved decision the annotator records
    # the main hint and a confidence level; the UI panels opened on the row are
    # logged passively, and a reflection is collected per session. Adds the
    # `_panels`, `_hint` and `_confidence` columns.
    collect_feedback: bool = False
    # DEBUG ONLY. Shows the gold answer in the annotator so retrieval failures can
    # be inspected. Any row saved while this is on is stamped `_debug_gold_shown`
    # and excluded from the study's accuracy numbers — turning the flag back off
    # does NOT clean those rows. Never leave on for a real annotation pass.
    debug_show_gold: bool = False

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
