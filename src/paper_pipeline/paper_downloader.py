"""
Paper Downloader — Phase 5 of the EMERGING track.

Reads papers_meta.json, attempts to download each paper as a PDF,
and writes downloaded files to data/papers/<openalex_id>.pdf.

Download strategy (no Selenium, no SciHub):
  1. arXiv URL     → direct requests to arxiv.org/pdf/<id>.pdf
  2. Direct PDF    → oa_url ends in .pdf  OR  HEAD returns content-type: application/pdf
  3. Unpaywall     → looks up DOI in Unpaywall API for a direct PDF URL
                     Requires OPENALEX_MAILTO env var or defaults to pedro@everme.ai.
                     Only attempted when strategies 1 and 2 are unavailable.
  4. Otherwise     → skip (not downloadable without scraping)

Idempotent: if the PDF already exists on disk, the paper is skipped.
papers_meta.json is updated with download_status and pdf_path per paper.

Download statuses written to papers_meta:
  downloaded     — successfully downloaded this run
  already_exists — file already present from a previous run
  no_oa_url      — no open-access URL and no DOI for Unpaywall lookup
  not_pdf        — URL present but not a direct-download PDF and Unpaywall returned nothing
  failed         — request made but download failed

Unpaywall strategy was added after observing that many journal pages with oa_url
pointing to HTML landing pages could still yield a direct PDF via Unpaywall's
best_oa_location.url_for_pdf field. This reduced not_pdf cases significantly.

Adapted from longevus/src/paper_downloader/tools/paper_download_tools.py
  (download_from_arxiv, _looks_like_pdf, _extract_arxiv_id, direct fast-path)
  Selenium and Anna's Archive explicitly excluded.

Usage:
  python src/paper_pipeline/paper_downloader.py
  python src/paper_pipeline/paper_downloader.py --meta data/papers_meta.json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR            = Path(__file__).resolve().parent
PAPERS_DIR          = BASE_DIR / "data" / "papers"
META_PATH           = BASE_DIR / "data" / "papers_meta.json"
REQUEST_DELAY       = 1.0   # seconds between download attempts
HEAD_TIMEOUT        = 8     # seconds for HEAD request
DL_TIMEOUT          = 60    # seconds for full download
UNPAYWALL_MAILTO    = "pedro@everme.ai"


# ── Helpers (from longevus paper_download_tools.py) ───────────────────────────

def _looks_like_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(2048).lstrip().startswith(b"%PDF")
    except Exception:
        return False


def _extract_arxiv_id(url: str) -> Optional[str]:
    for pattern in [
        r"arxiv\.org/(?:abs|pdf)/([a-zA-Z\-]+/\d+)",
        r"arxiv\.org/(?:abs|pdf)/(\d+\.\d+)",
    ]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _download_arxiv(arxiv_id: str, dest: Path) -> bool:
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    try:
        with requests.get(pdf_url, stream=True, timeout=DL_TIMEOUT,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            r.raise_for_status()
            ct = r.headers.get("content-type", "").lower()
            if "pdf" not in ct and "octet-stream" not in ct:
                return False
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(8192):
                    if chunk:
                        fh.write(chunk)
        return _looks_like_pdf(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        return False


def _is_direct_pdf(url: str) -> bool:
    """True if URL ends in .pdf, or HEAD reports content-type: application/pdf."""
    if url.lower().split("?")[0].endswith(".pdf"):
        return True
    try:
        head = requests.head(url, allow_redirects=True, timeout=HEAD_TIMEOUT,
                             headers={"User-Agent": "Mozilla/5.0"})
        return "pdf" in head.headers.get("content-type", "").lower()
    except Exception:
        return False


def _unpaywall_pdf_url(doi: str) -> Optional[str]:
    """Query Unpaywall for a direct PDF URL given a DOI."""
    if not doi:
        return None
    url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_MAILTO}"
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        best = resp.json().get("best_oa_location") or {}
        return best.get("url_for_pdf") or None
    except Exception:
        return None


def _download_direct(url: str, dest: Path) -> bool:
    try:
        parsed   = urlparse(url)
        filename = unquote(Path(parsed.path).name) or "download.pdf"
        with requests.get(url, stream=True, timeout=DL_TIMEOUT,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(8192):
                    if chunk:
                        fh.write(chunk)
        return _looks_like_pdf(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        return False


# ── Core download logic ───────────────────────────────────────────────────────

def _download_paper(paper: dict, papers_dir: Path) -> tuple[str, Optional[str]]:
    """
    Attempt to download one paper.
    Returns (status, pdf_path_or_None).
    """
    openalex_id = paper["openalex_id"]
    oa_url      = paper.get("oa_url") or ""
    doi         = paper.get("doi") or ""
    dest        = papers_dir / f"{openalex_id}.pdf"

    # Idempotent — skip if already on disk
    if dest.exists() and _looks_like_pdf(dest):
        return "already_exists", str(dest)

    # Strategy 1 — arXiv
    if oa_url:
        arxiv_id = _extract_arxiv_id(oa_url)
        if arxiv_id:
            ok = _download_arxiv(arxiv_id, dest)
            return ("downloaded", str(dest)) if ok else ("failed", None)

    # Strategy 2 — direct PDF via oa_url
    if oa_url and _is_direct_pdf(oa_url):
        ok = _download_direct(oa_url, dest)
        return ("downloaded", str(dest)) if ok else ("failed", None)

    # Strategy 3 — Unpaywall (requires DOI)
    # Trust url_for_pdf directly — Unpaywall guarantees it points to a PDF, but
    # the URL may not end in .pdf and may return application/octet-stream (e.g. OSF).
    if doi:
        pdf_url = _unpaywall_pdf_url(doi)
        if pdf_url:
            arxiv_id = _extract_arxiv_id(pdf_url)
            if arxiv_id:
                ok = _download_arxiv(arxiv_id, dest)
            else:
                ok = _download_direct(pdf_url, dest)
            return ("downloaded", str(dest)) if ok else ("failed", None)

    if not oa_url and not doi:
        return "no_oa_url", None

    return "not_pdf", None


# ── Pipeline phase ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Paper Downloader — phase 5 of EMERGING track")
    parser.add_argument("--meta", default=str(META_PATH), metavar="PATH",
                        help="Path to papers_meta.json")
    parser.add_argument("--papers-dir", default=str(PAPERS_DIR), metavar="PATH",
                        help="Directory to save PDFs")
    args = parser.parse_args()

    meta_path   = Path(args.meta)
    papers_dir  = Path(args.papers_dir)

    if not meta_path.exists():
        sys.exit(f"papers_meta.json not found: {meta_path}")

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    papers_dir.mkdir(parents=True, exist_ok=True)

    # Include not_pdf papers — they can now succeed via the Unpaywall strategy
    to_process = [
        p for p in meta.values()
        if p.get("download_status") not in ("downloaded", "already_exists")
    ]

    print("Paper Downloader")
    print(f"  meta            {meta_path.name}  ({len(meta)} papers total)")
    print(f"  to process      {len(to_process)}")
    print(f"  output dir      {papers_dir}")
    print()

    counts = {"downloaded": 0, "already_exists": 0,
              "no_oa_url": 0, "not_pdf": 0, "failed": 0}

    for paper in to_process:
        pid   = paper["openalex_id"]
        title = (paper.get("title") or "")[:60]
        oa    = paper.get("oa_url") or ""

        status, pdf_path = _download_paper(paper, papers_dir)
        counts[status] += 1

        meta[pid]["download_status"] = status
        meta[pid]["pdf_path"]        = pdf_path

        icon = {"downloaded": "✓", "already_exists": "·",
                "no_oa_url": "–", "not_pdf": "~", "failed": "✗"}.get(status, "?")
        print(f"  {icon} [{status:<14}]  {title}")

        if status == "downloaded":
            time.sleep(REQUEST_DELAY)

    # Persist updated meta
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print()
    print(f"  downloaded      {counts['downloaded']}")
    print(f"  already exists  {counts['already_exists']}")
    print(f"  no OA url       {counts['no_oa_url']}")
    print(f"  not direct PDF  {counts['not_pdf']}")
    print(f"  failed          {counts['failed']}")
    print(f"\nDone → {papers_dir}")


if __name__ == "__main__":
    main()
