"""
Catalog — Phase 2 of the EMERGING track.

Pipeline phase: reads the latest expansion_DATE.json produced by term_expander,
ingests all queries into the cumulative catalog.json, and prints a summary.

The Catalog class is also imported by paper_search and longevus_checker to read
pending queries and write back results — this is the shared data layer between
those phases. All phases still run sequentially; the class is just shared logic.

File: data/catalog.json  (committed to git — grows over time)

Entry status lifecycle:
  active           → registered, not yet searched
  searched         → OpenAlex search completed (paper_count set)
  no_results       → searched, 0 papers found
  longevus_covered → topic already in Longevus; skip download forever

Usage (pipeline phase):
  python src/paper_pipeline/catalog.py
  python src/paper_pipeline/catalog.py --expansion data/expansions/expansion_2026-05-13.json

Usage (as shared data layer — imported by paper_search, longevus_checker):
  from paper_pipeline.catalog import Catalog
  cat = Catalog.load()
  for entry in cat.pending_search(): ...
  cat.mark_searched(query, paper_count=14)
  cat.save()
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG_PATH = BASE_DIR / "data" / "catalog.json"
SCHEMA_VERSION = "1.0"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def normalize(query: str) -> str:
    """Lowercase + strip punctuation — canonical form used for dedup."""
    return re.sub(r"[^\w\s]", "", query.lower()).strip()


def _latest_expansion() -> Optional[Path]:
    d = BASE_DIR / "data" / "expansions"
    files = sorted(d.glob("expansion_*.json"))
    return files[-1] if files else None


# ── Catalog class ─────────────────────────────────────────────────────────────

class Catalog:
    def __init__(self, data: dict, path: Path):
        self._data = data
        self.path = path
        self._index: dict[str, int] = {
            e["query_normalized"]: i for i, e in enumerate(self._data["entries"])
        }

    @classmethod
    def load(cls, path: Path = DEFAULT_CATALOG_PATH) -> "Catalog":
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {
                "schema_version": SCHEMA_VERSION,
                "created_at": _today(),
                "last_updated": _today(),
                "entries": [],
            }
        return cls(data, path)

    def save(self) -> None:
        """Atomic write via temp file → rename."""
        self._data["last_updated"] = _today()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            os.unlink(tmp)
            raise

    # ── Write API (called by pipeline phases) ─────────────────────────────────

    def upsert(self, query: str, qtype: str, confidence: str,
               parent_term_id: str, date: Optional[str] = None) -> bool:
        """Add query if not already present. Returns True if new entry created."""
        key = normalize(query)
        if key in self._index:
            return False
        entry = {
            "query": query,
            "query_normalized": key,
            "type": qtype,
            "confidence": confidence,
            "parent_term_id": parent_term_id,
            "first_seen": date or _today(),
            "last_run": None,
            "paper_count": None,
            "longevus_covered": False,
            "longevus_checked": None,
            "status": "active",
        }
        idx = len(self._data["entries"])
        self._data["entries"].append(entry)
        self._index[key] = idx
        return True

    def ingest_expansion(self, expansion: dict, date: Optional[str] = None) -> tuple[int, int]:
        """Ingest one term's queries from an expansion dict. Returns (new, skipped)."""
        date = date or _today()
        new = skipped = 0
        for q in expansion.get("queries", []):
            added = self.upsert(
                query=q["query"],
                qtype=q.get("type", "compound"),
                confidence=q.get("confidence", "medium"),
                parent_term_id=expansion.get("term_id", ""),
                date=date,
            )
            if added:
                new += 1
            else:
                skipped += 1
        return new, skipped

    def mark_searched(self, query: str, paper_count: int, date: Optional[str] = None) -> None:
        """Called by paper_search after a query completes."""
        entry = self._get(query)
        if entry is None:
            raise KeyError(f"Query not in catalog: {query!r}")
        if entry["longevus_covered"]:
            return
        entry["last_run"] = date or _today()
        entry["paper_count"] = paper_count
        entry["status"] = "no_results" if paper_count == 0 else "searched"

    def mark_longevus_covered(self, query: str, date: Optional[str] = None) -> None:
        """Called by longevus_checker when topic is already in Longevus."""
        entry = self._get(query)
        if entry is None:
            raise KeyError(f"Query not in catalog: {query!r}")
        entry["longevus_covered"] = True
        entry["longevus_checked"] = date or _today()
        entry["status"] = "longevus_covered"

    def mark_longevus_checked(self, query: str, date: Optional[str] = None) -> None:
        """Called by longevus_checker when check ran but topic is NOT covered."""
        entry = self._get(query)
        if entry is None:
            raise KeyError(f"Query not in catalog: {query!r}")
        entry["longevus_checked"] = date or _today()

    # ── Read API ──────────────────────────────────────────────────────────────

    def pending_search(self) -> list[dict]:
        """Entries not yet searched and not covered by Longevus."""
        return [
            e for e in self._data["entries"]
            if not e["longevus_covered"] and e["status"] in ("active", "no_results")
        ]

    def pending_longevus_check(self) -> list[dict]:
        """Entries that have never had a Longevus check."""
        return [e for e in self._data["entries"] if e["longevus_checked"] is None]

    def get(self, query: str) -> Optional[dict]:
        return self._get(query)

    @property
    def entries(self) -> list[dict]:
        return self._data["entries"]

    def stats(self) -> dict:
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for e in self._data["entries"]:
            by_status[e["status"]] = by_status.get(e["status"], 0) + 1
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        return {
            "total": len(self._data["entries"]),
            "by_status": by_status,
            "by_type": by_type,
            "last_updated": self._data.get("last_updated"),
        }

    def _get(self, query: str) -> Optional[dict]:
        idx = self._index.get(normalize(query))
        return self._data["entries"][idx] if idx is not None else None


# ── Pipeline phase ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Catalog — phase 2 of EMERGING track")
    parser.add_argument("--expansion", default=None, metavar="PATH",
                        help="expansion_DATE.json to ingest (default: latest in data/expansions/)")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH), metavar="PATH",
                        help="Path to catalog.json")
    args = parser.parse_args()

    expansion_path = Path(args.expansion) if args.expansion else _latest_expansion()
    if not expansion_path or not expansion_path.exists():
        sys.exit(f"Expansion file not found: {expansion_path}")

    with open(expansion_path, encoding="utf-8") as f:
        expansion_file = json.load(f)

    cat = Catalog.load(Path(args.catalog))
    date = _today()
    total_new = total_skipped = 0

    print("Catalog")
    print(f"  expansion     {expansion_path.name}")
    print(f"  catalog       {Path(args.catalog).name}")
    print()

    for expansion in expansion_file.get("expansions", []):
        new, skipped = cat.ingest_expansion(expansion, date=date)
        total_new += new
        total_skipped += skipped
        print(f"  {expansion['term_id']:<30}  +{new} new  {skipped} already present")

    cat.save()

    s = cat.stats()
    print(f"\n  total in catalog   {s['total']}")
    print(f"  pending search     {len(cat.pending_search())}")
    print(f"\nDone → {cat.path}")


if __name__ == "__main__":
    main()
