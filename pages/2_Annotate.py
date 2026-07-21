from __future__ import annotations

import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from eltool.config import SessionConfig
from eltool.kb_client import KBClient
from eltool.session import list_sessions, output_csv_path, session_config_path
from eltool.store import find_resume_index, init_output, load_input, write_row

st.set_page_config(page_title="Annotate · ELTool", page_icon="✍️", layout="wide")
st.title("✍️ Annotate")

# ── session picker ─────────────────────────────────────────────────────────
sessions = list_sessions()
if not sessions:
    st.warning("No sessions found. Start one in **Configure**.")
    st.stop()

default_idx = 0
if "active_session" in st.session_state and st.session_state["active_session"] in sessions:
    default_idx = sessions.index(st.session_state["active_session"])
session_name = st.sidebar.selectbox("Session", sessions, index=default_idx)

cfg      = SessionConfig.load(session_config_path(session_name))
input_df, _ = load_input(Path(cfg.input_path), cfg.delimiter)
out_path = output_csv_path(session_name)
kb_names = [k.name for k in cfg.kbs]
out_df   = init_output(out_path, input_df, kb_names, cfg.annotator)

# ── helpers ────────────────────────────────────────────────────────────────
def sel_key(kb):    return f"sel::{session_name}::{row_idx}::{kb}"
def manual_key(kb): return f"manual::{session_name}::{row_idx}::{kb}"
def cache_key(kb):  return f"cands::{session_name}::{row_idx}::{kb}"

# ── row navigation ─────────────────────────────────────────────────────────
total     = len(input_df)
state_key = f"row_idx::{session_name}"
if state_key not in st.session_state:
    st.session_state[state_key] = min(find_resume_index(out_df, kb_names), max(total - 1, 0))

row_idx: int = max(0, min(st.session_state[state_key], total - 1))

# ── header bar ────────────────────────────────────────────────────────────
hc1, hc2, hc3, hc4 = st.columns([2, 2, 2, 3])
hc1.metric("Row", f"{row_idx + 1} / {total}")
done = sum(1 for i in range(total) if all(str(out_df.at[i, kb]) for kb in kb_names))
hc2.metric("Annotated", f"{done} / {total}")
hc3.markdown(f"**Annotator:** `{cfg.annotator}`")

# Jump widget — use a separate pending key so it doesn't fight with row navigation
jump_key = f"jump_val::{session_name}"
jump = hc4.number_input("Jump to row", min_value=1, max_value=total,
                         value=st.session_state.get(jump_key, row_idx + 1),
                         key=f"jump_widget_{session_name}")
if jump - 1 != row_idx and st.session_state.get(f"jump_triggered_{session_name}") != jump:
    st.session_state[f"jump_triggered_{session_name}"] = jump
    st.session_state[jump_key] = jump
    st.session_state[state_key] = jump - 1
    st.rerun()

st.progress((row_idx + 1) / total if total else 0.0)
st.divider()

# ── current row ───────────────────────────────────────────────────────────
row         = input_df.iloc[row_idx]
label_value = str(row[cfg.label_column])

st.markdown(f"<div class='label-big'>{label_value}</div>", unsafe_allow_html=True)
st.markdown(f"<div class='progress-line'>label column: <code>{cfg.label_column}</code></div>",
            unsafe_allow_html=True)

if cfg.info_columns:
    pills = "".join(
        f"<span class='info-pill'><b>{c}</b>: {row[c]}</span>" for c in cfg.info_columns
    )
    st.markdown(pills, unsafe_allow_html=True)

st.markdown("&nbsp;")

# ── PRE-FETCH candidates for all KBs before rendering ─────────────────────
for kb_cfg in cfg.kbs:
    ck = cache_key(kb_cfg.name)
    if ck not in st.session_state:
        try:
            st.session_state[ck] = KBClient(kb_cfg).query(label_value)
        except Exception as e:  # noqa: BLE001
            st.session_state[ck] = e

# ── AUTO-SELECT: default to top candidate on first visit to this row ───────
AUTO_SECS   = 5
init_flag   = f"init::{session_name}::{row_idx}"
interacted  = f"interacted::{session_name}::{row_idx}"

if init_flag not in st.session_state:
    for kb_cfg in cfg.kbs:
        sk = sel_key(kb_cfg.name)
        existing = str(out_df.at[row_idx, kb_cfg.name] or "")
        if existing:
            st.session_state[sk] = existing          # resume from saved CSV
        else:
            cands = st.session_state.get(cache_key(kb_cfg.name), [])
            if isinstance(cands, list) and cands:
                st.session_state[sk] = cands[0].id  # auto-select top candidate
    st.session_state[init_flag]  = True
    st.session_state[interacted] = False             # fresh row, show timer

# ── countdown timer (only while no manual interaction has happened) ────────
show_timer = not st.session_state.get(interacted, True)

if show_timer:
    timer_ph = st.empty()
    components.html(
        f"""
        <div id="tw" style="background:#F4F4F7;border-radius:8px;padding:10px 14px;
                            font-family:sans-serif;font-size:0.88rem;color:#374151;
                            margin-bottom:4px;">
          ⏱ Auto-selected top candidate — you can change it below &nbsp;
          (<b><span id="cnt">{AUTO_SECS}</span>s</b> to dismiss)
          <div style="background:#E5E7EB;border-radius:4px;height:5px;
                      margin-top:6px;overflow:hidden;">
            <div id="fill" style="background:#4F46E5;height:5px;border-radius:4px;
                                  width:100%;transition:width 0.9s linear;"></div>
          </div>
        </div>
        <script>
          var n = {AUTO_SECS};
          var fill = document.getElementById('fill');
          var cnt  = document.getElementById('cnt');
          var tw   = document.getElementById('tw');
          var iv   = setInterval(function() {{
            n--;
            cnt.textContent = n;
            fill.style.width = (n / {AUTO_SECS} * 100) + '%';
            if (n <= 0) {{
              clearInterval(iv);
              tw.style.opacity = '0';
              tw.style.transition = 'opacity 0.4s';
            }}
          }}, 1000);
        </script>
        """,
        height=70,
    )

# ── KB tabs ────────────────────────────────────────────────────────────────
tabs = st.tabs([f"🔎 {k.name}" for k in cfg.kbs]) if len(cfg.kbs) > 1 else [st.container()]

for tab, kb_cfg in zip(tabs, cfg.kbs):
    with tab:
        sk       = sel_key(kb_cfg.name)
        mk       = manual_key(kb_cfg.name)
        ck       = cache_key(kb_cfg.name)
        cur_sel  = st.session_state.get(sk, "")

        # ── selection status + action buttons ───────────────────────────
        sc, nc, cc = st.columns([5, 1, 1])
        if cur_sel:
            sc.markdown(
                f"<div style='padding:6px 0'>✅ Selected: "
                f"<span class='sel-badge'>{cur_sel}</span></div>",
                unsafe_allow_html=True,
            )
        else:
            sc.markdown(
                "<div class='sel-none' style='padding:6px 0'>"
                "No selection — pick a candidate below</div>",
                unsafe_allow_html=True,
            )

        if nc.button("🚫 NIL", key=f"nil_{session_name}_{row_idx}_{kb_cfg.name}",
                     use_container_width=True):
            st.session_state[sk] = "NIL"
            st.session_state[interacted] = True
            st.rerun()
        if cc.button("✖ Clear", key=f"clr_{session_name}_{row_idx}_{kb_cfg.name}",
                     use_container_width=True):
            st.session_state[sk] = ""
            st.session_state[interacted] = True
            st.rerun()

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        # ── candidate cards ─────────────────────────────────────────────
        cands = st.session_state[ck]
        if isinstance(cands, Exception):
            st.error(f"Query failed: {cands}")
            if st.button("🔁 Retry", key=f"retry_{kb_cfg.name}_{row_idx}"):
                st.session_state.pop(ck, None)
                st.rerun()
        else:
            if not cands:
                st.info("No candidates returned.")

            for j, cand in enumerate(cands):
                is_sel    = cur_sel == cand.id
                score_str = f"{cand.score:.4f}" if cand.score is not None else "—"
                prefix    = "✅  " if is_sel else f"{j + 1}.  "
                btn_label = (
                    f"{prefix}**{cand.label or '(no label)'}**\n"
                    f"`{cand.id}`   ·   score {score_str}"
                )

                # selected → border container for indigo highlight
                container = st.container(border=is_sel)
                with container:
                    if st.button(btn_label, key=f"pick_{kb_cfg.name}_{row_idx}_{j}",
                                 use_container_width=True):
                        st.session_state[sk] = cand.id
                        st.session_state[interacted] = True
                        st.rerun()

                    # disambiguation meta — shown below the button
                    meta_parts = []
                    if cand.matched_alias and cand.matched_alias != cand.label:
                        meta_parts.append(f"↳ matched via **{cand.matched_alias}**")
                    if cand.aliases:
                        other = [a for a in cand.aliases if a != cand.label]
                        if other:
                            with st.expander(
                                f"{'📖' if is_sel else '  '} {len(other)} synonym{'s' if len(other) != 1 else ''}",
                                expanded=False,
                            ):
                                st.markdown(
                                    " · ".join(f"`{a}`" for a in other),
                                    unsafe_allow_html=False,
                                )
                    if meta_parts:
                        st.caption("  ".join(meta_parts))

        # ── manual entry ────────────────────────────────────────────────
        st.divider()
        mc, uc = st.columns([5, 1])
        manual = mc.text_input(
            "Manual KB id", value="", key=mk,
            placeholder="paste or type a KB id…",
            label_visibility="collapsed",
        )
        if uc.button("Use ↵", key=f"use_manual_{kb_cfg.name}_{row_idx}",
                     use_container_width=True):
            if manual.strip():
                st.session_state[sk] = manual.strip()
                st.session_state[interacted] = True
                st.rerun()
        st.caption("↑ Manual KB id entry")

# ── footer nav ────────────────────────────────────────────────────────────
st.divider()

all_filled = all(st.session_state.get(sel_key(kb), "") for kb in kb_names)
n1, n2, n3, _, n5 = st.columns([1, 1, 1, 4, 1])

if n1.button("⬅️ Prev", disabled=row_idx == 0, use_container_width=True):
    st.session_state[state_key] = row_idx - 1
    st.session_state[jump_key]  = row_idx       # sync jump widget
    st.rerun()

if n2.button("💾 Save", use_container_width=True, disabled=not all_filled):
    kb_values = {kb: st.session_state[sel_key(kb)] for kb in kb_names}
    write_row(out_path, out_df, row_idx, cfg.annotator, kb_values)
    st.toast("Saved ✓", icon="💾")

if n3.button("Next ➡️", type="primary", use_container_width=True):
    if not all_filled:
        pending = [kb for kb in kb_names if not st.session_state.get(sel_key(kb), "")]
        st.warning(
            f"⚠️ Please make a selection for: **{', '.join(pending)}** "
            f"— pick a candidate, enter an id manually, or mark as NIL.",
            icon="⚠️",
        )
    elif row_idx >= total - 1:
        st.info("🎉 You've reached the last row. All done!", icon="🎉")
    else:
        kb_values = {kb: st.session_state[sel_key(kb)] for kb in kb_names}
        out_df = write_row(out_path, out_df, row_idx, cfg.annotator, kb_values)
        next_row = row_idx + 1
        st.session_state[state_key] = next_row
        st.session_state[jump_key]  = next_row + 1   # sync jump widget
        st.session_state[f"jump_triggered_{session_name}"] = next_row + 1
        st.rerun()

with n5:
    with open(out_path, "rb") as f:
        st.download_button(
            "⬇️ CSV", data=f.read(),
            file_name=f"{session_name}_annotated.csv",
            mime="text/csv", use_container_width=True,
        )
