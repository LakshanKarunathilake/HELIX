"""
ELTool FastAPI backend.

Run from the ELTool root:
    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eltool.config import KBConfig, SessionConfig
from eltool import feedback as fb
from eltool.provenance import classify_method
from eltool.graph_client import GraphError, Neo4jGraphClient, load_neo4j_settings
from eltool.kb_client import KBClient
from eltool.llm_reranker import LLMRerankError, LLMReranker
from eltool.metrics import metrics
from eltool.session import (
    input_copy_path, list_sessions, output_csv_path, output_snapshot_path,
    reflection_path, session_config_path, session_dir,
)
from eltool.store import (
    ANNOTATOR_COL, CONFIDENCE_COL, HINT_COL, LLM_COL, detect_delimiter,
    find_resume_index, init_output, load_input, save_output, write_feedback, write_row,
)

# Output CSVs are written by full-file read-modify-write, so concurrent saves
# (e.g. Auto-match firing many at once) must be serialised or they clobber each
# other's rows. One process-wide lock guards every write.
_WRITE_LOCK = threading.Lock()

app = FastAPI(title="ELTool API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_TMP = Path("/tmp/eltool_uploads")
_TMP.mkdir(exist_ok=True)


# ── sessions ────────────────────────────────────────────────────────────────

@app.get("/api/sessions")
def get_sessions():
    return {"sessions": list_sessions()}


@app.post("/api/sessions/parse-file")
async def parse_file(file: UploadFile = File(...)):
    """Upload a CSV/TSV, return columns + preview. File stored in /tmp."""
    content = await file.read()
    tmp_path = _TMP / f"{uuid.uuid4().hex}_{file.filename}"
    tmp_path.write_bytes(content)
    delimiter = detect_delimiter(tmp_path)
    df, delimiter = load_input(tmp_path, delimiter)
    return {
        "filename": file.filename,
        "delimiter": delimiter,
        "columns": list(df.columns),
        "preview": df.head(5).fillna("").to_dict(orient="records"),
        "total_rows": len(df),
        "tmp_path": str(tmp_path),
    }



class CreateSessionRequest(BaseModel):
    session_name: str
    annotator: str
    tmp_path: str
    original_filename: str
    delimiter: str
    label_column: str
    info_columns: list[str] = []
    kbs: list[dict[str, Any]] = []
    context_folder: str | None = None
    doc_id_col: str | None = None
    span_col: str | None = None
    auto_select_secs: int = 5
    score_tie_threshold: float = 0.02
    entity_type_col: str | None = None
    enable_llm_rerank: bool = False
    llm_provider: str = "anthropic"
    llm_base_url: str | None = None
    llm_model: str = "claude-haiku-4-5"
    llm_top_n: int = 10
    llm_prompt: str | None = None
    llm_api_key: str | None = None
    enable_graph: bool = False
    graph_pin_threshold: float = 0.90
    graph_switch_margin: float = 0.10
    enable_auto_select: bool = True
    study_condition: str = ""
    collect_feedback: bool = False
    debug_show_gold: bool = False


@app.post("/api/sessions")
def create_session(req: CreateSessionRequest):
    try:
        kbs = [KBConfig(**k) for k in req.kbs]
    except (ValidationError, Exception) as e:
        raise HTTPException(400, str(e))

    sess_path = session_dir(req.session_name)
    sess_path.mkdir(parents=True, exist_ok=True)

    src = Path(req.tmp_path)
    if not src.exists():
        raise HTTPException(400, "Uploaded file not found — please re-upload.")
    dest = input_copy_path(req.session_name, req.original_filename)
    src.rename(dest)

    if req.context_folder and not Path(req.context_folder).is_dir():
        raise HTTPException(400, f"Context folder not found: {req.context_folder}")

    try:
        cfg = SessionConfig(
            session_name=req.session_name,
            annotator=req.annotator,
            input_path=str(dest),
            delimiter=req.delimiter,
            label_column=req.label_column,
            info_columns=req.info_columns,
            kbs=kbs,
            context_folder=req.context_folder,
            doc_id_col=req.doc_id_col,
            span_col=req.span_col,
            auto_select_secs=req.auto_select_secs,
            score_tie_threshold=req.score_tie_threshold,
            entity_type_col=req.entity_type_col,
            enable_llm_rerank=req.enable_llm_rerank,
            llm_provider=req.llm_provider,
            llm_base_url=req.llm_base_url,
            llm_model=req.llm_model,
            llm_top_n=req.llm_top_n,
            llm_prompt=req.llm_prompt,
            llm_api_key=req.llm_api_key,
            enable_graph=req.enable_graph,
            graph_pin_threshold=req.graph_pin_threshold,
            graph_switch_margin=req.graph_switch_margin,
            enable_auto_select=req.enable_auto_select,
            study_condition=req.study_condition,
            collect_feedback=req.collect_feedback,
            debug_show_gold=req.debug_show_gold,
        )
    except ValidationError as e:
        raise HTTPException(400, str(e))

    cfg.save(session_config_path(req.session_name))
    df, _ = load_input(dest, req.delimiter)
    init_output(output_csv_path(req.session_name), df, [k.name for k in kbs], req.annotator)
    return {"session_name": req.session_name}


@app.get("/api/sessions/{name}")
def get_session(name: str):
    cfg = _load_cfg(name)
    d = cfg.model_dump()
    # Never expose the stored API key to the browser; report only whether one is set.
    d["llm_api_key_set"] = bool(d.get("llm_api_key"))
    d["llm_api_key"] = ""
    return d


@app.put("/api/sessions/{name}")
def update_session(name: str, body: dict = Body(...)):
    existing = _load_cfg(name)
    body = dict(body)
    body.pop("llm_api_key_set", None)  # UI-only flag, not a config field
    # A blank key from the UI means "keep whatever is stored" (it was masked on GET).
    if not (body.get("llm_api_key") or "").strip():
        body["llm_api_key"] = existing.llm_api_key
    try:
        cfg = SessionConfig(**body)
        cfg.save(session_config_path(name))
        df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
        init_output(output_csv_path(name), df, [k.name for k in cfg.kbs], cfg.annotator)
    except (ValidationError, Exception) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/llm/default-prompt")
def llm_default_prompt():
    from eltool.llm_reranker import DEFAULT_SYSTEM_PROMPT
    return {"prompt": DEFAULT_SYSTEM_PROMPT}


@app.delete("/api/sessions/{name}")
def delete_session(name: str):
    path = session_dir(name)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    shutil.rmtree(path)
    return {"ok": True}


# ── progress ─────────────────────────────────────────────────────────────────

@app.get("/api/sessions/{name}/progress")
def get_progress(name: str):
    cfg = _load_cfg(name)
    kb_names = [k.name for k in cfg.kbs]
    out_path = output_csv_path(name)
    # Read-only + vectorised: never rewrite the output here (it fights with
    # concurrent annotation writes and is O(n) per poll). Only initialise on
    # first access when no output exists yet.
    if out_path.exists():
        out_df = pd.read_csv(out_path, dtype=str, keep_default_na=False)
    else:
        df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
        out_df = init_output(out_path, df, kb_names, cfg.annotator)
    total = len(out_df)
    if kb_names and total:
        filled = pd.Series(True, index=out_df.index)
        for kb in kb_names:
            filled &= out_df[kb].astype(str).str.strip() != ""
        done = int(filled.sum())
        resume = total if bool(filled.all()) else int((~filled).to_numpy().argmax())
    else:
        done, resume = 0, 0
    return {"total": total, "done": done, "resume_idx": resume}


# ── rows ──────────────────────────────────────────────────────────────────────

def build_gold_report(gold: str | None, lookup) -> list[dict]:
    """Per gold component: is it in the index, and what is it?

    This is the distinction that matters when diagnosing a miss — a gold that is
    absent from the index is a coverage failure no reranking can fix, whereas an
    indexed gold that was not ranked first is a ranking failure that can be.
    `found` is None (not False) when the lookup itself failed, so a broken KB is
    never reported as a missing concept.
    """
    out: list[dict] = []
    for cid in [c.strip() for c in str(gold or "").split("|") if c.strip()]:
        try:
            ent = lookup(cid)
        except Exception as exc:  # noqa: BLE001
            out.append({"id": cid, "found": None, "error": str(exc)})
            continue
        if not ent:
            out.append({"id": cid, "found": False})
            continue
        aliases = ent.get("all_aliases") or ent.get("aliases") or []
        out.append({
            "id": cid,
            "found": True,
            "label": ent.get("primary_alias") or (aliases[0] if aliases else cid),
            "semantic_groups": ent.get("semantic_groups") or "",
            "semantic_types": ent.get("semantic_types") or "",
            "definition": ent.get("definition") or "",
            "n_aliases": len(aliases),
        })
    return out


_GOLD_FILE = Path(__file__).resolve().parent.parent / "eval" / "out" / "study_gold.csv"


def _lookup_gold(uid: str) -> str | None:
    """Gold for one mention, read from the separate gold file (debug mode only)."""
    if not uid or not _GOLD_FILE.exists():
        return None
    try:
        g = pd.read_csv(_GOLD_FILE, dtype=str, keep_default_na=False)
        hit = g.loc[g["mention_uid"] == uid, "gold_cui"]
        return str(hit.iloc[0]) if len(hit) else None
    except Exception as exc:
        logger.warning("gold lookup failed for %s: %s", uid, exc)
        return None


@app.get("/api/sessions/{name}/rows/{idx}")
def get_row(name: str, idx: int):
    cfg = _load_cfg(name)
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    if idx < 0 or idx >= len(df):
        raise HTTPException(404, "Row index out of range")
    out_df = init_output(output_csv_path(name), df, [k.name for k in cfg.kbs], cfg.annotator)
    row = df.iloc[idx].fillna("").to_dict()

    context_text: str | None = None
    entity_span: str | None = None
    if cfg.context_folder and cfg.doc_id_col:
        try:
            doc_id = str(row.get(cfg.doc_id_col, "")).strip()
            if doc_id:
                folder = Path(cfg.context_folder)
                # Try <doc_id>.txt, then <doc_id> exactly, then any extension
                for candidate in [folder / f"{doc_id}.txt", folder / doc_id,
                                   *list(folder.glob(f"{doc_id}.*"))]:
                    if Path(candidate).is_file():
                        context_text = Path(candidate).read_text(encoding="utf-8", errors="replace")
                        break
        except Exception as exc:
            logger.warning("Context lookup failed for row %d: %s", idx, exc)
    if cfg.span_col:
        entity_span = str(row.get(cfg.span_col, "") or "")

    # DEBUG ONLY — the gold answer, looked up from the gold file that is
    # deliberately kept out of session data. Never included unless the session
    # explicitly asks; rows saved while it is on are stamped as contaminated.
    debug_gold: str | None = None
    debug_gold_info: list[dict] = []
    if cfg.debug_show_gold:
        debug_gold = _lookup_gold(str(row.get("mention_uid", "")).strip())
        if debug_gold and cfg.kbs:
            base = cfg.kbs[0].endpoint_url

            def _kb_entity(cid: str):
                r = requests.get(_entity_url(base, cid), timeout=10)
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                return r.json()

            debug_gold_info = build_gold_report(debug_gold, _kb_entity)

    # Document-level progress: how many mentions in THIS document are annotated.
    doc_progress: dict[str, Any] | None = None
    if cfg.doc_id_col:
        doc_id = str(row.get(cfg.doc_id_col, "")).strip()
        if doc_id:
            kb_names = [k.name for k in cfg.kbs]
            same_doc = df.index[df[cfg.doc_id_col].astype(str).str.strip() == doc_id].tolist()
            done = sum(
                1 for i in same_doc
                if kb_names and all(str(out_df.at[i, kb] or "").strip() for kb in kb_names)
            )
            # 1-based position of this mention within its document
            position = same_doc.index(idx) + 1 if idx in same_doc else None
            doc_progress = {
                "doc_id": doc_id,
                "total": len(same_doc),   # pre-known number of entities in the document
                "done": done,
                "position": position,
                "row_indices": same_doc,
            }

    # Per-decision feedback already recorded for this row (None = not yet, or
    # the session does not collect feedback). The UI prompts only when None.
    feedback: dict[str, str] | None = None
    if cfg.collect_feedback and HINT_COL in out_df.columns:
        hint = str(out_df.at[idx, HINT_COL] or "")
        if hint:
            feedback = {"hint": hint, "confidence": str(out_df.at[idx, CONFIDENCE_COL] or "")}

    return {
        "idx": idx,
        "total": len(df),
        "label": row.get(cfg.label_column, ""),
        "label_column": cfg.label_column,
        "info": {c: row.get(c, "") for c in cfg.info_columns},
        "annotations": {kb.name: str(out_df.at[idx, kb.name] or "") for kb in cfg.kbs},
        "context_text": context_text,
        "entity_span": entity_span,
        "doc_progress": doc_progress,
        "debug_gold": debug_gold,   # None unless the session enables debug_show_gold
        "debug_gold_info": debug_gold_info,
        "feedback": feedback,
    }


class BatchUpdate(BaseModel):
    idx: int
    annotations: dict[str, str]
    llm_used: str | None = None


class BatchAnnotateRequest(BaseModel):
    updates: list[BatchUpdate]


# NOTE: this literal route MUST be declared before "/rows/{idx}" — otherwise
# FastAPI matches "/rows/batch" against the parameterised route (idx="batch")
# and 422s on the int-parse.
@app.post("/api/sessions/{name}/rows/batch")
def save_rows_batch(name: str, req: BatchAnnotateRequest):
    """Apply many row updates in ONE atomic write (used by Auto-match)."""
    cfg = _load_cfg(name)
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    out_path = output_csv_path(name)
    n = len(df)
    with _WRITE_LOCK, metrics.timer("write.batch", name, rows=len(req.updates)):
        out_df = init_output(out_path, df, [k.name for k in cfg.kbs], cfg.annotator)
        for u in req.updates:
            if u.idx < 0 or u.idx >= n:
                continue
            out_df.at[u.idx, ANNOTATOR_COL] = cfg.annotator
            if u.llm_used is not None:
                out_df.at[u.idx, LLM_COL] = u.llm_used
            for kb, val in u.annotations.items():
                out_df.at[u.idx, kb] = val
        save_output(out_path, out_df)   # single write
    return {"ok": True, "written": len(req.updates)}


class AnnotateRequest(BaseModel):
    annotations: dict[str, str]
    llm_used: str | None = None      # e.g. "openai/gpt-4o" when the LLM pick was accepted
    # Pilot-study signals. The UI reports what it observed; the method label is
    # derived server-side (eltool.provenance) and the condition comes from the
    # session config, so neither can be mislabelled by the client.
    elapsed_ms: int | None = None       # time on the row, ms
    selected_rank: int | None = None    # 1-based rank of the committed candidate
    top1_id: str | None = None          # what retrieval ranked first
    user_interacted: bool = False       # annotator touched the UI before commit
    manual_entry: bool = False          # id typed by hand rather than picked
    panels: list[str] | None = None     # UI panels opened on the row (feedback sessions)


def _provenance_for(cfg, req: AnnotateRequest) -> dict[str, str] | None:
    """Build the provenance row for a study session (None for ordinary ones)."""
    if not cfg.study_condition:
        return None
    selected = "|".join(req.annotations[k] for k in sorted(req.annotations))
    return {
        "_method": classify_method(
            selected=selected,
            top1_id=req.top1_id or "",
            user_interacted=req.user_interacted,
            manual_entry=req.manual_entry,
        ),
        "_elapsed_ms": "" if req.elapsed_ms is None else str(req.elapsed_ms),
        "_selected_rank": "" if req.selected_rank is None else str(req.selected_rank),
        "_top1_id": req.top1_id or "",
        "_condition": cfg.study_condition,
        "_debug_gold_shown": "1" if cfg.debug_show_gold else "0",
    }


@app.post("/api/sessions/{name}/rows/{idx}")
def save_row(name: str, idx: int, req: AnnotateRequest):
    cfg = _load_cfg(name)
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    out_path = output_csv_path(name)
    provenance = _provenance_for(cfg, req)
    panels = fb.format_panels(req.panels) if cfg.collect_feedback else None
    with _WRITE_LOCK:   # atomic read-modify-write
        out_df = init_output(out_path, df, [k.name for k in cfg.kbs], cfg.annotator,
                             provenance=bool(cfg.study_condition),
                             feedback=cfg.collect_feedback)
        write_row(out_path, out_df, idx, cfg.annotator, req.annotations,
                  llm_used=req.llm_used, provenance=provenance, panels=panels)
    return {"ok": True}


# ── per-decision feedback (Annotation Guide Sec. 8) ─────────────────────────────

@app.get("/api/feedback/options")
def feedback_options():
    return fb.options()


class FeedbackRequest(BaseModel):
    hint: str
    confidence: str


@app.post("/api/sessions/{name}/rows/{idx}/feedback")
def save_feedback(name: str, idx: int, req: FeedbackRequest):
    """Hint + confidence for a row that has already been saved. Recorded after
    the save so it never counts toward the measured decision time."""
    cfg = _load_cfg(name)
    if not cfg.collect_feedback:
        raise HTTPException(400, "This session does not collect feedback.")
    if req.hint not in fb.HINT_CODES:
        raise HTTPException(422, f"Unknown hint: {req.hint}")
    if req.confidence not in fb.CONFIDENCE_CODES:
        raise HTTPException(422, f"Unknown confidence: {req.confidence}")
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    if idx < 0 or idx >= len(df):
        raise HTTPException(404, "Row index out of range")
    out_path = output_csv_path(name)
    kb_names = [k.name for k in cfg.kbs]
    with _WRITE_LOCK:
        out_df = init_output(out_path, df, kb_names, cfg.annotator,
                             provenance=bool(cfg.study_condition), feedback=True)
        if not all(str(out_df.at[idx, kb] or "").strip() for kb in kb_names):
            raise HTTPException(409, "Save the annotation before recording feedback.")
        write_feedback(out_path, out_df, idx, req.hint, req.confidence)
    return {"ok": True}


class ReflectionRequest(BaseModel):
    ranking: list[str]      # every hint code, most useful first
    comment: str = ""


@app.get("/api/sessions/{name}/reflection")
def get_reflection(name: str):
    _load_cfg(name)
    path = reflection_path(name)
    return json.loads(path.read_text()) if path.exists() else None


@app.put("/api/sessions/{name}/reflection")
def save_reflection(name: str, req: ReflectionRequest):
    """End-of-session reflection: hint types ranked by usefulness, plus a
    short comment on what helped or misled."""
    cfg = _load_cfg(name)
    if not cfg.collect_feedback:
        raise HTTPException(400, "This session does not collect feedback.")
    try:
        fb.validate_reflection_ranking(req.ranking)
    except ValueError as e:
        raise HTTPException(422, str(e))
    record = {
        "session_name": name,
        "annotator": cfg.annotator,
        "study_condition": cfg.study_condition,
        "ranking": req.ranking,
        "comment": req.comment.strip(),
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    reflection_path(name).write_text(json.dumps(record, indent=2))
    return record


# ── candidates ────────────────────────────────────────────────────────────────

def _serialize_candidates(cands):
    return [
        {"id": c.id, "label": c.label, "score": c.score,
         "matched_alias": c.matched_alias, "aliases": c.aliases,
         "semantic_groups": c.semantic_groups, "semantic_types": c.semantic_types,
         "definition": c.definition}
        for c in cands
    ]


def _derived_url(base: str, suffix: str) -> str:
    """Append /dense or /lexical to the configured endpoint URL."""
    return base.rstrip("/") + suffix


def _entity_url(base: str, entity_id: str) -> str:
    """The KB's by-id lookup route, derived from its configured /query URL.

    The id is escaped (composite dictionary ids contain '|') and stripped of
    path separators so it cannot steer the request off the /entity route.
    """
    root = base.rstrip("/")
    if root.endswith("/query"):
        root = root[: -len("/query")]
    safe = quote(str(entity_id).replace("/", "").replace("..", ""), safe="")
    return f"{root}/entity/{safe}"


def _pick_best(dense: list, lexical: list, entity_type: str, threshold: float):
    """Mirror the frontend pickBest logic: dedup by id, sort by score,
    then within tie-threshold prefer a candidate whose semantic_groups
    contains the entity_type string."""
    all_cands = [*dense, *lexical]
    if not all_cands:
        return None
    by_id: dict = {}
    for c in all_cands:
        prev = by_id.get(c.id)
        if not prev or (c.score or 0) > (prev.score or 0):
            by_id[c.id] = c
    pool = sorted(by_id.values(), key=lambda c: c.score or 0, reverse=True)
    top_score = pool[0].score or 0
    tied = [c for c in pool if top_score - (c.score or 0) <= threshold]
    if entity_type:
        groups = [g.strip() for g in (pool[0].semantic_groups or "").replace(",", "|").split("|")]
        match = next(
            (c for c in tied if entity_type in
             [g.strip() for g in (c.semantic_groups or "").replace(",", "|").split("|")]),
            None,
        )
        if match:
            return match
    return pool[0]


@app.get("/api/sessions/{name}/kb/{kb_name}/entity/{entity_id:path}")
def lookup_entity(name: str, kb_name: str, entity_id: str):
    """Resolve a manually typed id against the KB index.

    Returns {found: false} rather than an HTTP error when the index simply does
    not contain the id — that is a normal, informative answer for the annotator
    (on MedMentions ~40% of gold concepts are not indexed at all), not a fault.
    """
    cfg = _load_cfg(name)
    kb = next((k for k in cfg.kbs if k.name == kb_name), None)
    if kb is None:
        raise HTTPException(404, f"No KB named '{kb_name}' in this session")

    eid = (entity_id or "").strip()
    if not eid:
        raise HTTPException(400, "Empty entity id")

    try:
        resp = requests.get(_entity_url(kb.endpoint_url, eid), timeout=10)
    except requests.RequestException as exc:
        raise HTTPException(502, f"KB unreachable: {exc}")

    if resp.status_code == 404:
        return {"found": False, "id": eid,
                "message": f"'{eid}' is not in the {kb_name} index."}
    if not resp.ok:
        raise HTTPException(502, f"KB error {resp.status_code}: {resp.text[:200]}")

    ent = resp.json()
    aliases = ent.get("all_aliases") or ent.get("aliases") or []
    return {
        "found": True,
        "id": ent.get("entity_id", eid),
        "label": (ent.get("primary_alias") or (aliases[0] if aliases else eid)),
        "aliases": aliases,
        "semantic_groups": ent.get("semantic_groups") or "",
        "semantic_types": ent.get("semantic_types") or "",
        "definition": ent.get("definition") or "",
    }


# ── auto-annotate ─────────────────────────────────────────────────────────────

@app.post("/api/sessions/{name}/auto-annotate")
def auto_annotate_session(name: str, skip_annotated: bool = True):
    """Automatically pick and save the best candidate for every unannotated row."""
    cfg = _load_cfg(name)
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    kb_names = [k.name for k in cfg.kbs]
    out_path = output_csv_path(name)
    out_df = init_output(out_path, df, kb_names, cfg.annotator)

    threshold = float(cfg.score_tie_threshold or 0.02)
    entity_type_col = cfg.entity_type_col

    annotated_count = 0
    skipped_count = 0

    for idx in range(len(df)):
        row = df.iloc[idx].fillna("").to_dict()
        all_done = all(str(out_df.at[idx, kb] or "").strip() for kb in kb_names)
        if skip_annotated and all_done:
            skipped_count += 1
            continue

        label = str(row.get(cfg.label_column, "")).strip()
        if not label:
            continue

        entity_type = str(row.get(entity_type_col, "")).strip() if entity_type_col else ""

        new_annotations: dict[str, str] = {}
        for kb_cfg in cfg.kbs:
            existing = str(out_df.at[idx, kb_cfg.name] or "").strip()
            if skip_annotated and existing:
                new_annotations[kb_cfg.name] = existing
                continue

            dense_cands, lexical_cands = [], []
            if kb_cfg.enable_dense:
                try:
                    d_cfg = KBConfig(**{**kb_cfg.model_dump(),
                                        "endpoint_url": _derived_url(kb_cfg.endpoint_url, "/dense")})
                    dense_cands = KBClient(d_cfg).query(label)
                except Exception as e:
                    logger.warning("Dense search failed KB '%s' row %d: %s", kb_cfg.name, idx, e)
            if kb_cfg.enable_lexical:
                try:
                    l_cfg = KBConfig(**{**kb_cfg.model_dump(),
                                        "endpoint_url": _derived_url(kb_cfg.endpoint_url, "/lexical")})
                    lexical_cands = KBClient(l_cfg).query(label)
                except Exception as e:
                    logger.warning("Lexical search failed KB '%s' row %d: %s", kb_cfg.name, idx, e)

            best = _pick_best(dense_cands, lexical_cands, entity_type, threshold)
            if best:
                new_annotations[kb_cfg.name] = best.id

        if new_annotations:
            write_row(out_path, out_df, idx, cfg.annotator, new_annotations)
            annotated_count += 1

    return {"annotated": annotated_count, "skipped": skipped_count, "total": len(df)}


# Reranker instances are reused across requests (keeps the Anthropic client +
# prompt cache warm). Keyed by (model, top_n, prompt, api_key).
_RERANKERS: dict[tuple, LLMReranker] = {}


def _get_reranker(model: str, top_n: int, prompt: str | None, api_key: str | None,
                  provider: str = "anthropic", base_url: str | None = None) -> LLMReranker:
    key = (provider, base_url or "", model, top_n, prompt or "", api_key or "")
    if key not in _RERANKERS:
        _RERANKERS[key] = LLMReranker(
            model=model, top_n=top_n, system_prompt=prompt, api_key=api_key or None,
            provider=provider, base_url=base_url or None,
        )
    return _RERANKERS[key]


def _fuse_candidates(dense, lexical):
    """Merge dense + lexical, dedup by id (keep highest score), sorted by score desc."""
    by_id: dict[str, Any] = {}
    for c in [*dense, *lexical]:
        prev = by_id.get(c.id)
        if prev is None or (c.score or 0) > (prev.score or 0):
            by_id[c.id] = c
    return sorted(by_id.values(), key=lambda c: c.score or 0, reverse=True)


class CandidateRequest(BaseModel):
    label: str
    kb_name: str
    enable_dense: bool = True
    enable_lexical: bool = True
    context: str | None = None


@app.post("/api/sessions/{name}/candidates")
def get_candidates(name: str, req: CandidateRequest):
  with metrics.timer("candidates", name, kb=req.kb_name, mlen=len(req.label or "")):
    cfg = _load_cfg(name)
    kb_cfg = next((k for k in cfg.kbs if k.name == req.kb_name), None)
    if not kb_cfg:
        raise HTTPException(404, f"KB '{req.kb_name}' not found")
    dense_cands, lexical_cands = [], []

    if req.enable_dense:
        try:
            dense_cfg = KBConfig(**{**kb_cfg.model_dump(), "endpoint_url": _derived_url(kb_cfg.endpoint_url, "/dense")})
            with metrics.timer("retrieval.dense", name, kb=req.kb_name):
                dense_cands = KBClient(dense_cfg).query(req.label)
        except Exception as e:
            logger.warning("Dense search failed for KB '%s': %s", req.kb_name, e)

    if req.enable_lexical:
        try:
            lex_cfg = KBConfig(**{**kb_cfg.model_dump(), "endpoint_url": _derived_url(kb_cfg.endpoint_url, "/lexical")})
            with metrics.timer("retrieval.lexical", name, kb=req.kb_name):
                lexical_cands = KBClient(lex_cfg).query(req.label)
        except Exception as e:
            msg = str(e)
            if "503" in msg or "lexical index not initialised" in msg.lower():
                logger.warning(
                    "Lexical search is disabled on the KB server for KB '%s' — "
                    "set lexical.enabled: true in kb/config.yaml to enable it. "
                    "Returning empty lexical results.",
                    req.kb_name,
                )
            else:
                logger.warning("Lexical search failed for KB '%s': %s", req.kb_name, e)

    # ── LLM re-ranking (RAG disambiguation over the fused candidate set) ──────
    llm: dict[str, Any] | None = None
    if cfg.enable_llm_rerank:
        fused = _fuse_candidates(dense_cands, lexical_cands)
        if fused:
            try:
                reranker = _get_reranker(
                    cfg.llm_model, cfg.llm_top_n, cfg.llm_prompt, cfg.llm_api_key,
                    provider=cfg.llm_provider, base_url=cfg.llm_base_url,
                )
                with metrics.timer("rerank.llm", name, provider=cfg.llm_provider, model=cfg.llm_model):
                    result = reranker.rank(req.label, req.context, fused)
                llm = result.to_dict()
            except LLMRerankError as e:
                # Graceful degradation — annotator still sees retrieval results.
                logger.warning("LLM re-rank failed for KB '%s': %s", req.kb_name, e)
                llm = {"error": str(e)}

    return {
        "dense_candidates":   _serialize_candidates(dense_cands),
        "lexical_candidates": _serialize_candidates(lexical_cands),
        "llm": llm,
    }


# ── graph neighborhood (Neo4j) ────────────────────────────────────────────────

# Graph clients are reused across requests. Connection + schema come from the
# neo4j.yaml config file (or env fallback); cache is keyed on those resolved
# values so editing the file and reloading picks up the change.
_GRAPH_CLIENTS: dict[tuple, Neo4jGraphClient] = {}


def _get_graph_client() -> Neo4jGraphClient:
    s = load_neo4j_settings()
    sc = s["schema"]
    key = (
        s["uri"], s["user"], s["password"], s["database"] or "",
        sc.get("node_label", ""), sc.get("id_prop", ""), sc.get("rel_type", ""),
        sc.get("rel_prop", ""), sc.get("name_prop", ""),
        tuple(sc.get("rel_include") or []), tuple(sc.get("rel_exclude") or []),
    )
    if key not in _GRAPH_CLIENTS:
        _GRAPH_CLIENTS[key] = Neo4jGraphClient()
    return _GRAPH_CLIENTS[key]


class GraphCandidateRequest(BaseModel):
    ids: list[str] = []
    neighbor_limit: int = 25


@app.post("/api/sessions/{name}/graph/candidates")
def graph_candidates(name: str, req: GraphCandidateRequest):
    """Individual level — how one mention's candidates connect to each other + neighbors."""
    cfg = _load_cfg(name)
    if not cfg.enable_graph:
        return {"nodes": [], "edges": [], "enabled": False}
    try:
        result = _get_graph_client().candidate_graph(req.ids, req.neighbor_limit)
        return {**result.to_dict(), "enabled": True}
    except GraphError as e:
        logger.warning("Graph candidate query failed: %s", e)
        return {"nodes": [], "edges": [], "enabled": True, "error": str(e)}


class GraphDocumentRequest(BaseModel):
    cuis: list[str] = []


@app.post("/api/sessions/{name}/graph/document")
def graph_document(name: str, req: GraphDocumentRequest):
    """Document level — subgraph among the accepted CUIs, with isolated-pick flags."""
    cfg = _load_cfg(name)
    if not cfg.enable_graph:
        return {"nodes": [], "edges": [], "enabled": False}
    try:
        result = _get_graph_client().document_graph(req.cuis)
        return {**result.to_dict(), "enabled": True}
    except GraphError as e:
        logger.warning("Graph document query failed: %s", e)
        return {"nodes": [], "edges": [], "enabled": True, "error": str(e)}


class GraphOptimizeRequest(BaseModel):
    idx: int                     # any row of the document to optimise
    kb_name: str


def _retrieve_fused(kb_cfg: KBConfig, label: str):
    """Dense + lexical candidates for a label, fused (mirrors get_candidates)."""
    dense, lexical = [], []
    if kb_cfg.enable_dense:
        try:
            dcfg = KBConfig(**{**kb_cfg.model_dump(), "endpoint_url": _derived_url(kb_cfg.endpoint_url, "/dense")})
            dense = KBClient(dcfg).query(label)
        except Exception as e:
            logger.warning("optimize: dense failed for %r: %s", label, e)
    if kb_cfg.enable_lexical:
        try:
            lcfg = KBConfig(**{**kb_cfg.model_dump(), "endpoint_url": _derived_url(kb_cfg.endpoint_url, "/lexical")})
            lexical = KBClient(lcfg).query(label)
        except Exception as e:
            if "503" not in str(e):
                logger.warning("optimize: lexical failed for %r: %s", label, e)
    return _fuse_candidates(dense, lexical)


@app.post("/api/sessions/{name}/graph/optimize")
def graph_optimize(name: str, req: GraphOptimizeRequest):
    """Auto-match: pick candidates across the document to maximise graph connections."""
    cfg = _load_cfg(name)
    if not cfg.enable_graph:
        return {"selections": {}, "enabled": False}
    if not cfg.doc_id_col:
        raise HTTPException(400, "This session has no doc_id_col configured.")
    kb_cfg = next((k for k in cfg.kbs if k.name == req.kb_name), None)
    if not kb_cfg:
        raise HTTPException(404, f"KB '{req.kb_name}' not found")

    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    if req.idx < 0 or req.idx >= len(df):
        raise HTTPException(404, "Row index out of range")
    doc_id = str(df.iloc[req.idx].get(cfg.doc_id_col, "")).strip()
    same = df.index[df[cfg.doc_id_col].astype(str).str.strip() == doc_id].tolist()

    # retrieve candidate IDs per mention (top-N, capped to the LLM/graph budget)
    top_n = max(5, cfg.llm_top_n)
    mention_candidates: list[tuple[int, list[str]]] = []
    for i in same:
        label = str(df.iloc[i].get(cfg.label_column, "")).strip()
        if not label:
            continue
        fused = _retrieve_fused(kb_cfg, label)
        mention_candidates.append((int(i), [(c.id, c.score) for c in fused[:top_n]]))

    try:
        result = _get_graph_client().optimize_selection(mention_candidates, pin_threshold=cfg.graph_pin_threshold, switch_margin=cfg.graph_switch_margin)
        metrics.record_graph(
            name, doc=doc_id, mentions=len(mention_candidates),
            nodes=result.get("nodes_queried", 0), edges=result.get("edges_found", 0),
            query_ms=result.get("query_ms", 0.0), iterations=result.get("iterations", 0),
            changed=result.get("changed", 0),
            edges_before=result.get("edges_before", 0), edges_after=result.get("edges_after", 0),
        )
        return {**result, "enabled": True, "doc_id": doc_id, "kb_name": req.kb_name}
    except GraphError as e:
        logger.warning("Graph optimise failed: %s", e)
        return {"selections": {}, "enabled": True, "error": str(e)}


@app.post("/api/sessions/{name}/graph/optimize-all")
def graph_optimize_all(name: str, skip_annotated: bool = True):
    """
    Bulk graph match — run the connection-maximising optimiser over EVERY document
    in the session and write all results in one atomic pass. Already-annotated
    mentions are pinned (kept) when skip_annotated is true.
    """
    cfg = _load_cfg(name)
    if not cfg.enable_graph:
        return {"enabled": False, "error": "Graph is not enabled for this session."}
    if not cfg.doc_id_col:
        raise HTTPException(400, "This session has no doc_id_col configured.")
    if not cfg.kbs:
        raise HTTPException(400, "Session has no KB configured.")
    kb_cfg = cfg.kbs[0]                       # graph optimises one KB's identifiers
    kb = kb_cfg.name

    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    out_path = output_csv_path(name)
    top_n = max(5, cfg.llm_top_n)

    updates: list[dict[str, Any]] = []
    docs_done = 0
    edges_before_total = edges_after_total = 0

    with _WRITE_LOCK:
        out_df = init_output(out_path, df, [k.name for k in cfg.kbs], cfg.annotator)
        gc = _get_graph_client()

        for doc_id, group in df.groupby(df[cfg.doc_id_col].astype(str).str.strip()):
            if not str(doc_id).strip():
                continue
            idxs = list(group.index)
            fixed: dict[int, str] = {}
            mention_candidates: list[tuple[int, list[str]]] = []
            for i in idxs:
                existing = str(out_df.at[i, kb] or "").strip()
                # pin single-valued existing annotations (skip NIL / multi)
                if skip_annotated and existing and existing != "NIL" and "|" not in existing:
                    fixed[int(i)] = existing
                    continue
                if skip_annotated and existing:   # NIL or multi → leave as-is, don't optimise
                    continue
                label = str(df.at[i, cfg.label_column] or "").strip()
                if not label:
                    continue
                fused = _retrieve_fused(kb_cfg, label)
                mention_candidates.append((int(i), [(c.id, c.score) for c in fused[:top_n]]))

            if not mention_candidates:
                continue
            try:
                res = gc.optimize_selection(mention_candidates, fixed=fixed, pin_threshold=cfg.graph_pin_threshold, switch_margin=cfg.graph_switch_margin)
            except GraphError as e:
                return {"enabled": True, "error": str(e), "documents": docs_done}
            metrics.record_graph(
                name, doc=str(doc_id), mentions=len(mention_candidates),
                nodes=res.get("nodes_queried", 0), edges=res.get("edges_found", 0),
                query_ms=res.get("query_ms", 0.0), iterations=res.get("iterations", 0),
                changed=res.get("changed", 0),
                edges_before=res.get("edges_before", 0), edges_after=res.get("edges_after", 0),
            )
            for sidx, cui in res["selections"].items():
                updates.append({"idx": int(sidx), "cui": cui})
            edges_before_total += res["edges_before"]
            edges_after_total += res["edges_after"]
            docs_done += 1

        # single atomic write of everything
        for u in updates:
            out_df.at[u["idx"], ANNOTATOR_COL] = cfg.annotator
            out_df.at[u["idx"], LLM_COL] = ""        # graph-driven, not LLM
            out_df.at[u["idx"], kb] = u["cui"]
        save_output(out_path, out_df)

    return {
        "enabled": True,
        "documents": docs_done,
        "mentions_written": len(updates),
        "edges_before": edges_before_total,
        "edges_after": edges_after_total,
    }


@app.get("/api/sessions/{name}/documents")
def list_documents(name: str):
    """Document index for the session (for chunked, resumable bulk operations)."""
    cfg = _load_cfg(name)
    if not cfg.doc_id_col:
        return {"documents": []}
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    docs = []
    for doc_id, group in df.groupby(df[cfg.doc_id_col].astype(str).str.strip(), sort=False):
        if not str(doc_id).strip():
            continue
        docs.append({"doc_id": str(doc_id),
                     "first_idx": int(group.index[0]),
                     "mentions": int(len(group))})
    return {"documents": docs}


@app.get("/api/sessions/{name}/documents/mentions")
def document_mentions(name: str, idx: int = -1, doc_id: str = ""):
    """All mentions of a document with their current selections (for Level-2 review)."""
    cfg = _load_cfg(name)
    if not cfg.doc_id_col:
        raise HTTPException(400, "This session has no doc_id_col configured.")
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    kb_names = [k.name for k in cfg.kbs]
    out_df = init_output(output_csv_path(name), df, kb_names, cfg.annotator)

    if not doc_id:
        if idx < 0 or idx >= len(df):
            raise HTTPException(404, "Row index out of range")
        doc_id = str(df.iloc[idx].get(cfg.doc_id_col, "")).strip()
    same = df.index[df[cfg.doc_id_col].astype(str).str.strip() == doc_id].tolist()

    mentions = []
    for i in same:
        row = df.iloc[i].fillna("").to_dict()
        mentions.append({
            "idx": int(i),
            "label": str(row.get(cfg.label_column, "")),
            "span": str(row.get(cfg.span_col, "") or "") if cfg.span_col else "",
            "selected": {kb: str(out_df.at[i, kb] or "") for kb in kb_names},
        })
    return {"doc_id": doc_id, "kb_names": kb_names, "mentions": mentions}


# ── KB test ───────────────────────────────────────────────────────────────────

class TestKBRequest(BaseModel):
    kb_config: dict[str, Any]
    label: str = "test"


@app.post("/api/kb/test")
def test_kb(req: TestKBRequest):
    try:
        kb_cfg = KBConfig(**req.kb_config)
        cands = KBClient(kb_cfg).query(req.label)
        return {"ok": True, "candidates": _serialize_candidates(cands)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── performance tracking (latency + memory) ───────────────────────────────────

class MetricsToggleRequest(BaseModel):
    enabled: bool


@app.get("/api/metrics/status")
def metrics_status():
    return {"enabled": metrics.enabled}


@app.post("/api/metrics/toggle")
def metrics_toggle(req: MetricsToggleRequest):
    metrics.set_enabled(req.enabled)
    return {"enabled": metrics.enabled}


@app.get("/api/sessions/{name}/metrics/insights")
def metrics_insights(name: str):
    _load_cfg(name)
    return {"enabled": metrics.enabled, **metrics.insights(name)}


@app.post("/api/sessions/{name}/metrics/reset")
def metrics_reset(name: str):
    _load_cfg(name)
    metrics.reset(name)
    return {"ok": True}


@app.get("/api/sessions/{name}/graph/stats")
def graph_stats(name: str):
    """Neo4j query cost / scale for each graph-optimise call (always recorded)."""
    _load_cfg(name)
    return metrics.graph_insights(name)


@app.post("/api/sessions/{name}/graph/stats/reset")
def graph_stats_reset(name: str):
    _load_cfg(name)
    metrics.reset_graph(name)
    return {"ok": True}


@app.get("/api/sessions/{name}/metrics/export")
def metrics_export(name: str):
    _load_cfg(name)
    path = session_dir(name) / "metrics.jsonl"
    if not path.exists():
        raise HTTPException(404, "No metrics recorded yet")
    return StreamingResponse(
        io.BytesIO(path.read_bytes()),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{name}_metrics.jsonl"'},
    )


# ── export & per-step snapshots ───────────────────────────────────────────────

_STAGE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@app.post("/api/sessions/{name}/snapshot")
def snapshot_stage(name: str, stage: str):
    """Copy the current output to a per-step snapshot (e.g. 'retrieval', 'graph')."""
    _load_cfg(name)
    if not _STAGE_RE.match(stage):
        raise HTTPException(400, "Invalid stage name.")
    src = output_csv_path(name)
    if not src.exists():
        raise HTTPException(404, "No output file yet")
    shutil.copy(src, output_snapshot_path(name, stage))
    return {"ok": True, "stage": stage}


@app.get("/api/sessions/{name}/snapshots")
def list_snapshots(name: str):
    """Which per-step snapshots exist for this session."""
    _load_cfg(name)
    d = session_dir(name)
    stages = []
    if d.exists():
        for p in d.glob("annotated.*.csv"):
            stages.append(p.name[len("annotated."):-len(".csv")])
    return {"stages": sorted(stages)}


@app.get("/api/sessions/{name}/export")
def export_csv(name: str, stage: str = ""):
    """Download the final output, or a per-step snapshot via ?stage=retrieval|graph."""
    if stage:
        if not _STAGE_RE.match(stage):
            raise HTTPException(400, "Invalid stage name.")
        path = output_snapshot_path(name, stage)
        fname = f"{name}_{stage}.csv"
    else:
        path = output_csv_path(name)
        fname = f"{name}_annotated.csv"
    if not path.exists():
        raise HTTPException(404, "No output file for that stage yet")
    return StreamingResponse(
        io.BytesIO(path.read_bytes()),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── helper ────────────────────────────────────────────────────────────────────

def _load_cfg(name: str) -> SessionConfig:
    path = session_config_path(name)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    return SessionConfig.load(path)
