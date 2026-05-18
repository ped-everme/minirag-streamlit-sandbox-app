# TikTok + Instagram Validators — M1d + M1e

`collectors/tiktok_instagram.py` — backlog. Pending Apify account and actor selection.

---

## Role in Mini-RAG

TikTok and Instagram capture **viral reach and passive consumption** — content that spreads without users actively seeking it. A term trending on TikTok often precedes its appearance on YouTube, Reddit, or Google Search by weeks. These platforms are the earliest signal in the pipeline.

Instagram signals aesthetic adoption — when a health trend reaches Instagram, it has crossed from niche communities into lifestyle/mainstream audiences.

---

## Access

- **Platform:** Apify (cloud scraping infrastructure)
- **Env var:** `APIFY_API_TOKEN`
- **Cost:** pay-per-use (~$0.50–$1.50 per 1,000 results depending on actor)
- **Library:** `apify-client` (`pip install apify-client`)

### Before implementing — steps to do manually in Apify

1. Log in at apify.com
2. In Apify Store, search for a TikTok keyword/hashtag scraper and test it manually with one of the `what_users_say` queries
3. Note the exact actor slug (e.g. `clockworks/free-tiktok-scraper`)
4. In Apify Store, search for an Instagram hashtag scraper and test manually
5. Note the exact actor slug (e.g. `apify/instagram-hashtag-scraper`)
6. For each actor, inspect the output JSON and confirm which fields map to the metrics below
7. Note the estimated cost per 1,000 results shown on the actor page

---

## Collection flow

```mermaid
sequenceDiagram
    participant S as tiktok_instagram.py
    participant AC as Apify Client
    participant Actor as Apify Actor (cloud)

    loop for each term in terms.json
        loop for each what_users_say query
            S->>AC: actor(slug).call(run_input={query, limit})
            AC->>Actor: start run
            Actor-->>AC: run completed + dataset_id
            AC-->>S: dataset items (posts)
        end
        S->>S: deduplicate, aggregate per-term metrics
    end
    S->>S: write data/raw/tiktok_YYYY-MM-DD.json
    S->>S: write data/raw/instagram_YYYY-MM-DD.json
```

Apify runs are **async** — the client polls for completion. Typical run: 30–120 seconds per actor call.

---

## Output structure

### TikTok

```json
{
  "source": "tiktok",
  "collected_at": "2026-04-29T14:00:00Z",
  "term_count": 12,
  "terms": [
    {
      "term_id": "wolverine-stack",
      "social_trend": "Wolverine Stack",
      "underlying_topic": "Peptides",
      "window": "90d",
      "post_count": 312,
      "total_plays": 48200000,
      "avg_play_count": 154487,
      "top_play_count": 8200000,
      "avg_like_count": 12400,
      "avg_share_count": 3100,
      "avg_comment_count": 890,
      "posts": [
        {
          "post_id": "7312345678901234567",
          "description": "Tried the Wolverine Stack for 6 weeks 🐺 BPC-157 + TB-500 results",
          "author": "healthhacker_mike",
          "created_at": "2026-03-10T18:00:00Z",
          "play_count": 8200000,
          "like_count": 412000,
          "share_count": 89000,
          "comment_count": 14200,
          "hashtags": ["wolverinestack", "peptides", "BPC157", "biohacking"],
          "matched_query": "wolverine stack"
        }
      ]
    }
  ]
}
```

### Instagram

```json
{
  "source": "instagram",
  "collected_at": "2026-04-29T14:00:00Z",
  "term_count": 12,
  "terms": [
    {
      "term_id": "wolverine-stack",
      "social_trend": "Wolverine Stack",
      "window": "90d",
      "post_count": 847,
      "avg_like_count": 4200,
      "top_like_count": 182000,
      "avg_comment_count": 310,
      "posts": [
        {
          "post_id": "CxYz1234abcd",
          "caption": "Week 6 of the Wolverine Stack — here are my results...",
          "author": "biohacker_pedro",
          "timestamp": "2026-03-22T14:30:00Z",
          "like_count": 182000,
          "comment_count": 4800,
          "type": "reel",
          "hashtags": ["wolverinestack", "peptidetherapy", "longevity"],
          "matched_query": "wolverine stack"
        }
      ]
    }
  ]
}
```

---

## Metrics explained

### TikTok metrics

| Metric | What it means |
|--------|---------------|
| `post_count` | Volume of content — how many creators are posting about this term |
| `avg_play_count` | Average passive reach — how many people are seeing this content |
| `top_play_count` | Peak viral reach — identifies breakout content |
| `avg_like_count` | Active positive engagement |
| `avg_share_count` | Virality signal — shares drive content to new audiences. High shares = actively spreading |
| `avg_comment_count` | Conversation depth |

**TikTok-specific signal strength:**

`avg_share_count` is the most important metric for trend detection on TikTok. Content that gets shared spreads beyond the original creator's followers and enters the "For You Page" algorithm — that's how TikTok trends actually form.

```
engagement_rate = (avg_like_count + avg_comment_count + avg_share_count) / avg_play_count
```

High engagement rate (> 5%) = content is resonating, not just being scrolled past.

### Instagram metrics

| Metric | What it means |
|--------|---------------|
| `post_count` | Volume of content in the hashtag/search |
| `avg_like_count` | Community validation |
| `top_like_count` | Breakout content reach |
| `avg_comment_count` | Conversation — Instagram tends to have less comments than TikTok |
| `type` distribution | Mix of posts/reels/carousels — reels = higher reach |

### TikTok vs Instagram signal differences

| Signal | TikTok | Instagram |
|--------|--------|-----------|
| Speed | Fastest — days from post to viral | Slower — weeks to accumulate |
| Audience | Gen Z + Millennials, early adopters | Broader, more lifestyle-oriented |
| Content type | Experiment videos, challenges | Before/after, aesthetic, educational |
| When high signal matters | Term is just emerging | Term has entered mainstream lifestyle |

A term appearing strongly on TikTok but weakly on Instagram = **still emerging** — hasn't crossed into the broader lifestyle audience yet.

---

## Cost estimate

Before running in production, test with 1 term and `limit=20` to measure actual cost. Estimate:
- TikTok: ~$0.50–1.00 per term per run
- Instagram: ~$0.50–1.00 per term per run
- 12 terms × 2 platforms = ~$12–24 per full run

Log actual cost after first run and update this doc.

---

## Known limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Apify actors can break when platforms update | Run failures | Have backup actor slug ready; check Apify Store for alternatives |
| TikTok doesn't have a reliable date filter | May return older content | Filter by `created_at` post-fetch |
| Instagram requires hashtag as query, not free text | Miss content without hashtag | Include hashtag variants in `what_users_say` (e.g. `#wolverinestack`) |
| Pay-per-use cost | Costs per run | Test with `limit=20` before full runs |
| Private accounts not scraped | Incomplete dataset | Acceptable — public content is the trend signal |
