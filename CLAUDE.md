# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

Tazzu is a headless Python bot that scrapes 7 Maldivian English-language news sites every 30 minutes (via GitHub Actions), deduplicates articles across runs, generates AI summaries with Claude, and sends formatted messages to a Telegram chat.

## Running the Bot

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot (requires env vars below)
python tazzu_bot.py
```

**Required env vars:**
- `TELEGRAM_TOKEN` — bot token from @BotFather
- `TELEGRAM_CHAT_ID` — recipient chat ID (negative for group chats)

**Optional env var:**
- `ANTHROPIC_API_KEY` — enables Claude AI summaries; without it the bot falls back to raw article excerpts (still fully functional)

There are no tests, no linter config, and no build step — this is a single-file Python script.

## Architecture

### Single-file monolith (`tazzu_bot.py`, ~705 lines)

The file is divided into clearly labelled sections with `# ──` banners:

| Section | Lines | Purpose |
|---|---|---|
| CONFIG | 30–114 | Env vars, `SITES` registry, `SITE_EMOJI`, `HEADERS`, constants |
| TEXT UTILITIES | 116–144 | HTML cleaning, Thaana script detection, title validation |
| URL FILTERING | 146–176 | Per-site article URL validators, skip-list patterns |
| DATE PARSING | 178–215 | RFC 5322 / ISO 8601 parsing, relative time formatting |
| GOOGLE NEWS RESOLVER | 217–254 | Unwraps `news.google.com` redirects to real article URLs |
| FETCH METHODS | 257–420 | `fetch_via_gnews`, `fetch_via_rss`, `fetch_via_html`, `scrape_site` orchestrator |
| DEDUPLICATION | 422–457 | `load_seen` / `save_seen` (JSON), `make_key` |
| ARTICLE TEXT FETCHER | 460–495 | Fetches full body text, strips nav/ads, max 4000 chars |
| CLAUDE AI SUMMARIZER | 498–559 | Calls Anthropic API directly via `requests`, 2-sentence / 55-word summaries |
| TELEGRAM SENDER | 562–608 | Formats HTML message, sends inline keyboard button |
| MAIN | 614–701 | Orchestrates: scrape → dedup → summarise → send → save seen |

### Data flow per run

```
load seen_articles.json
    ↓
scrape all 7 SITES (1.2s sleep between each)
    ↓
filter out already-seen articles (URL or source::title key)
    ↓
for each new article:
    fetch full article text  →  get_ai_summary (Claude API)  →  send_article (Telegram)
    mark as seen
    ↓
save_seen → seen_articles.json → git auto-commit [skip ci]
```

### State management

`seen_articles.json` is the only persistent state. It is committed back to the repo by GitHub Actions after every run (see `.github/workflows/tazzu.yml`). The file stores a rolling list of up to `MAX_SEEN = 3000` dedup keys. Keys are article URLs when real; `source::title` when Google News redirects can't be resolved.

### Site registry (`SITES` dict)

Each entry in `SITES` specifies:
- `label` — display name used in Telegram messages
- `method` — primary scrape method: `"gnews"`, `"rss"`, or `"html"`
- `gnews_query` — Google News RSS query string (used by all sites as fallback)
- `rss_url` — only for `method: "rss"` sites (e.g. PSM News)
- `home_url` / `url_filter` — only for `method: "html"` sites (e.g. Edition.mv, Avas.mv, CNM.mv)

`SITE_EMOJI` is a parallel dict mapping site keys to emoji shown in Telegram.

### Scraping fallback chain

`scrape_site()` tries the configured `method` first. HTML-method sites fall back to `fetch_via_gnews` if HTML scraping returns nothing. All methods return a list of article dicts with keys: `source`, `title`, `url`, `date`, `excerpt`, `method`.

### Resilience patterns

- All network calls have `timeout=15` (20 for Claude API)
- Every `except Exception` is caught and logged rather than crashing
- AI summaries are optional — failure returns `""` and the bot falls back to the raw excerpt
- Telegram send failures don't add the article to `seen`, so it retries on the next run

## Adding a New News Site

1. Add an entry to `SITES` with the appropriate `method`, `label`, and `gnews_query`.
2. Add the site key to `SITE_EMOJI`.
3. If using `method: "html"`, add URL filter logic in the `URL FILTERING` section (`is_article_url()`).

## CI/CD

`.github/workflows/tazzu.yml` runs on a `*/30 * * * *` cron and on `workflow_dispatch`. It requires `permissions: contents: write` to commit `seen_articles.json` back after each run. The commit uses `[skip ci]` in the message to prevent infinite loops.

GitHub secrets required in the repo: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, and optionally `ANTHROPIC_API_KEY`.
