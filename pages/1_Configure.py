from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from eltool.config import KBConfig, SessionConfig
from eltool.kb_client import KBClient
from eltool.session import (
    input_copy_path,
    list_sessions,
    output_csv_path,
    session_config_path,
    session_dir,
)
from eltool.store import detect_delimiter, load_input

st.set_page_config(page_title="Configure · ELTool", page_icon="⚙️", layout="wide")
st.title("⚙️ Configure session")

# ---- Session selection ----------------------------------------------------
existing = list_sessions()
mode = st.radio(
    "Session",
    ["New session", "Open existing"] if existing else ["New session"],
    horizontal=True,
    key="cfg_mode",
)

prefill: SessionConfig | None = None
if mode == "Open existing" and existing:
    chosen = st.selectbox("Pick a session", existing, key="cfg_open")
    if chosen:
        try:
            prefill = SessionConfig.load(session_config_path(chosen))
        except (ValidationError, FileNotFoundError, json.JSONDecodeError) as e:
            st.error(f"Could not load session: {e}")

st.divider()

# ---- Annotator + session name --------------------------------------------
col_a, col_b = st.columns(2)
with col_a:
    annotator = st.text_input(
        "Annotator name",
        value=prefill.annotator if prefill else "",
        placeholder="e.g. lakshan",
    )
with col_b:
    session_name = st.text_input(
        "Session name (slug)",
        value=prefill.session_name if prefill else "",
        placeholder="e.g. drugs_2026_05",
        disabled=(prefill is not None),
    )

# ---- Input file -----------------------------------------------------------
st.subheader("Input file")
uploaded = st.file_uploader(
    "Upload CSV or TSV",
    type=["csv", "tsv", "txt"],
    accept_multiple_files=False,
)

input_df: pd.DataFrame | None = None
input_path: Path | None = None
delimiter: str | None = None

if uploaded is not None and session_name:
    sess_path = session_dir(session_name)
    sess_path.mkdir(parents=True, exist_ok=True)
    input_path = input_copy_path(session_name, uploaded.name)
    input_path.write_bytes(uploaded.getvalue())
    delimiter = detect_delimiter(input_path)
    input_df, delimiter = load_input(input_path, delimiter)
elif prefill and Path(prefill.input_path).exists():
    input_path = Path(prefill.input_path)
    delimiter = prefill.delimiter
    input_df, delimiter = load_input(input_path, delimiter)

if input_df is not None:
    st.caption(f"Detected delimiter: `{repr(delimiter)}` · {len(input_df)} rows · {len(input_df.columns)} columns")
    st.dataframe(input_df.head(5), use_container_width=True, hide_index=True)
elif uploaded is None and not prefill:
    st.info("Upload a file to continue.")

# ---- Column mapping -------------------------------------------------------
label_column = ""
info_columns: list[str] = []
if input_df is not None:
    st.subheader("Column mapping")
    cols = list(input_df.columns)
    default_label = prefill.label_column if (prefill and prefill.label_column in cols) else cols[0]
    label_column = st.selectbox(
        "Entity-label column", cols, index=cols.index(default_label) if default_label in cols else 0
    )
    info_default = [c for c in (prefill.info_columns if prefill else []) if c in cols and c != label_column]
    info_columns = st.multiselect(
        "Additional info columns (shown as context during annotation)",
        [c for c in cols if c != label_column],
        default=info_default,
    )

# ---- KB configurations ----------------------------------------------------
st.subheader("Knowledge bases")
st.caption("Each KB will produce a column of the same name in the output CSV.")

if "kbs_draft" not in st.session_state:
    st.session_state.kbs_draft = (
        [k.model_dump() for k in prefill.kbs] if prefill else []
    )
elif prefill and not st.session_state.get("kbs_draft_loaded_for") == (prefill.session_name if prefill else None):
    st.session_state.kbs_draft = [k.model_dump() for k in prefill.kbs]
    st.session_state.kbs_draft_loaded_for = prefill.session_name

add_col, _ = st.columns([1, 5])
with add_col:
    if st.button("➕ Add KB", use_container_width=True):
        st.session_state.kbs_draft.append(
            KBConfig(
                name=f"kb{len(st.session_state.kbs_draft) + 1}",
                endpoint_url="http://localhost:8765/query",
            ).model_dump()
        )

remove_idx: int | None = None
test_idx: int | None = None

for i, kb in enumerate(st.session_state.kbs_draft):
    with st.expander(f"KB #{i + 1} — {kb.get('name', '')}", expanded=True):
        c1, c2, c3 = st.columns([2, 3, 1])
        kb["name"] = c1.text_input("Name (column)", value=kb.get("name", ""), key=f"kb_name_{i}")
        kb["endpoint_url"] = c2.text_input("Endpoint URL", value=kb.get("endpoint_url", ""), key=f"kb_url_{i}")
        kb["method"] = c3.selectbox(
            "Method",
            ["POST", "GET"],
            index=0 if kb.get("method", "POST") == "POST" else 1,
            key=f"kb_method_{i}",
        )

        c4, c5, c6 = st.columns([1, 2, 2])
        kb["top_k"] = c4.number_input("top_k", min_value=1, max_value=100, value=int(kb.get("top_k", 10)), key=f"kb_k_{i}")
        kb["response_path"] = c5.text_input(
            "response_path (dotted)",
            value=kb.get("response_path", ""),
            key=f"kb_rp_{i}",
            help="Path to the candidate array in the JSON response. Leave empty if the response IS the array.",
        )
        kb["headers"] = c6.text_input(
            "headers (JSON)",
            value=kb.get("headers", "{}"),
            key=f"kb_h_{i}",
        )

        kb["request_template"] = st.text_area(
            "request_template — JSON, with {label} and {top_k} placeholders",
            value=kb.get("request_template", '{"label": "{label}", "top_k": {top_k}}'),
            key=f"kb_tpl_{i}",
            height=80,
        )

        c7, c8, c9 = st.columns(3)
        kb["id_field"] = c7.text_input("id_field", value=kb.get("id_field", "id"), key=f"kb_id_{i}")
        kb["label_field"] = c8.text_input("label_field", value=kb.get("label_field", "label"), key=f"kb_lab_{i}")
        kb["score_field"] = c9.text_input("score_field", value=kb.get("score_field", "score"), key=f"kb_sc_{i}")

        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("🧪 Test KB", key=f"kb_test_{i}"):
            test_idx = i
        if b2.button("🗑️ Remove", key=f"kb_del_{i}"):
            remove_idx = i

if remove_idx is not None:
    st.session_state.kbs_draft.pop(remove_idx)
    st.rerun()

if test_idx is not None:
    sample_label = (
        str(input_df.iloc[0][label_column])
        if input_df is not None and label_column
        else "test"
    )
    try:
        cfg = KBConfig(**st.session_state.kbs_draft[test_idx])
        client = KBClient(cfg)
        cands = client.query(sample_label)
        st.success(f"Got {len(cands)} candidates for label '{sample_label}'.")
        st.dataframe(
            pd.DataFrame([c.__dict__ for c in cands]),
            use_container_width=True,
            hide_index=True,
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Test failed: {e}")

# ---- Save -----------------------------------------------------------------
st.divider()
save_disabled = not (annotator and session_name and input_df is not None and label_column and st.session_state.kbs_draft)
if st.button("💾 Save session", type="primary", disabled=save_disabled):
    try:
        kbs = [KBConfig(**k) for k in st.session_state.kbs_draft]
        cfg = SessionConfig(
            session_name=session_name,
            annotator=annotator,
            input_path=str(input_path),
            delimiter=delimiter or ",",
            label_column=label_column,
            info_columns=info_columns,
            kbs=kbs,
        )
        cfg.save(session_config_path(session_name))
        st.success(
            f"Session saved to `{session_config_path(session_name)}`. "
            f"Output will be written to `{output_csv_path(session_name)}`."
        )
        st.session_state["active_session"] = session_name
        st.page_link("pages/2_Annotate.py", label="➡️ Go to Annotate", icon="✍️")
    except ValidationError as e:
        st.error(f"Invalid configuration:\n{e}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Save failed: {e}")
