"""
RAG module — hybrid search + OpenAI answer generation over the paper pipeline index.

  search(query, top_k)  → list[dict]   hybrid FAISS semantic + SQLite FTS5 keyword
  ask(question, top_k)  → dict          search → LLM → {answer, sources, chunks}

The index is built by indexer.py and lives in data/index/.
Requires OPENAI_API_KEY env var.
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional

import faiss
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_DIR    = Path(__file__).resolve().parent
INDEX_DIR   = BASE_DIR / "data" / "index"
EMBED_MODEL = "text-embedding-3-small"
GEN_MODEL   = "gpt-5.4-nano"

_client = OpenAI()


# ── Embedding ─────────────────────────────────────────────────────────────────

def _embed(text: str) -> np.ndarray:
    resp = _client.embeddings.create(model=EMBED_MODEL, input=[text])
    vec  = np.array(resp.data[0].embedding, dtype="float32")
    norm = np.linalg.norm(vec)
    return (vec / max(norm, 1e-9)).reshape(1, -1)


# ── Hybrid search ─────────────────────────────────────────────────────────────

def search(query: str, top_k: int = 8, semantic_weight: float = 0.6,
           index_dir: Optional[Path] = None) -> list[dict]:
    """
    Hybrid FAISS semantic + SQLite FTS5 keyword search.
    Returns top_k chunks ranked by combined score, or [] if index not built.
    """
    idx_dir          = index_dir or INDEX_DIR
    db_path          = idx_dir / "chunks.db"
    faiss_index_path = idx_dir / "faiss.index"
    faiss_ids_path   = idx_dir / "faiss_ids.json"

    if not db_path.exists() or not faiss_index_path.exists():
        return []

    index     = faiss.read_index(str(faiss_index_path))
    faiss_ids = json.loads(faiss_ids_path.read_text())
    conn      = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # ── Semantic scores ───────────────────────────────────────────────────────
    q_vec    = _embed(query)
    n_search = min(top_k * 5, index.ntotal)
    distances, indices = index.search(q_vec, n_search)
    sem_scores: dict[str, float] = {
        faiss_ids[idx]: float(dist)
        for dist, idx in zip(distances[0], indices[0]) if idx >= 0
    }
    if sem_scores:
        mn, mx = min(sem_scores.values()), max(sem_scores.values())
        rng = mx - mn or 1.0
        sem_scores = {k: (v - mn) / rng for k, v in sem_scores.items()}

    # ── FTS5 scores ───────────────────────────────────────────────────────────
    fts_scores: dict[str, float] = {}
    try:
        fts_rows = conn.execute(
            "SELECT chunk_id, rank FROM chunks_fts WHERE chunk_text MATCH ? "
            "ORDER BY rank LIMIT ?",
            (query, top_k * 5),
        ).fetchall()
        if fts_rows:
            raw = {r["chunk_id"]: -r["rank"] for r in fts_rows}
            mn, mx = min(raw.values()), max(raw.values())
            rng = mx - mn or 1.0
            fts_scores = {k: (v - mn) / rng for k, v in raw.items()}
    except Exception:
        pass

    # ── Combine ───────────────────────────────────────────────────────────────
    all_ids  = set(sem_scores) | set(fts_scores)
    combined = {
        cid: semantic_weight * sem_scores.get(cid, 0.0)
             + (1 - semantic_weight) * fts_scores.get(cid, 0.0)
        for cid in all_ids
    }
    top_ids = sorted(combined, key=combined.get, reverse=True)[:top_k]

    placeholders = ",".join("?" * len(top_ids))
    rows = conn.execute(
        f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})", top_ids
    ).fetchall()
    conn.close()

    by_id   = {r["chunk_id"]: r for r in rows}
    results = []
    for cid in top_ids:
        if cid not in by_id:
            continue
        r = by_id[cid]
        results.append({
            "chunk_id":           cid,
            "combined_score":     round(combined[cid], 4),
            "semantic_score":     round(sem_scores.get(cid, 0.0), 4),
            "fts_score":          round(fts_scores.get(cid, 0.0), 4),
            "doc_type":           r["doc_type"],
            "openalex_id":        r["openalex_id"],
            "title":              r["title"],
            "year":               r["year"],
            "doi":                r["doi"],
            "venue":              r["venue"],
            "authors":            json.loads(r["authors"] or "[]"),
            "cited_by_count":     r["cited_by_count"],
            "social_trend_names": json.loads(r["social_trend_names"] or "[]"),
            "related_terms":      json.loads(r["related_terms"] or "[]"),
            "final_score":        r["final_score"],
            "chunk_index":        r["chunk_index"],
            "chunk_text":         (r["chunk_text"] or "")[:600],
        })
    return results


# ── Answer generation ─────────────────────────────────────────────────────────

_SYSTEM = """\
You are a health science expert for EverMe, a health and wellness platform.
Answer questions about health trends using the provided research context.
Be accurate and nuanced: cite specific papers by title when evidence supports a
claim, and clearly flag when evidence is preliminary, mixed, or absent.
Write for a health-conscious, intelligent audience — not academic, not hype.\
"""


def ask(question: str, top_k: int = 8) -> dict:
    """Full RAG pipeline: search → build context → LLM answer. Returns {answer, sources, chunks}."""
    chunks = search(question, top_k=top_k)
    if not chunks:
        return {
            "answer":  "No relevant content found in the index. Make sure the indexer has run.",
            "sources": [],
            "chunks":  [],
        }

    ctx_parts = []
    for i, c in enumerate(chunks):
        if c.get("doc_type") == "term_summary":
            trends = ", ".join(c.get("social_trend_names", []))
            hdr = f"[{i+1}] Trend document — {c.get('title', '')} | trends: {trends}"
        else:
            score_str = f"{c['final_score']}/100" if c.get("final_score") else "unscored"
            hdr = (f"[{i+1}] Paper — {c.get('title', '')} "
                   f"({c.get('year', 'n/a')}) | quality: {score_str}")
        ctx_parts.append(f"{hdr}\n{c.get('chunk_text', '')}")

    prompt = (
        f"Question: {question}\n\n"
        f"Context ({len(chunks)} sources):\n"
        + "\n\n---\n".join(ctx_parts)
    )

    resp = _client.chat.completions.create(
        model=GEN_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        max_completion_tokens=1024,
        temperature=0.3,
    )

    return {
        "answer": resp.choices[0].message.content.strip(),
        "sources": [
            {
                "title":          c.get("title"),
                "year":           c.get("year"),
                "doc_type":       c.get("doc_type"),
                "final_score":    c.get("final_score"),
                "combined_score": c.get("combined_score"),
                "trends":         c.get("social_trend_names", []),
                "doi":            c.get("doi"),
            }
            for c in chunks
        ],
        "chunks": chunks,
    }
