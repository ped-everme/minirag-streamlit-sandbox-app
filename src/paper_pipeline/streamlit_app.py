"""
EverMe Trend Radar — Streamlit app.

Two pages:
  Chat   — RAG chatbot over the paper pipeline index
  Audit  — pipeline validation: terms, queries, papers, scores

Run:
  .venv/bin/streamlit run src/paper_pipeline/streamlit_app.py
"""

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).resolve().parent
SIGNAL_DIR     = BASE_DIR.parent / "trend_radar" / "data" / "output"
CATALOG_PATH   = BASE_DIR / "data" / "catalog.json"
META_PATH      = BASE_DIR / "data" / "papers_meta.json"
DOCS_DIR       = BASE_DIR / "data" / "documents"
INDEX_DIR      = BASE_DIR / "data" / "index"

sys.path.insert(0, str(BASE_DIR))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="EverMe Trend Radar",
    page_icon="📡",
    layout="wide",
)

# ── Sidebar navigation ────────────────────────────────────────────────────────
st.sidebar.title("📡 Trend Radar")
page = st.sidebar.radio("", ["💬 Chat", "🔍 Audit"], label_visibility="collapsed")

# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_data
def load_signal() -> dict:
    files = sorted(SIGNAL_DIR.glob("signal_*.json"))
    if not files:
        return {}
    return json.loads(files[-1].read_text(encoding="utf-8")), files[-1].name


@st.cache_data
def load_catalog() -> list[dict]:
    if not CATALOG_PATH.exists():
        return []
    cat = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return cat.get("entries", [])


@st.cache_data
def load_meta() -> dict:
    if not META_PATH.exists():
        return {}
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def _doc_exists(term_id: str) -> str:
    if not DOCS_DIR.exists():
        return "—"
    emerging = list(DOCS_DIR.glob(f"{term_id}_[0-9]*.md"))
    hyped    = list(DOCS_DIR.glob(f"{term_id}_hyped_*.md"))
    parts = []
    if emerging: parts.append("EMERGING")
    if hyped:    parts.append("HYPED")
    return " + ".join(parts) if parts else "—"


# ── AUDIT PAGE ────────────────────────────────────────────────────────────────

def show_audit():
    st.title("🔍 Pipeline Audit")

    result = load_signal()
    if not result:
        st.error("No signal file found in trend_radar/data/output/")
        return

    signal, signal_name = result
    catalog = load_catalog()
    meta    = load_meta()

    terms = signal.get("terms", [])

    # ── Signal header ─────────────────────────────────────────────────────────
    st.caption(f"Signal: **{signal_name}**")

    clf_counts = {}
    for t in terms:
        c = t.get("classification", "unknown")
        clf_counts[c] = clf_counts.get(c, 0) + 1

    cols = st.columns(len(clf_counts) + 2)
    cols[0].metric("Total terms", len(terms))
    for i, (clf, n) in enumerate(sorted(clf_counts.items()), start=1):
        cols[i].metric(clf, n)

    # index status
    index_ok = (INDEX_DIR / "faiss.index").exists()
    if index_ok:
        import faiss as _faiss
        idx = _faiss.read_index(str(INDEX_DIR / "faiss.index"))
        cols[-1].metric("Index vectors", idx.ntotal)
    else:
        cols[-1].metric("Index", "not built")

    st.divider()

    # ── Terms overview table ──────────────────────────────────────────────────
    st.subheader("Terms overview")

    rows = []
    for term in terms:
        tid     = term["term_id"]
        papers  = [p for p in meta.values() if tid in p.get("parent_term_ids", [])]
        dl      = [p for p in papers if p.get("download_status") in ("downloaded", "already_exists")]
        scored  = [p for p in papers if p.get("score_status") == "scored"]
        queries = [e for e in catalog if e.get("parent_term_id") == tid]

        avg_score = (
            round(sum(p.get("final_score", 0) for p in scored) / len(scored), 1)
            if scored else None
        )
        rows.append({
            "Term":           term.get("social_trend_name", tid),
            "Classification": term.get("classification", "—"),
            "Hype score":     round(term.get("hype_score", 0), 3),
            "Emerging score": round(term.get("emerging_score", 0), 3),
            "Queries":        len(queries),
            "Papers found":   len(papers),
            "Downloaded":     len(dl),
            "DL rate":        f"{round(len(dl)/len(papers)*100)}%" if papers else "—",
            "Scored":         len(scored),
            "Avg score":      avg_score,
            "Doc":            _doc_exists(tid),
        })

    df_terms = pd.DataFrame(rows)
    st.dataframe(
        df_terms,
        use_container_width=True,
        column_config={
            "Hype score":     st.column_config.ProgressColumn("Hype score", min_value=0, max_value=1, format="%.3f"),
            "Emerging score": st.column_config.ProgressColumn("Emerging score", min_value=0, max_value=1, format="%.3f"),
            "Avg score":      st.column_config.NumberColumn("Avg score", format="%.1f"),
        },
        hide_index=True,
    )

    st.divider()

    # ── Per-term detail ───────────────────────────────────────────────────────
    st.subheader("Per-term detail")

    for term in terms:
        tid        = term["term_id"]
        name       = term.get("social_trend_name", tid)
        clf        = term.get("classification", "—")
        clf_color  = {"EMERGING": "🟢", "HYPED": "🔴", "HYPED + EMERGING": "🟡",
                      "ESTABLISHED": "🔵"}.get(clf, "⚪")

        with st.expander(f"{clf_color} **{name}** — {clf}"):
            papers  = [p for p in meta.values() if tid in p.get("parent_term_ids", [])]
            queries = [e for e in catalog if e.get("parent_term_id") == tid]

            # Queries
            if queries:
                st.markdown("**Queries**")
                q_rows = [{"Query": e["query"], "Type": e.get("type", "—"),
                           "Papers": e.get("paper_count", 0), "Status": e.get("status", "—")}
                          for e in queries]
                st.dataframe(pd.DataFrame(q_rows), use_container_width=True,
                             hide_index=True, height=min(38 + len(q_rows) * 35, 250))
            else:
                st.caption("No queries in catalog for this term.")

            # Papers
            if papers:
                st.markdown(f"**Papers** ({len(papers)} found)")
                p_rows = []
                for p in sorted(papers, key=lambda x: x.get("final_score") or 0, reverse=True):
                    dl_status = p.get("download_status", "—")
                    sc_status = p.get("score_status", "—")
                    p_rows.append({
                        "Title":       (p.get("title") or "")[:80],
                        "Year":        p.get("year"),
                        "Citations":   p.get("cited_by_count", 0),
                        "Download":    dl_status,
                        "Score":       p.get("final_score"),
                        "HoE":         p.get("hoe_score"),
                        "CL":          p.get("cl_score"),
                        "SD":          p.get("sd_score"),
                        "SS":          p.get("ss_score"),
                    })
                st.dataframe(
                    pd.DataFrame(p_rows),
                    use_container_width=True,
                    hide_index=True,
                    height=min(38 + len(p_rows) * 35, 400),
                    column_config={
                        "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
                    },
                )
            else:
                st.caption("No papers found for this term.")

            # Generated docs
            doc_status = _doc_exists(tid)
            if doc_status != "—":
                st.success(f"Document generated: {doc_status}")
            else:
                st.warning("No document generated yet.")

    st.divider()

    # ── Full papers table ─────────────────────────────────────────────────────
    st.subheader("All papers")

    if meta:
        all_rows = []
        term_name_map = {t["term_id"]: t.get("social_trend_name", t["term_id"]) for t in terms}

        for p in meta.values():
            parent_names = [term_name_map.get(tid, tid) for tid in p.get("parent_term_ids", [])]
            all_rows.append({
                "Title":       (p.get("title") or "")[:80],
                "Year":        p.get("year"),
                "Venue":       (p.get("venue") or "—")[:40],
                "Citations":   p.get("cited_by_count", 0),
                "Terms":       ", ".join(parent_names),
                "Download":    p.get("download_status", "—"),
                "Score":       p.get("final_score"),
                "OA":          "✓" if p.get("is_open_access") else "✗",
            })

        df_papers = pd.DataFrame(all_rows)

        # Filter controls
        col1, col2 = st.columns(2)
        dl_filter  = col1.multiselect(
            "Download status",
            options=df_papers["Download"].unique().tolist(),
            default=df_papers["Download"].unique().tolist(),
        )
        term_filter = col2.multiselect(
            "Term",
            options=sorted({t for row in all_rows for t in row["Terms"].split(", ")}),
        )

        filtered = df_papers[df_papers["Download"].isin(dl_filter)]
        if term_filter:
            filtered = filtered[filtered["Terms"].apply(
                lambda x: any(t in x for t in term_filter)
            )]

        st.dataframe(
            filtered,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
                "Citations": st.column_config.NumberColumn("Citations"),
            },
        )
        st.caption(f"{len(filtered)} of {len(df_papers)} papers shown")
    else:
        st.info("papers_meta.json not found — run paper_search.py first.")


# ── CHAT PAGE ─────────────────────────────────────────────────────────────────

def show_chat():
    st.title("💬 Health Trends RAG")
    st.caption("Ask anything about the trending health topics and their scientific evidence.")

    if not (INDEX_DIR / "faiss.index").exists():
        st.error("Index not built yet — run `indexer.py` first.")
        return

    # Lazy-load RAG to avoid blocking the audit page
    try:
        import rag
    except Exception as e:
        st.error(f"Could not load RAG module: {e}")
        return

    # Chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                with st.expander(f"Sources ({len(msg['sources'])})"):
                    for s in msg["sources"]:
                        icon = "📄" if s.get("doc_type") == "paper_chunk" else "📋"
                        score_str = f" — score {s['final_score']}/100" if s.get("final_score") else ""
                        trends = ", ".join(s.get("trends", []))
                        trends_str = f" | {trends}" if trends else ""
                        st.markdown(f"{icon} **{s.get('title', 'Unknown')}** "
                                    f"({s.get('year', 'n/a')}){score_str}{trends_str}")

    # Input
    if prompt := st.chat_input("Ask about a health trend..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Searching index and generating answer…"):
                result = rag.ask(prompt, top_k=8)

            st.markdown(result["answer"])
            if result.get("sources"):
                with st.expander(f"Sources ({len(result['sources'])})"):
                    for s in result["sources"]:
                        icon = "📄" if s.get("doc_type") == "paper_chunk" else "📋"
                        score_str = f" — score {s['final_score']}/100" if s.get("final_score") else ""
                        trends = ", ".join(s.get("trends", []))
                        trends_str = f" | {trends}" if trends else ""
                        st.markdown(f"{icon} **{s.get('title', 'Unknown')}** "
                                    f"({s.get('year', 'n/a')}){score_str}{trends_str}")

            st.session_state.messages.append({
                "role":    "assistant",
                "content": result["answer"],
                "sources": result.get("sources", []),
            })

    if st.session_state.messages:
        if st.sidebar.button("Clear chat"):
            st.session_state.messages = []
            st.rerun()


# ── Router ─────────────────────────────────────────────────────────────────────

if page == "💬 Chat":
    show_chat()
else:
    show_audit()
