"""
Indexer — last phase of the pipeline (runs after doc_generator and hyped_summarizer).

Indexes two sources into a single FAISS + SQLite hybrid index:

  1. Paper chunks   — full text of downloaded PDFs, chunked into 800-token windows
  2. Term summaries — generated Markdown documents from doc_generator (EMERGING)
                      and hyped_summarizer (HYPED)

Both sources are distinguished by a `doc_type` field in SQLite:
  "paper_chunk"   — chunk of a peer-reviewed paper PDF
  "term_summary"  — chunk of a generated trend document

Index stores:
  FAISS         — semantic search via text-embedding-3-small (cosine similarity)
  SQLite FTS5   — keyword/term search (BM25-style ranking)

Hybrid search: FAISS score (semantic) + FTS5 rank (keyword) normalised and
combined with configurable weights (default: 0.6 semantic / 0.4 keyword).

Every run overwrites the index from scratch. Incremental indexing is a
future improvement — embeddings are cheap enough to re-run.

Output files:
  data/index/faiss.index      — FAISS IndexFlatIP (L2-normalised vectors)
  data/index/faiss_ids.json   — ordered list of chunk_ids matching FAISS rows
  data/index/chunks.db        — SQLite: chunks table + chunks_fts virtual table

Chunking: 800 tokens per chunk, 100 token overlap (tiktoken cl100k_base).
Embedding model: text-embedding-3-small (1536 dims) via OpenAI.

Usage:
  python src/paper_pipeline/indexer.py
  python src/paper_pipeline/indexer.py --signal path/to/signal.json
  python src/paper_pipeline/indexer.py --query "cold water recovery" --top-k 5
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import faiss
import numpy as np
import pdfplumber
import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_DIR    = Path(__file__).resolve().parent
META_PATH   = BASE_DIR / "data" / "papers_meta.json"
PAPERS_DIR  = BASE_DIR / "data" / "papers"
DOCS_DIR    = BASE_DIR / "data" / "documents"
INDEX_DIR   = BASE_DIR / "data" / "index"

EMBED_MODEL    = "text-embedding-3-small"
EMBED_DIMS     = 1536
CHUNK_TOKENS   = 800
OVERLAP_TOKENS = 100
EMBED_BATCH    = 100

client = OpenAI()

enc = tiktoken.get_encoding("cl100k_base")


# ── Signal helpers ────────────────────────────────────────────────────────────

def _load_signal_index(signal_path: Optional[Path]) -> dict[str, dict]:
    """Returns {term_id: {social_trend_name, related_terms}} from signal file."""
    if not signal_path or not signal_path.exists():
        return {}
    sig = json.loads(signal_path.read_text(encoding="utf-8"))
    return {
        t["term_id"]: {
            "social_trend_name": t.get("social_trend_name", ""),
            "related_terms":     t.get("related_terms", []),
        }
        for t in sig.get("terms", [])
    }


def _latest_signal() -> Optional[Path]:
    out_dir = BASE_DIR.parent / "trend_radar" / "data" / "output"
    files = sorted(out_dir.glob("signal_*.json"))
    return files[-1] if files else None


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text(pdf_path: Path) -> str:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return " ".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return ""


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_tokens: int = CHUNK_TOKENS,
                overlap_tokens: int = OVERLAP_TOKENS) -> list[str]:
    tokens = enc.encode(text)
    step   = chunk_tokens - overlap_tokens
    chunks = []
    for start in range(0, len(tokens), step):
        chunks.append(enc.decode(tokens[start : start + chunk_tokens]))
        if start + chunk_tokens >= len(tokens):
            break
    return chunks


# ── Embeddings ────────────────────────────────────────────────────────────────

def _embed_batch(texts: list[str]) -> np.ndarray:
    resp  = client.embeddings.create(model=EMBED_MODEL, input=texts)
    vecs  = np.array([e.embedding for e in resp.data], dtype="float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-9)


# ── SQLite helpers ────────────────────────────────────────────────────────────

_CREATE_CHUNKS = """
CREATE TABLE IF NOT EXISTS chunks (
    id                  INTEGER PRIMARY KEY,
    chunk_id            TEXT UNIQUE,
    doc_type            TEXT,
    openalex_id         TEXT,
    title               TEXT,
    year                INTEGER,
    doi                 TEXT,
    venue               TEXT,
    authors             TEXT,
    abstract            TEXT,
    cited_by_count      INTEGER,
    parent_term_ids     TEXT,
    social_trend_names  TEXT,
    related_terms       TEXT,
    final_score         REAL,
    hoe_score           REAL,
    cl_score            REAL,
    sd_score            REAL,
    ss_score            REAL,
    chunk_index         INTEGER,
    chunk_text          TEXT
);
"""

_CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id            UNINDEXED,
    chunk_text,
    content             = chunks,
    content_rowid       = id
);
"""

_POPULATE_FTS = """
INSERT INTO chunks_fts(rowid, chunk_id, chunk_text)
    SELECT id, chunk_id, chunk_text FROM chunks;
"""


def _init_db(db_path: Path) -> sqlite3.Connection:
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_CHUNKS)
    conn.execute(_CREATE_FTS)
    conn.commit()
    return conn


def _insert_chunk(conn: sqlite3.Connection, chunk_id: str, paper: dict,
                  chunk_index: int, chunk_text: str,
                  social_trend_names: list[str], related_terms: list[str],
                  doc_type: str = "paper_chunk") -> None:
    authors = [a.get("name", "") for a in paper.get("authors", [])]
    conn.execute(
        """INSERT OR REPLACE INTO chunks
           (chunk_id, doc_type, openalex_id, title, year, doi, venue,
            authors, abstract, cited_by_count,
            parent_term_ids, social_trend_names, related_terms,
            final_score, hoe_score, cl_score, sd_score, ss_score,
            chunk_index, chunk_text)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            chunk_id,
            doc_type,
            paper.get("openalex_id"),
            paper.get("title"),
            paper.get("year"),
            paper.get("doi"),
            paper.get("venue"),
            json.dumps(authors),
            paper.get("abstract") or "",
            paper.get("cited_by_count"),
            json.dumps(paper.get("parent_term_ids", [])),
            json.dumps(social_trend_names),
            json.dumps(related_terms),
            paper.get("final_score"),
            paper.get("hoe_score"),
            paper.get("cl_score"),
            paper.get("sd_score"),
            paper.get("ss_score"),
            chunk_index,
            chunk_text,
        ),
    )


def _insert_doc_chunk(conn: sqlite3.Connection, chunk_id: str, term_id: str,
                      track: str, social_name: str, related_terms: list[str],
                      chunk_index: int, chunk_text: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO chunks
           (chunk_id, doc_type, title,
            parent_term_ids, social_trend_names, related_terms,
            chunk_index, chunk_text)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            chunk_id,
            "term_summary",
            social_name,
            json.dumps([term_id]),
            json.dumps([social_name]),
            json.dumps(related_terms),
            chunk_index,
            chunk_text,
        ),
    )


# ── Document helpers ─────────────────────────────────────────────────────────

def _parse_doc_filename(name: str) -> tuple[str, str]:
    """Returns (term_id, track) from a document filename."""
    m = re.match(r"^(.+?)_hyped_\d{4}-\d{2}-\d{2}\.md$", name)
    if m:
        return m.group(1), "hyped"
    m = re.match(r"^(.+?)_\d{4}-\d{2}-\d{2}\.md$", name)
    if m:
        return m.group(1), "emerging"
    return name.replace(".md", ""), "unknown"


def _collect_doc_chunks(docs_dir: Path,
                        signal_index: dict) -> list[tuple]:
    """Reads all *.md documents and returns chunk records for indexing."""
    if not docs_dir.exists():
        return []
    records = []
    for md_file in sorted(docs_dir.glob("*.md")):
        term_id, track = _parse_doc_filename(md_file.name)
        text   = md_file.read_text(encoding="utf-8")
        chunks = _chunk_text(text)
        sig         = signal_index.get(term_id, {})
        social_name = sig.get("social_trend_name", term_id)
        related     = sig.get("related_terms", [])
        for i, chunk in enumerate(chunks):
            chunk_id = f"doc_{term_id}_{track}_{i}"
            records.append((chunk_id, term_id, track, social_name, related, i, chunk))
        print(f"  ✓ {len(chunks):3d} chunks  [{track}] {social_name}")
    return records


# ── Hybrid search ─────────────────────────────────────────────────────────────

def search(query: str, top_k: int = 10,
           semantic_weight: float = 0.6,
           db_path: Optional[Path] = None,
           faiss_index_path: Optional[Path] = None,
           faiss_ids_path: Optional[Path] = None) -> list[dict]:
    """
    Hybrid search: FAISS semantic + SQLite FTS5 keyword.
    Returns top_k chunks ranked by combined score.
    """
    db_path          = db_path          or INDEX_DIR / "chunks.db"
    faiss_index_path = faiss_index_path or INDEX_DIR / "faiss.index"
    faiss_ids_path   = faiss_ids_path   or INDEX_DIR / "faiss_ids.json"

    if not db_path.exists() or not faiss_index_path.exists():
        raise FileNotFoundError("Index not found — run indexer.py first")

    index     = faiss.read_index(str(faiss_index_path))
    faiss_ids = json.loads(Path(faiss_ids_path).read_text())

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── Semantic scores ───────────────────────────────────────────────────────
    q_vec    = _embed_batch([query])
    n_search = min(top_k * 5, index.ntotal)
    distances, indices = index.search(q_vec, n_search)

    sem_scores: dict[str, float] = {}
    for dist, idx in zip(distances[0], indices[0]):
        if idx >= 0:
            sem_scores[faiss_ids[idx]] = float(dist)

    if sem_scores:
        min_s, max_s = min(sem_scores.values()), max(sem_scores.values())
        rng = max_s - min_s or 1.0
        sem_scores = {k: (v - min_s) / rng for k, v in sem_scores.items()}

    # ── FTS5 scores ───────────────────────────────────────────────────────────
    fts_rows = conn.execute(
        "SELECT chunk_id, rank FROM chunks_fts WHERE chunk_text MATCH ? ORDER BY rank LIMIT ?",
        (query, top_k * 5),
    ).fetchall()

    fts_scores: dict[str, float] = {}
    if fts_rows:
        raw = {r["chunk_id"]: -r["rank"] for r in fts_rows}
        min_f, max_f = min(raw.values()), max(raw.values())
        rng = max_f - min_f or 1.0
        fts_scores = {k: (v - min_f) / rng for k, v in raw.items()}

    # ── Combine ───────────────────────────────────────────────────────────────
    all_ids  = set(sem_scores) | set(fts_scores)
    combined = {
        cid: semantic_weight * sem_scores.get(cid, 0.0)
             + (1 - semantic_weight) * fts_scores.get(cid, 0.0)
        for cid in all_ids
    }
    top_ids = sorted(combined, key=combined.get, reverse=True)[:top_k]

    # ── Fetch metadata ────────────────────────────────────────────────────────
    placeholders = ",".join("?" * len(top_ids))
    rows  = conn.execute(
        f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})", top_ids
    ).fetchall()

    results = []
    by_id   = {r["chunk_id"]: r for r in rows}
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
            "chunk_text":         (r["chunk_text"] or "")[:300] + "…",
        })

    conn.close()
    return results


# ── Pipeline phase ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Indexer — last phase of pipeline")
    parser.add_argument("--meta",       default=str(META_PATH),  metavar="PATH")
    parser.add_argument("--papers-dir", default=str(PAPERS_DIR), metavar="PATH")
    parser.add_argument("--docs-dir",   default=str(DOCS_DIR),   metavar="PATH")
    parser.add_argument("--index-dir",  default=str(INDEX_DIR),  metavar="PATH")
    parser.add_argument("--signal",     default=None, metavar="PATH",
                        help="Path to signal_DATE.json to resolve social_trend_names and related_terms")
    parser.add_argument("--query",      default=None,
                        help="Run a test hybrid search after indexing")
    parser.add_argument("--top-k",      default=5, type=int)
    args = parser.parse_args()

    meta_path   = Path(args.meta)
    papers_dir  = Path(args.papers_dir)
    docs_dir    = Path(args.docs_dir)
    index_dir   = Path(args.index_dir)
    signal_path = Path(args.signal) if args.signal else _latest_signal()

    if not meta_path.exists():
        sys.exit(f"papers_meta.json not found: {meta_path}")

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    signal_index = _load_signal_index(signal_path)
    if signal_index:
        print(f"  signal      {signal_path.name}  ({len(signal_index)} terms)")
    else:
        print("  signal      not provided — social_trend_names will be empty")

    to_index = [
        p for p in meta.values()
        if p.get("download_status") in ("downloaded", "already_exists")
    ]

    print("Indexer")
    print(f"  meta        {meta_path.name}  ({len(meta)} papers total)")
    print(f"  to index    {len(to_index)} downloaded papers")
    print(f"  docs dir    {docs_dir}")
    print(f"  model       {EMBED_MODEL}  ({EMBED_DIMS}d)")
    print(f"  chunks      {CHUNK_TOKENS} tokens  overlap {OVERLAP_TOKENS}")
    print(f"  output      {index_dir}")
    print()

    index_dir.mkdir(parents=True, exist_ok=True)
    db_conn = _init_db(index_dir / "chunks.db")

    all_chunk_texts: list[str] = []
    all_chunk_ids:   list[str] = []
    paper_chunks:    list[tuple] = []

    # ── Paper chunks ──────────────────────────────────────────────────────────
    print("  [papers]")
    for paper in to_index:
        pid      = paper["openalex_id"]
        pdf_path = papers_dir / f"{pid}.pdf"
        text     = _extract_text(pdf_path)
        if not text.strip():
            print(f"  ✗ no text   {(paper.get('title') or '')[:60]}")
            continue

        term_ids           = paper.get("parent_term_ids", [])
        social_trend_names = list({
            signal_index[tid]["social_trend_name"]
            for tid in term_ids if tid in signal_index
        })
        related_terms = list({
            rt
            for tid in term_ids if tid in signal_index
            for rt in signal_index[tid]["related_terms"]
        })

        chunks = _chunk_text(text)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{pid}_{i}"
            all_chunk_texts.append(chunk)
            all_chunk_ids.append(chunk_id)
            paper_chunks.append((chunk_id, paper, i, chunk, social_trend_names, related_terms))

        print(f"  ✓ {len(chunks):3d} chunks  {(paper.get('title') or '')[:60]}")

    print(f"  subtotal    {len(all_chunk_texts)} paper chunks")

    # ── Document chunks ───────────────────────────────────────────────────────
    print()
    print("  [documents]")
    doc_chunk_records = _collect_doc_chunks(docs_dir, signal_index)
    for rec in doc_chunk_records:
        chunk_id, *_ = rec
        all_chunk_texts.append(rec[6])
        all_chunk_ids.append(chunk_id)
    print(f"  subtotal    {len(doc_chunk_records)} document chunks")

    print()
    print(f"  total       {len(all_chunk_texts)} chunks")

    if not all_chunk_texts:
        sys.exit("No chunks to index — run paper_downloader.py and doc_generator.py first.")

    print(f"  embedding … (batches of {EMBED_BATCH})")

    all_vectors: list[np.ndarray] = []
    for i in range(0, len(all_chunk_texts), EMBED_BATCH):
        batch = all_chunk_texts[i : i + EMBED_BATCH]
        vecs  = _embed_batch(batch)
        all_vectors.append(vecs)
        print(f"  embedded {min(i + EMBED_BATCH, len(all_chunk_texts))}/{len(all_chunk_texts)}")

    vectors = np.vstack(all_vectors)

    faiss_index = faiss.IndexFlatIP(EMBED_DIMS)
    faiss_index.add(vectors)
    faiss.write_index(faiss_index, str(index_dir / "faiss.index"))
    with open(index_dir / "faiss_ids.json", "w") as f:
        json.dump(all_chunk_ids, f)

    for chunk_id, paper, chunk_idx, chunk_text, social_trend_names, related_terms in paper_chunks:
        _insert_chunk(db_conn, chunk_id, paper, chunk_idx, chunk_text,
                      social_trend_names, related_terms)
    for chunk_id, term_id, track, social_name, related, chunk_idx, chunk_text in doc_chunk_records:
        _insert_doc_chunk(db_conn, chunk_id, term_id, track, social_name,
                          related, chunk_idx, chunk_text)
    db_conn.execute(_POPULATE_FTS)
    db_conn.commit()
    db_conn.close()

    print()
    print(f"  FAISS index   {faiss_index.ntotal} vectors → faiss.index")
    print(f"  SQLite        {len(paper_chunks)} paper + {len(doc_chunk_records)} doc rows → chunks.db")
    print(f"\nDone → {index_dir}")

    if args.query:
        print(f"\n── Test search: '{args.query}' (top {args.top_k}) ──")
        results = search(args.query, top_k=args.top_k,
                         db_path=index_dir / "chunks.db",
                         faiss_index_path=index_dir / "faiss.index",
                         faiss_ids_path=index_dir / "faiss_ids.json")
        for r in results:
            print(f"\n  [{r['combined_score']:.3f}]  sem={r['semantic_score']:.3f}"
                  f"  fts={r['fts_score']:.3f}  type={r['doc_type']}  score={r['final_score']}")
            print(f"  {r['title']}  ({r['year']})")
            print(f"  trends: {r['social_trend_names']}")
            print(f"  {r['chunk_text']}")


if __name__ == "__main__":
    main()
