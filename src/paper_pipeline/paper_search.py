"""
Paper Search — Phase 4 of the EMERGING track.

Reads pending queries from catalog.json, searches for papers, and writes
results to data/papers_meta.json.

Primary:  SerpAPI Google Scholar → ranked titles → OpenAlex by title for
          full metadata enrichment (abstract, DOI, OA URL, citations, authors).
          Requires SERPAPI_API_KEY env var.

Fallback: OpenAlex free text search — used when SERPAPI_API_KEY is not set
          or SerpAPI returns no results for a query.

Rationale: Google Scholar ranks by relevance (citations, co-occurrence, venue
quality), producing far fewer off-topic results than OpenAlex full-text search.
OpenAlex is still used for metadata enrichment (abstract, OA URL, DOI, etc.)
since Scholar only returns titles and snippets.

Output: data/papers_meta.json — dict keyed by openalex_id.
        Papers found by multiple queries are merged (not duplicated).
        catalog.json is updated with paper_count per query.

Filters applied when using the OpenAlex fallback:
  publication_year > 2021
  type: journal-article or preprint
  cited_by_count >= 2

Post-fetch relevance filter (applied to all sources):
  Discards papers where query keywords don't appear in title or abstract:
    - at least 1 keyword must appear in the title, OR
    - at least 1 keyword must appear in the abstract.
  Stop words and tokens shorter than 3 chars are excluded from keywords.

--rerun flag: resets all catalog entries to status=active so the search
  runs again from scratch (useful when re-tuning filters).

Usage:
  python src/paper_pipeline/paper_search.py
  python src/paper_pipeline/paper_search.py --rerun
  python src/paper_pipeline/paper_search.py --catalog data/catalog.json
"""

import json
import os
import re
import sys
import time
import argparse
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# Add parent to path so catalog import works when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog import Catalog

BASE_DIR = Path(__file__).resolve().parent
PAPERS_META_PATH = BASE_DIR / "data" / "papers_meta.json"

# ── Config ────────────────────────────────────────────────────────────────────

MIN_YEAR         = 2021       # publication_year > this (OpenAlex fallback only)
MIN_CITATIONS    = 2          # cited_by_count >= this (OpenAlex fallback only)
PER_PAGE         = 25         # results per OpenAlex query
MAX_PER_TERM     = 50         # cap papers per parent_term_id after all queries
SERP_N_PAPERS    = 20         # how many Scholar results to fetch per query
OPENALEX_MAILTO  = "pedro@everme.ai"
REQUEST_DELAY    = 0.5        # seconds between OpenAlex requests (polite pool)

# Relevance filter — words ignored when building keyword set
_STOP = {
    "a", "an", "the", "and", "or", "of", "in", "for", "to", "on", "by",
    "at", "is", "are", "was", "were", "be", "been", "with", "from", "that",
    "this", "as", "it", "its", "into", "via", "after", "before", "between",
    "during", "among", "role", "effect", "effects", "impact", "impacts",
    "study", "review", "analysis", "based", "using", "use",
}


# ── Abstract helper (from longevus openalex_tools.py) ─────────────────────────

def _convert_inverted_abstract(inverted_index: dict) -> str:
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    try:
        positions = []
        for word, locs in inverted_index.items():
            for pos in locs:
                positions.append((pos, word))
        positions.sort(key=lambda x: x[0])
        return " ".join(w for _, w in positions)
    except Exception:
        return ""


# ── Paper dict builder ────────────────────────────────────────────────────────

def _build_paper(work: dict) -> dict:
    doi = work.get("doi", "") or ""
    doi = doi.replace("https://doi.org/", "").strip() or None

    oa = work.get("open_access", {})
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}

    authorships = work.get("authorships", [])
    authors = [
        {
            "name": a.get("author", {}).get("display_name", ""),
            "openalex_id": (a.get("author", {}).get("id") or "").split("/")[-1],
        }
        for a in authorships[:10]
    ]

    abstract_raw = work.get("abstract_inverted_index")
    abstract = _convert_inverted_abstract(abstract_raw) if abstract_raw else ""

    return {
        "openalex_id":    (work.get("id") or "").split("/")[-1],
        "title":          work.get("title") or "",
        "doi":            doi,
        "year":           work.get("publication_year"),
        "cited_by_count": work.get("cited_by_count", 0),
        "is_open_access": oa.get("is_oa", False),
        "oa_url":         oa.get("oa_url") or None,
        "abstract":       abstract,
        "authors":        authors,
        "venue":          source.get("display_name") or None,
        "type":           work.get("type") or None,
    }


# ── Relevance filter ─────────────────────────────────────────────────────────

def _keywords(query: str) -> list[str]:
    """Extract meaningful words from a query for relevance scoring."""
    tokens = re.sub(r"[^\w\s]", " ", query.lower()).split()
    return [t for t in tokens if len(t) >= 3 and t not in _STOP]


def _is_relevant(paper: dict, keywords: list[str]) -> bool:
    """
    True if query keywords appear meaningfully in the paper's title or abstract.

    Pass conditions (OR):
      - at least 1 keyword appears in the title
      - at least 1 keyword appears in the abstract

    Papers with no keywords (very short queries) are always kept.
    """
    if not keywords:
        return True

    title    = (paper.get("title")    or "").lower()
    abstract = (paper.get("abstract") or "").lower()

    title_hits    = sum(1 for kw in keywords if kw in title)
    abstract_hits = sum(1 for kw in keywords if kw in abstract)

    return title_hits >= 1 or abstract_hits >= 1


# ── OpenAlex search ───────────────────────────────────────────────────────────

def _openalex_search(query: str) -> list[dict]:
    """Direct full-text search on OpenAlex /works endpoint."""
    url = "https://api.openalex.org/works"
    params = {
        "search":   query,
        "filter":   (
            f"publication_year:>{MIN_YEAR},"
            f"type:journal-article|preprint,"
            f"cited_by_count:>{MIN_CITATIONS - 1}"
        ),
        "per_page": PER_PAGE,
        "sort":     "cited_by_count:desc",
        "mailto":   OPENALEX_MAILTO,
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.RequestException as exc:
        print(f"    OpenAlex error for '{query}': {exc}", file=sys.stderr)
        return []


# ── SerpAPI primary search ────────────────────────────────────────────────────

def _serpapi_titles(query: str, n: int = SERP_N_PAPERS) -> list[str]:
    """Return paper titles from Google Scholar via SerpAPI."""
    try:
        import serpapi
        client = serpapi.Client(api_key=os.getenv("SERPAPI_API_KEY"))
        titles = []
        for start in range(0, n, 10):
            res = client.search({"engine": "google_scholar", "q": query, "start": start})
            for item in res.get("organic_results", []):
                t = item.get("title")
                if t:
                    titles.append(t)
        return titles[:n]
    except Exception as exc:
        print(f"    SerpAPI error: {exc}", file=sys.stderr)
        return []


def _openalex_by_title(title: str) -> list[dict]:
    """Fetch one OpenAlex work by exact title for metadata enrichment."""
    url = "https://api.openalex.org/works"
    params = {"search.exact": title, "per_page": 1, "mailto": OPENALEX_MAILTO}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.RequestException:
        return []


def _serpapi_search(query: str) -> list[dict]:
    """Primary: SerpAPI Google Scholar → titles → OpenAlex for full metadata."""
    titles = _serpapi_titles(query)
    works = []
    for title in titles:
        works.extend(_openalex_by_title(title))
        time.sleep(0.2)
    return works


# ── papers_meta helpers ───────────────────────────────────────────────────────

def _load_meta(path: Path) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_meta(meta: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _merge_paper(meta: dict, paper: dict, query: str, term_id: str) -> None:
    """Insert paper into meta dict, merging query/term tracking if already present."""
    pid = paper["openalex_id"]
    if not pid:
        return
    if pid not in meta:
        meta[pid] = {**paper, "found_by_queries": [], "parent_term_ids": []}
    entry = meta[pid]
    if query not in entry["found_by_queries"]:
        entry["found_by_queries"].append(query)
    if term_id not in entry["parent_term_ids"]:
        entry["parent_term_ids"].append(term_id)


def _apply_per_term_cap(meta: dict) -> dict:
    """Keep only the top MAX_PER_TERM papers per parent_term_id by citation count."""
    by_term: dict[str, list] = {}
    for pid, paper in meta.items():
        for tid in paper.get("parent_term_ids", []):
            by_term.setdefault(tid, []).append(paper)

    keep_ids: set[str] = set()
    for tid, papers in by_term.items():
        papers.sort(key=lambda p: p.get("cited_by_count", 0), reverse=True)
        for p in papers[:MAX_PER_TERM]:
            keep_ids.add(p["openalex_id"])

    return {pid: p for pid, p in meta.items() if pid in keep_ids}


# ── Pipeline phase ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Paper Search — phase 4 of EMERGING track")
    parser.add_argument("--catalog", default=None, metavar="PATH")
    parser.add_argument("--output", default=str(PAPERS_META_PATH), metavar="PATH")
    parser.add_argument("--rerun", action="store_true",
                        help="Reset all catalog entries to status=active and re-search from scratch")
    args = parser.parse_args()

    cat = Catalog.load(Path(args.catalog)) if args.catalog else Catalog.load()
    out_path = Path(args.output)

    if args.rerun:
        for entry in cat._data["entries"]:
            if entry.get("status") == "searched":
                entry["status"] = "active"
                entry["paper_count"] = None
        cat.save()
        meta = {}
        print("--rerun: reset all searched entries to active, cleared papers_meta")
    else:
        meta = _load_meta(out_path)

    pending = cat.pending_search()

    serper_key = os.getenv("SERPAPI_API_KEY")
    strategy   = "SerpAPI → OpenAlex" if serper_key else "OpenAlex (no SERPAPI_API_KEY)"

    print("Paper Search")
    print(f"  catalog         {cat.path.name}")
    print(f"  pending queries {len(pending)}")
    print(f"  strategy        {strategy}")
    if not serper_key:
        print(f"  filters         year>{MIN_YEAR}  citations>={MIN_CITATIONS}  types=journal-article|preprint")
    print(f"  output          {out_path.name}")
    print()

    if not pending:
        print("No pending queries — nothing to search.")
        return

    total_found = total_new = 0

    for entry in pending:
        query    = entry["query"]
        term_id  = entry["parent_term_id"]

        print(f"  [{entry['type'][:3]}] {query}")

        if serper_key:
            works  = _serpapi_search(query)
            source = "SerpAPI"
            if not works:
                works  = _openalex_search(query)
                source = "OpenAlex (SerpAPI empty)"
        else:
            works  = _openalex_search(query)
            source = "OpenAlex"

        time.sleep(REQUEST_DELAY)

        keywords = _keywords(query)
        before = len(meta)
        relevant = 0
        filtered = 0
        for work in works:
            paper = _build_paper(work)
            if not paper["openalex_id"]:
                continue
            if _is_relevant(paper, keywords):
                _merge_paper(meta, paper, query, term_id)
                relevant += 1
            else:
                filtered += 1

        new_papers = len(meta) - before
        total_found += len(works)
        total_new   += new_papers
        filter_note = f"  ({filtered} filtered)" if filtered else ""
        print(f"        [{source}] {len(works)} results  {relevant} relevant  {new_papers} new{filter_note}")

        cat.mark_searched(query, paper_count=len(works))

    # Apply per-term cap and persist
    before_cap = len(meta)
    meta = _apply_per_term_cap(meta)
    capped = before_cap - len(meta)

    cat.save()
    _save_meta(meta, out_path)

    print()
    print(f"  total results   {total_found}")
    print(f"  unique papers   {len(meta)}  ({capped} removed by per-term cap of {MAX_PER_TERM})")
    print(f"\nDone → {out_path}")


if __name__ == "__main__":
    main()
