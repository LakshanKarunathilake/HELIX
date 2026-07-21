import streamlit as st

st.set_page_config(
    page_title="ELTool — Entity Linking Annotator",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 4rem; }

    /* ── candidate card buttons ─────────────────────────────────────────── */
    /* All candidate buttons are secondary; scoped to tab/vertical content */
    div[data-testid="stTabContent"] div[data-testid="stButton"] > button,
    div[data-testid="stVerticalBlock"] > div > div[data-testid="stButton"] > button {
        width: 100%;
        text-align: left !important;
        background: #FFFFFF;
        border: 1.5px solid #D1D5DB;
        border-radius: 10px;
        padding: 12px 16px;
        margin-bottom: 4px;
        font-size: 0.92rem;
        line-height: 1.6;
        color: #111827;
        transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
    }
    div[data-testid="stTabContent"] div[data-testid="stButton"] > button:hover,
    div[data-testid="stVerticalBlock"] > div > div[data-testid="stButton"] > button:hover {
        border-color: #6366F1;
        background: #F5F3FF;
        box-shadow: 0 0 0 3px #EEF2FF;
    }

    /* selected candidate — border container gets indigo highlight */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #4F46E5 !important;
        background: #EEF2FF !important;
        border-radius: 10px !important;
        box-shadow: 0 0 0 3px #C7D2FE;
        margin-bottom: 6px;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stButton"] > button {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }

    /* ── countdown timer bar ────────────────────────────────────────────── */
    .timer-wrap { background: #F4F4F7; border-radius: 8px; padding: 10px 14px;
                  margin-bottom: 12px; font-size: 0.88rem; color: #374151; }
    .timer-bar-track { background: #E5E7EB; border-radius: 4px; height: 5px;
                       margin-top: 6px; overflow: hidden; }
    .timer-bar-fill  { background: #4F46E5; height: 5px; border-radius: 4px;
                       transition: width 0.9s linear; }

    /* ── nav buttons (columns context) — always normal ──────────────────── */
    div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button {
        text-align: center !important;
        padding: 0.4rem 0.8rem !important;
        border-radius: 8px;
        font-size: 0.9rem;
    }

    /* ── misc ──────────────────────────────────────────────────────────── */
    .label-big  { font-size: 1.6rem; font-weight: 600; }
    .info-pill  { display: inline-block; background: #F4F4F7; border-radius: 999px;
                  padding: 4px 10px; margin: 2px 4px 2px 0; font-size: 0.85rem; }
    .progress-line { color: #6B7280; font-size: 0.9rem; }
    .sel-badge  { display: inline-block; background: #4F46E5; color: #fff;
                  border-radius: 6px; padding: 3px 10px; font-size: 0.85rem;
                  font-family: ui-monospace, monospace; }
    .sel-none   { color: #9CA3AF; font-style: italic; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🔗 ELTool")
st.caption("Human-in-the-loop entity-linking annotation against FAISS-backed KBs.")

st.markdown(
    """
    ### Workflow
    1. **Configure** — upload a CSV/TSV, choose your label column and additional info columns,
       and define one or more KBs (REST endpoints).
    2. **Annotate** — for each row, pick the matching KB ID from top-K candidates,
       enter one manually, or mark as NIL. Output is auto-saved per row.

    Use the sidebar to navigate between pages.
    """
)

st.info("Open **Configure** in the sidebar to start a new session, or reopen an existing one.")
