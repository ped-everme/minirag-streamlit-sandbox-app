# TODO — Query-level tracking for related_terms

## Problem

The pipeline currently treats each `terms.json` entry as the unit of analysis. `related_terms` are used as additional search queries only by YouTube and Twitter — Google Trends and TikTok ignore them entirely and only search by `social_trend_name`.

This means that after 6+ real runs, we will have historical time-series data for the primary term name only. Related terms will have no independent history, making it impossible to:

1. Detect when a related term is outpacing the primary name (e.g. "BPC-157" trending harder than "wolverine stack")
2. Merge or split terms based on observed score correlation over time
3. Identify which specific framing of a trend is resonating on which platform

## Why it matters

The long-term plan is to move from batch-relative scoring (current) to per-term historical normalisation. That requires enough historical data per query to establish each term's own baseline. If related terms are never tracked independently, that future upgrade only applies to `social_trend_name` entries — the related variants stay blind spots.

## Chosen solution — Option B: expand related_terms into first-class entries

Rather than modifying Google Trends and TikTok collectors to loop over related_terms (which adds API cost and code complexity), promote each related_term to a standalone `terms.json` entry with a `parent_term_id` field pointing to the primary term.

All four collectors already work without modification — they read `social_trend_name` from each entry. The M2 aggregator and signal output also require no changes.

### Schema addition

```json
[
  {
    "id": "peptide-stacking-protocol",
    "social_trend_name": "Peptide Stacking Protocol",
    "parent_term_id": null,
    ...
  },
  {
    "id": "bpc-157",
    "social_trend_name": "BPC-157",
    "parent_term_id": "peptide-stacking-protocol",
    "underlying_topic": "Synthetic Peptides",
    "everme_category": "Supplements",
    "related_terms": [],
    "hashtags": ["#BPC157"],
    "horizon": "3m",
    "source_rank_3m": 18,
    "source_rank_12m": null
  }
]
```

Child entries inherit `underlying_topic`, `everme_category`, and `horizon` from their parent. `related_terms` is empty for child entries to avoid exponential query expansion.

### Changes required

**`scripts/ingest_deep_research.py`**
After the two LLM passes, add a third step that expands each entry:
- Keep the original entry as the parent (`parent_term_id: null`)
- For each item in `related_terms`, create a child entry with:
  - `id`: slugified version of the term
  - `social_trend_name`: the related term string as-is
  - `parent_term_id`: parent's `id`
  - `related_terms`: `[]`
  - all other fields inherited from parent
- Remove `related_terms` from the parent entry (they are now child entries)

**`data/output/signal_DATE.json`** and **`data/output/audit_DATE.json`**
No schema change needed. Child entries appear as regular entries in the output. Consumers can filter by `parent_term_id is null` to get only the primary-term view, or use all entries for the full query-level analysis.

**M2 aggregator (`pipeline/aggregate.py`)**
No changes needed.

**Collectors**
No changes needed.

### When to do this

After the first real collection run completes successfully. The expanded `terms.json` will roughly triple the number of entries (26 primaries × avg 2 related = ~78 total). TikTok cost scales linearly (~$1.50/run instead of ~$0.60). Google Trends rate limits will require a longer sleep between terms (`--sleep` flag already exists).

### Future: correlation-based merge

After 6+ real runs, compute Spearman correlation between the score time-series of parent and child entries. Entries with correlation > 0.8 are likely the same underlying trend and can be merged retroactively. Entries that diverge (correlation < 0.4) may warrant promotion to independent primary terms in the next `ingest_deep_research.py` run.
