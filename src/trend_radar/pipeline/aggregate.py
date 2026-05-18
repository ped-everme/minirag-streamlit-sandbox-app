"""
M2 — Cross-platform aggregator for Mini-RAG.

Reads the four raw collector outputs (YouTube, Google Trends, Twitter, TikTok),
extracts and derives metrics per term, normalises 0–1 within the batch,
computes Hype and Emerging scores, and classifies each term.

Outputs two files to data/output/:
  signal_DATE.json  — lean operational payload (scores, label, drivers, routing)
  audit_DATE.json   — full detail (raw + derived + normalised metrics)

Run:
  python src/trend_radar/pipeline/aggregate.py
  python src/trend_radar/pipeline/aggregate.py --date 2026-04-29
  python src/trend_radar/pipeline/aggregate.py \
    --youtube src/trend_radar/data/raw/youtube_2026-04-29.json \
    --google-trends src/trend_radar/data/raw/google_trends_2026-04-29.json \
    --twitter src/trend_radar/data/raw/twitter_2026-04-29.json \
    --tiktok src/trend_radar/data/raw/tiktok_2026-04-30.json
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# ── Weights ────────────────────────────────────────────────────────────────────

HYPE_WEIGHTS = {
    "tt_avg_shares":     0.25,
    "tw_avg_retweets":   0.20,
    "gt_velocity_90d":   0.20,
    "gt_above_baseline": 0.20,
    "yt_peak_ratio":     0.15,
}

EMERGING_WEIGHTS = {
    "gt_velocity_365d":  0.35,
    "gt_avg_365d":       0.30,
    "yt_avg_vpd_365d":   0.20,
    "tt_avg_plays":      0.15,
}

HYPE_THRESHOLD     = 0.5
EMERGING_THRESHOLD = 0.5

ROUTE_MAP: dict[str, list[str]] = {
    "HYPED":                   ["today_take"],
    "EMERGING":                ["rag"],
    "HYPED + EMERGING":        ["today_take", "rag"],
    "HYPED (platform-native)": ["today_take"],
    "DECLINING":               [],
    "ESTABLISHED":             [],
    "UNCLASSIFIED":            [],
}


# ── File discovery ─────────────────────────────────────────────────────────────

def latest_file(pattern: str) -> Path | None:
    files = sorted((BASE_DIR / "data" / "raw").glob(pattern))
    return files[-1] if files else None


def resolve_sources(args) -> dict[str, Path]:
    sources = {
        "youtube":       Path(args.youtube)       if args.youtube       else latest_file("youtube_*.json"),
        "google_trends": Path(args.google_trends) if args.google_trends else latest_file("google_trends_*.json"),
        "twitter":       Path(args.twitter)       if args.twitter       else latest_file("twitter_*.json"),
        "tiktok":        Path(args.tiktok)        if args.tiktok        else latest_file("tiktok_*.json"),
    }
    missing = [k for k, v in sources.items() if v is None or not v.exists()]
    if missing:
        raise SystemExit(f"Cannot find raw files for: {', '.join(missing)}\n"
                         "Run the collectors first or pass explicit paths.")
    return sources


# ── Load and index ─────────────────────────────────────────────────────────────

def load_by_term_id(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {t["term_id"]: t for t in data["terms"]}


# ── Raw metric extraction ──────────────────────────────────────────────────────

def extract_raw(term_id: str, yt: dict, gt: dict, tw: dict, tt: dict) -> dict:
    raw = {}

    # YouTube ──────────────────────────────────────────────────────────────────
    yt_term = yt.get(term_id, {})
    w90  = yt_term.get("windows", {}).get("90d", {})
    w365 = yt_term.get("windows", {}).get("365d", {})
    raw["yt_top_vpd_90d"]  = w90.get("top_views_per_day")  if w90  else None
    raw["yt_top_vpd_365d"] = w365.get("top_views_per_day") if w365 else None
    raw["yt_avg_vpd_90d"]  = w90.get("avg_views_per_day")  if w90  else None
    raw["yt_avg_vpd_365d"] = w365.get("avg_views_per_day") if w365 else None

    # Google Trends ────────────────────────────────────────────────────────────
    gt_term = gt.get(term_id, {})
    gw90  = gt_term.get("windows", {}).get("90d",  {})
    gw365 = gt_term.get("windows", {}).get("365d", {})

    gt_low_90  = gw90.get("low_data", True)
    gt_low_365 = gw365.get("low_data", True)

    raw["gt_low_data"] = gt_low_90 and gt_low_365  # both windows empty

    raw["gt_velocity_90d"]  = None if gt_low_90  else gw90.get("velocity")
    raw["gt_current_90d"]   = None if gt_low_90  else gw90.get("current_score")
    raw["gt_peak_date_90d"] = None if gt_low_90  else gw90.get("peak_date")
    raw["gt_velocity_365d"] = None if gt_low_365 else gw365.get("velocity")
    raw["gt_avg_365d"]      = None if gt_low_365 else gw365.get("avg_score")

    # Twitter ──────────────────────────────────────────────────────────────────
    tw_term = tw.get(term_id, {})
    has_tw  = tw_term.get("tweet_count", 0) > 0
    raw["tw_avg_retweets"] = tw_term.get("avg_retweets") if has_tw else None
    raw["tw_avg_likes"]    = tw_term.get("avg_likes")    if has_tw else None
    raw["tw_top_retweets"] = tw_term.get("top_retweets") if has_tw else None

    # TikTok ───────────────────────────────────────────────────────────────────
    tt_term  = tt.get(term_id, {})
    has_tt   = tt_term.get("video_count", 0) > 0
    raw["tt_avg_shares"]   = tt_term.get("avg_shares")   if has_tt else None
    raw["tt_top_plays"]    = tt_term.get("top_plays")    if has_tt else None
    raw["tt_avg_plays"]    = tt_term.get("avg_plays")    if has_tt else None
    raw["tt_avg_diggs"]    = tt_term.get("avg_diggs")    if has_tt else None
    raw["tt_avg_comments"] = tt_term.get("avg_comments") if has_tt else None

    return raw


def compute_derived(raw: dict, run_date: datetime) -> dict:
    derived = {}

    yt_top_90  = raw["yt_top_vpd_90d"]
    yt_top_365 = raw["yt_top_vpd_365d"]
    yt_avg_90  = raw["yt_avg_vpd_90d"]
    yt_avg_365 = raw["yt_avg_vpd_365d"]

    derived["yt_peak_ratio"] = (
        round(yt_top_90 / yt_top_365, 4) if yt_top_90 and yt_top_365 else None
    )
    derived["yt_momentum"] = (
        round(yt_avg_90 / yt_avg_365, 4) if yt_avg_90 and yt_avg_365 else None
    )

    peak_date_str = raw.get("gt_peak_date_90d")
    if peak_date_str:
        try:
            peak_dt = datetime.fromisoformat(peak_date_str)
            derived["gt_days_since_peak"] = (run_date.date() - peak_dt.date()).days
        except ValueError:
            derived["gt_days_since_peak"] = None
    else:
        derived["gt_days_since_peak"] = None

    current_90 = raw.get("gt_current_90d")
    avg_365    = raw.get("gt_avg_365d")
    derived["gt_above_baseline"] = (
        round(current_90 / avg_365, 4) if current_90 is not None and avg_365 else None
    )

    return derived


def count_platforms(raw: dict) -> int:
    count = 0
    if raw.get("yt_top_vpd_365d"):
        count += 1
    # GT counts if at least one window has data
    if not (raw.get("gt_low_data") and raw.get("gt_velocity_90d") is None):
        if raw.get("gt_velocity_90d") is not None or raw.get("gt_velocity_365d") is not None:
            count += 1
    if raw.get("tw_avg_retweets") is not None:
        count += 1
    if raw.get("tt_avg_plays") is not None:
        count += 1
    return count


# ── Normalisation ──────────────────────────────────────────────────────────────

def normalise_batch(all_metrics: list[dict], metric_keys: list[str]) -> list[dict]:
    """For each metric key, compute min/max across terms (ignoring nulls) and normalise."""
    normalised = [{} for _ in all_metrics]

    for key in metric_keys:
        values = [m.get(key) for m in all_metrics]
        valid  = [v for v in values if v is not None]

        if not valid:
            continue

        lo, hi = min(valid), max(valid)

        for i, v in enumerate(values):
            if v is None:
                normalised[i][key] = None
            elif hi == lo:
                normalised[i][key] = 0.5
            else:
                normalised[i][key] = round((v - lo) / (hi - lo), 4)

    return normalised


def weighted_score(normalised: dict, weights: dict) -> float | None:
    total_w, total_v = 0.0, 0.0
    for key, w in weights.items():
        v = normalised.get(key)
        if v is not None:
            total_v += v * w
            total_w += w
    if total_w == 0:
        return None
    return round(total_v / total_w, 4)


# ── Classification ─────────────────────────────────────────────────────────────

def classify(
    hype_score: float | None,
    emerging_score: float | None,
    raw: dict,
    normalised: dict,
) -> str:
    platforms = count_platforms(raw)

    if platforms < 2:
        return "UNCLASSIFIED"

    # Platform-native hype: GT all low_data but TikTok shares are strong
    gt_all_low = raw.get("gt_low_data", False)
    tt_shares_norm = normalised.get("tt_avg_shares")
    if gt_all_low and tt_shares_norm is not None and tt_shares_norm > 0.5:
        return "HYPED (platform-native)"

    h = hype_score or 0.0
    e = emerging_score or 0.0

    if h > HYPE_THRESHOLD and e > EMERGING_THRESHOLD:
        return "HYPED + EMERGING"
    if h > HYPE_THRESHOLD:
        return "HYPED"
    if e > EMERGING_THRESHOLD:
        return "EMERGING"

    # Declining: long-term negative + current peak weak
    gt_vel_365 = raw.get("gt_velocity_365d") or 0.0
    yt_peak    = (raw.get("yt_top_vpd_90d") or 0) / max(raw.get("yt_top_vpd_365d") or 1, 1)
    if gt_vel_365 < -0.10 and yt_peak < 0.3:
        return "DECLINING"

    return "ESTABLISHED"


# ── Signal drivers ────────────────────────────────────────────────────────────

def _top_drivers(weights: dict, normalised: dict, n: int) -> list[str]:
    scored = [
        (key, (normalised.get(key) or 0.0) * w)
        for key, w in weights.items()
        if normalised.get(key) is not None
    ]
    scored.sort(key=lambda x: -x[1])
    return [k for k, _ in scored[:n]]


def compute_signal_drivers(label: str, normalised: dict, n: int = 3) -> list[str]:
    if label == "HYPED":
        return _top_drivers(HYPE_WEIGHTS, normalised, n)
    if label == "EMERGING":
        return _top_drivers(EMERGING_WEIGHTS, normalised, n)
    if label == "HYPED + EMERGING":
        h = _top_drivers(HYPE_WEIGHTS, normalised, 2)
        e = _top_drivers(EMERGING_WEIGHTS, normalised, 2)
        seen, merged = set(), []
        for d in h + e:
            if d not in seen:
                seen.add(d)
                merged.append(d)
            if len(merged) >= n:
                break
        return merged
    if label == "HYPED (platform-native)":
        return _top_drivers({"tt_avg_shares": 1.0, "tt_avg_plays": 0.5}, normalised, 2)
    return _top_drivers({**HYPE_WEIGHTS, **EMERGING_WEIGHTS}, normalised, n)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="M2 — Mini-RAG cross-platform aggregator")
    parser.add_argument("--youtube",       default=None)
    parser.add_argument("--google-trends", default=None, dest="google_trends")
    parser.add_argument("--twitter",       default=None)
    parser.add_argument("--tiktok",        default=None)
    parser.add_argument("--date",          default=None,
                        help="Pick all four source files by date, e.g. --date 2026-04-29")
    parser.add_argument("--hype-threshold",     type=float, default=HYPE_THRESHOLD)
    parser.add_argument("--emerging-threshold", type=float, default=EMERGING_THRESHOLD)
    parser.add_argument("--terms", default=None,
                        help="Path to terms.json for related_terms enrichment "
                             "(default: src/trend_radar/data/terms.json, then data/mock/terms.json)")
    args = parser.parse_args()

    # --date expands to per-source paths only when individual flags are not set
    if args.date:
        for attr, pattern in [("youtube", "youtube"), ("google_trends", "google_trends"),
                               ("twitter", "twitter"), ("tiktok", "tiktok")]:
            if getattr(args, attr) is None:
                setattr(args, attr, str(BASE_DIR / "data" / "raw" / f"{pattern}_{args.date}.json"))

    sources = resolve_sources(args)

    print("M2 Aggregator")
    for name, path in sources.items():
        print(f"  {name:<15} {path}")
    print()

    yt = load_by_term_id(sources["youtube"])
    gt = load_by_term_id(sources["google_trends"])
    tw = load_by_term_id(sources["twitter"])
    tt = load_by_term_id(sources["tiktok"])

    # Load related_terms from terms.json — try explicit path, then real, then mock
    terms_candidates = [
        Path(args.terms) if args.terms else None,
        BASE_DIR / "data" / "terms.json",
        BASE_DIR / "data" / "mock" / "terms.json",
    ]
    terms_lookup: dict[str, list[str]] = {}
    for candidate in terms_candidates:
        if candidate and candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                for entry in json.load(f):
                    terms_lookup[entry["id"]] = entry.get("related_terms", [])
            print(f"  terms           {candidate}")
            break

    all_term_ids = sorted(set(yt) | set(gt) | set(tw) | set(tt))
    run_date = datetime.now(timezone.utc)

    # ── Extract raw + derived ────────────────────────────────────────────────
    records = []
    for term_id in all_term_ids:
        yt_term = yt.get(term_id, {})
        raw     = extract_raw(term_id, yt, gt, tw, tt)
        derived = compute_derived(raw, run_date)
        records.append({
            "term_id":           term_id,
            "social_trend_name": yt_term.get("social_trend_name", term_id),
            "underlying_topic":  yt_term.get("underlying_topic", ""),
            "everme_category":   yt_term.get("everme_category", ""),
            "related_terms":     terms_lookup.get(term_id, []),
            "platforms_with_data": count_platforms(raw),
            "raw":     raw,
            "derived": derived,
        })

    # ── Normalise ────────────────────────────────────────────────────────────
    # Merge raw + derived into one flat dict per term for normalisation
    all_metrics = []
    for r in records:
        merged = {**r["raw"], **r["derived"]}
        all_metrics.append(merged)

    score_keys = list(HYPE_WEIGHTS.keys()) + list(EMERGING_WEIGHTS.keys())
    normalised_batch = normalise_batch(all_metrics, score_keys)

    # ── Score + classify ─────────────────────────────────────────────────────
    signal_terms = []
    audit_terms  = []

    for i, rec in enumerate(records):
        norm        = normalised_batch[i]
        hype_score  = weighted_score(norm, HYPE_WEIGHTS)
        emrg_score  = weighted_score(norm, EMERGING_WEIGHTS)
        label       = classify(hype_score, emrg_score, {**rec["raw"], **rec["derived"]}, norm)
        drivers     = compute_signal_drivers(label, norm)
        routed_to   = ROUTE_MAP[label]

        signal_terms.append({
            "term_id":             rec["term_id"],
            "social_trend_name":   rec["social_trend_name"],
            "underlying_topic":    rec["underlying_topic"],
            "everme_category":     rec["everme_category"],
            "related_terms":       rec["related_terms"],
            "classification":      label,
            "hype_score":          hype_score,
            "emerging_score":      emrg_score,
            "platforms_with_data": rec["platforms_with_data"],
            "signal_drivers":      drivers,
            "routed_to":           routed_to,
        })

        audit_terms.append({
            "term_id":             rec["term_id"],
            "social_trend_name":   rec["social_trend_name"],
            "underlying_topic":    rec["underlying_topic"],
            "everme_category":     rec["everme_category"],
            "related_terms":       rec["related_terms"],
            "classification":      label,
            "hype_score":          hype_score,
            "emerging_score":      emrg_score,
            "platforms_with_data": rec["platforms_with_data"],
            "signal_drivers":      drivers,
            "routed_to":           routed_to,
            "raw_metrics":         rec["raw"],
            "derived_metrics":     rec["derived"],
            "normalised":          norm,
        })

        h_str = f"{hype_score:.2f}" if hype_score is not None else " n/a"
        e_str = f"{emrg_score:.2f}" if emrg_score is not None else " n/a"
        print(f"  {rec['social_trend_name']:<30}  hype={h_str}  emrg={e_str}  → {label}")

    # ── Write outputs ─────────────────────────────────────────────────────────
    date_str   = run_date.strftime("%Y-%m-%d")
    ts_str     = run_date.strftime("%Y-%m-%dT%H:%M:%SZ")
    out_dir    = BASE_DIR / "data" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    envelope = {
        "run_date":      date_str,
        "generated_at":  ts_str,
        "schema_version": "1.0",
        "sources": {k: str(v) for k, v in sources.items()},
        "thresholds": {
            "hype":     args.hype_threshold,
            "emerging": args.emerging_threshold,
        },
        "term_count": len(signal_terms),
    }

    signal_path = out_dir / f"signal_{date_str}.json"
    with open(signal_path, "w", encoding="utf-8") as f:
        json.dump({**envelope, "terms": signal_terms}, f, ensure_ascii=False, indent=2)

    audit_path = out_dir / f"audit_{date_str}.json"
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump({**envelope, "terms": audit_terms}, f, ensure_ascii=False, indent=2)

    # ── Summary ──────────────────────────────────────────────────────────────
    from collections import Counter
    counts = Counter(t["classification"] for t in signal_terms)
    print(f"\nDone → {signal_path}")
    print(f"       {audit_path}")
    print("\nClassification summary:")
    for label, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {n:>2}  {label}")


if __name__ == "__main__":
    main()
