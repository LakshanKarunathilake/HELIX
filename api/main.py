"""
ELTool FastAPI backend.

Run from the ELTool root:
    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import io
import logging
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eltool.config import KBConfig, SessionConfig
from eltool.graph_client import GraphError, Neo4jGraphClient, load_neo4j_settings
from eltool.kb_client import KBClient
from eltool.llm_reranker import LLMRerankError, LLMReranker
from eltool.session import (
    input_copy_path, list_sessions, output_csv_path,
    session_config_path, session_dir,
)
from eltool.store import (
    detect_delimiter, find_resume_index, init_output, load_input, write_row,
)

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
    llm_model: str = "claude-haiku-4-5"
    llm_top_n: int = 10
    llm_prompt: str | None = None
    llm_api_key: str | None = None
    enable_graph: bool = False


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
            llm_model=req.llm_model,
            llm_top_n=req.llm_top_n,
            llm_prompt=req.llm_prompt,
            llm_api_key=req.llm_api_key,
            enable_graph=req.enable_graph,
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
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    kb_names = [k.name for k in cfg.kbs]
    out_df = init_output(output_csv_path(name), df, kb_names, cfg.annotator)
    total = len(df)
    done = sum(1 for i in range(total) if all(str(out_df.at[i, kb]) for kb in kb_names))
    return {"total": total, "done": done, "resume_idx": find_resume_index(out_df, kb_names)}


# ── rows ──────────────────────────────────────────────────────────────────────

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
    }


class AnnotateRequest(BaseModel):
    annotations: dict[str, str]


@app.post("/api/sessions/{name}/rows/{idx}")
def save_row(name: str, idx: int, req: AnnotateRequest):
    cfg = _load_cfg(name)
    df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    out_path = output_csv_path(name)
    out_df = init_output(out_path, df, [k.name for k in cfg.kbs], cfg.annotator)
    write_row(out_path, out_df, idx, cfg.annotator, req.annotations)
    return {"ok": True}


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


def _get_reranker(model: str, top_n: int, prompt: str | None, api_key: str | None) -> LLMReranker:
    key = (model, top_n, prompt or "", api_key or "")
    if key not in _RERANKERS:
        _RERANKERS[key] = LLMReranker(
            model=model, top_n=top_n, system_prompt=prompt, api_key=api_key or None,
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
    cfg = _load_cfg(name)
    kb_cfg = next((k for k in cfg.kbs if k.name == req.kb_name), None)
    if not kb_cfg:
        raise HTTPException(404, f"KB '{req.kb_name}' not found")
    dense_cands, lexical_cands = [], []

    if req.enable_dense:
        try:
            dense_cfg = KBConfig(**{**kb_cfg.model_dump(), "endpoint_url": _derived_url(kb_cfg.endpoint_url, "/dense")})
            dense_cands = KBClient(dense_cfg).query(req.label)
        except Exception as e:
            logger.warning("Dense search failed for KB '%s': %s", req.kb_name, e)

    if req.enable_lexical:
        try:
            lex_cfg = KBConfig(**{**kb_cfg.model_dump(), "endpoint_url": _derived_url(kb_cfg.endpoint_url, "/lexical")})
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
                )
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


# ── export ────────────────────────────────────────────────────────────────────

@app.get("/api/sessions/{name}/export")
def export_csv(name: str):
    out_path = output_csv_path(name)
    if not out_path.exists():
        raise HTTPException(404, "No output file yet")
    return StreamingResponse(
        io.BytesIO(out_path.read_bytes()),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}_annotated.csv"'},
    )


# ── helper ────────────────────────────────────────────────────────────────────

def _load_cfg(name: str) -> SessionConfig:
    path = session_config_path(name)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    return SessionConfig.load(path)
