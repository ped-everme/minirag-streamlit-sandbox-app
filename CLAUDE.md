# CLAUDE.md — Mini-RAG

Instructions for Claude Code sessions on this project.

## Project purpose

**Mini-RAG** validates whether health trend terms (surfaced by external LLM deep research) are actually trending on social platforms. Output feeds EverMe's two-track system: hyped terms go to Today's Take / chat; emerging terms reinforce the foundational RAG.

## Architecture in one sentence

`terms.json` (from LLM research) → platform validators (YouTube, Google Trends, Twitter, TikTok) → aggregator → classifier → two outputs (hyped feed + RAG update signal).

## Current milestone

**ETL + mock dataset ready. Streamlit dashboard is next.**

```bash
# 0 — Ingest terms from Rafael's deep-research CSVs (run once per research batch)
python src/trend_radar/scripts/ingest_deep_research.py
# → writes src/trend_radar/data/terms.json  (pass --terms <path> to collectors below)

# Collectors (M1 — all implemented)
python src/trend_radar/collectors/youtube.py        --terms src/trend_radar/data/terms.json
python src/trend_radar/collectors/google_trends.py  --terms src/trend_radar/data/terms.json
python src/trend_radar/collectors/twitter.py        --terms src/trend_radar/data/terms.json
python src/trend_radar/collectors/tiktok.py         --terms src/trend_radar/data/terms.json

# Aggregator (M2 — implemented)
python src/trend_radar/pipeline/aggregate.py
# → src/trend_radar/data/output/signal_DATE.json

# ETL (implemented) — run after every new collector batch
python src/trend_radar/pipeline/build_dataset.py
# → 5 CSV tables in src/trend_radar/data/processed/
```

Mock history at `src/trend_radar/data/mock/` — 6 months of simulated data (Nov 2025 → Apr 2026). Real data at `src/trend_radar/data/raw/`.

## Repo conventions

- All documentation and code comments in **English**
- One collector per platform in `src/trend_radar/collectors/`
- Raw outputs in `src/trend_radar/data/raw/`, ETL tables in `src/trend_radar/data/processed/`, M2 signals in `src/trend_radar/data/output/`
- Never commit `.env` — only `.env.example`
- No `Co-Authored-By` trailers in commit messages
- Work on `main` branch; user will create feature branches when needed
- **Use Mermaid diagrams** in `.md` files whenever a flow, sequence, dependency, or structure can be expressed visually — prefer `graph TD` for flows, `sequenceDiagram` for API interactions, `erDiagram` for schemas

## Two-track system

| Track | Window | Signal | Destination |
|-------|--------|--------|-------------|
| hyped | 90d | Sudden velocity spike | Today's Take / chat |
| emerging | 365d | Sustained growth | Foundational RAG |

Every hyped term has an `underlying_topic` → that's what lives in the UI.

## Input: terms.json schema

```json
{
  "id": "string",
  "social_trend_name": "Wolverine Stack",
  "underlying_topic": "Peptides",
  "everme_category": "Supplements",
  "related_terms": ["BPC-157 TB-500", "wolverine protocol peptides"]
}
```

No `trend_type` in input — M2/M3 classify hyped vs emerging from collected data.

## Environment variables

```
YOUTUBE_DATA_API_KEY=...          # YouTube Data API v3 ✓
TWITTER_BEARER_TOKEN=...          # Twitter/X API v2 Basic tier ✓
TWITTER_API_KEY=...               # Twitter/X ✓
TWITTER_API_SECRET=...            # Twitter/X ✓
TWITTER_ACCESS_TOKEN=...          # Twitter/X ✓
TWITTER_ACCESS_TOKEN_SECRET=...   # Twitter/X ✓
APIFY_API_TOKEN=...               # Apify — clockworks/tiktok-scraper ✓
REDDIT_CLIENT_ID=...              # Reddit OAuth app (not yet obtained)
REDDIT_CLIENT_SECRET=...          # Reddit OAuth app (not yet obtained)
ANTHROPIC_API_KEY=...             # Claude for M3 classifier (not yet added)
```

## Quota awareness

- YouTube Data API v3: **10,000 units/day** — `search.list` = 100 units/call. Script prints estimate before running.
- Twitter Basic tier: **500k tweet reads/month**, ~10 req/15min window. Script handles rate limit headers proactively.
- Google Trends: no quota — rate limiting by IP after consecutive runs. Use `--sleep 5` if blocked.
- TikTok (Apify): **$5/1000 results** — default 10 results/term = ~$0.60/run of 12 terms.

## Key files

| File | Purpose |
|------|---------|
| `src/trend_radar/data/mock/terms.json` | Mock input — edit to test different terms |
| `src/trend_radar/collectors/youtube.py` | YouTube validator — 90d + 365d windows (M1a) |
| `src/trend_radar/collectors/google_trends.py` | Google Trends validator — 90d + 365d (M1b) |
| `src/trend_radar/collectors/twitter.py` | Twitter/X validator — 7d window (M1c) |
| `src/trend_radar/collectors/tiktok.py` | TikTok validator — Apify clockworks actor (M1e) |
| `src/trend_radar/collectors/reddit.py` | Reddit validator (M1d, backlog — file not yet created) |
| `src/trend_radar/pipeline/aggregate.py` | Cross-platform aggregator — scores + classifies (M2) |
| `src/trend_radar/pipeline/build_dataset.py` | ETL — flattens all JSONs into CSV time-series tables |
| `src/trend_radar/pipeline/classify.py` | Hyped vs emerging LLM classifier (M3, backlog — file not yet created) |
| `src/trend_radar/scripts/ingest_deep_research.py` | Converts Rafael's deep-research CSVs → `terms.json` via GPT-4.1-nano |
| `src/trend_radar/scripts/generate_mock_history.py` | Generates 6 months of mock data across all sources |
| `src/trend_radar/data/terms.json` | Real terms input — output of `ingest_deep_research.py`, fed to collectors via `--terms` |
| `src/trend_radar/data/mock/` | Mock terms + 6-month simulated history (~66 JSON files) |
| `src/trend_radar/data/processed/` | ETL output — 5 CSV tables consumed by the dashboard |
| `src/trend_radar/data/output/` | M2 aggregator output — `signal_*.json` + `audit_*.json` |
| `docs/macro_plan.md` | Full pipeline plan |
| `docs/aggregation_plan.md` | M2 methodology, formulas, scoring, classification |
| `docs/etl_plan.md` | ETL pipeline — schema, cadence, data flow |
| `docs/youtube_plan.md` | YouTube validator spec |
| `docs/google_trends_plan.md` | Google Trends validator spec |
| `docs/twitter_plan.md` | Twitter/X validator spec |
| `docs/tiktok_instagram_plan.md` | TikTok/Instagram collector spec |
| `docs/reddit_plan.md` | Reddit validator spec |
