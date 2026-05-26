#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║  TAZZU BOT — Maldives News → Telegram                       ║
║  Scrapes 7 sites, sends new articles. Zero duplicates.      ║
╚══════════════════════════════════════════════════════════════╝
Built on top of the Maldives News Scraper v4.
Runs every 30 min via GitHub Actions (free tier).

ENV VARS required:
  TELEGRAM_TOKEN    — from @BotFather
  TELEGRAM_CHAT_ID  — your personal or group chat ID
"""

import html as _html
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse, quote

import feedparser
import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SEEN_FILE        = Path("seen_articles.json")
MAX_SEEN         = 3000   # rolling cap — oldest keys dropped when exceeded
FETCH_DAYS       = 2      # look-back window when scraping (catches gaps)
ARTICLES_PER_SITE = 15    # max articles fetched per site per run

SITE_EMOJI = {
    "maldivesindependent.com": "🌊",
    "edition.mv":              "📰",
    "english.sun.mv":          "☀️",
    "raajje.mv":               "🏝️",
    "psmnews.mv":              "📡",
    "avas.mv":                 "🔵",
    "cnm.mv":                  "📺",
}

# ─────────────────────────────────────────────────────────────────────────────
# SITES REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

SITES = {
    "maldivesindependent.com": {
        "label":        "Maldives Independent",
        "method":       "gnews",
        "gnews_query":  "site:maldivesindependent.com",
    },
    "edition.mv": {
        "label":        "The Edition",
        "method":       "html",
        "home_url":     "https://edition.mv",
        "url_filter":   "edition",
        "gnews_query":  "site:edition.mv",
    },
    "english.sun.mv": {
        "label":        "Sun Online (EN)",
        "method":       "gnews",
        "gnews_query":  "site:english.sun.mv",
    },
    "raajje.mv": {
        "label":        "Raajje MV",
        "method":       "gnews",
        "gnews_query":  "site:raajje.mv english",
    },
    "psmnews.mv": {
        "label":        "PSM News",
        "method":       "rss",
        "rss_url":      "https://psmnews.mv/en/feed",
        "gnews_query":  "site:psmnews.mv",
    },
    "avas.mv": {
        "label":        "Avas",
        "method":       "html",
        "home_url":     "https://avas.mv/en",
        "url_filter":   "avas",
        "gnews_query":  "site:avas.mv english",
    },
    "cnm.mv": {
        "label":        "Channel News Maldives",
        "method":       "html",
        "home_url":     "https://cnm.mv",
        "url_filter":   "cnm",
        "gnews_query":  "site:cnm.mv english",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control":   "no-cache",
    "Referer":         "https://www.google.com/",
}

TIMEOUT = 15

# ─────────────────────────────────────────────────────────────────────────────
# TEXT UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def clean_title(raw: str) -> str:
    import html
    t = html.unescape(raw or "")
    return re.sub(r"\s+", " ", t).strip()

def has_thaana(text: str) -> bool:
    """Detect Dhivehi (Thaana) script — skip non-English articles."""
    return any("\u0780" <= c <= "\u07BF" for c in text)

def is_nav_title(title: str) -> bool:
    """Detect concatenated navigation text like 'NewsBusinessSportsFeaturesWorld'."""
    if len(title) < 20:
        return False
    return len(title.split()) <= 2

def is_valid_title(title: str) -> bool:
    return (
        bool(title)
        and len(title) >= 8
        and not is_nav_title(title)
        and not has_thaana(title)
    )

def escape_html(text: str) -> str:
    return _html.escape(str(text), quote=False)

# ─────────────────────────────────────────────────────────────────────────────
# URL FILTERS (per-site article URL detection)
# ─────────────────────────────────────────────────────────────────────────────

SKIP_WORDS = [
    "/tag/", "/category/", "/page/", "/author/", "/search",
    "/about", "/contact", "/login", "/register", "/info/",
    "/terms", "/privacy", "/policy", "/ethics", "?", "#",
]

def _ok_edition(path: str) -> bool:
    if any(s in path for s in SKIP_WORDS): return False
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2: return False
    slug = parts[-1]
    return bool(re.fullmatch(r"\d+", slug)) or bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{4,}", slug))

def _ok_avas(path: str) -> bool:
    if any(s in path for s in SKIP_WORDS): return False
    parts = [p for p in path.strip("/").split("/") if p]
    return len(parts) >= 2 and bool(re.fullmatch(r"\d+", parts[-1]))

def _ok_cnm(path: str) -> bool:
    if any(s in path for s in SKIP_WORDS): return False
    parts = [p for p in path.strip("/").split("/") if p]
    return len(parts) >= 2 and bool(re.fullmatch(r"\d+", parts[-1]))

URL_FILTERS = {
    "edition": _ok_edition,
    "avas":    _ok_avas,
    "cnm":     _ok_cnm,
}

# ─────────────────────────────────────────────────────────────────────────────
# DATE UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def parse_date(s: str):
    if not s: return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def is_recent(date_str: str, since) -> bool:
    if since is None: return True
    d = parse_date(date_str)
    if d is None: return True  # unknown date → include
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d >= since

def format_relative_time(date_str: str) -> str:
    """Return human-friendly relative time string."""
    d = parse_date(date_str)
    if not d: return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - d
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        mins = int(delta.total_seconds() // 60)
        return f"🕐 {mins}m ago" if mins > 0 else "🕐 Just now"
    if hours < 24:
        return f"🕐 {hours}h ago"
    return f"🕐 {d.strftime('%d %b %Y')}"

# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE NEWS URL RESOLVER
# ─────────────────────────────────────────────────────────────────────────────

def resolve_gn_url(gn_url: str, session: requests.Session) -> str:
    """Unwrap Google News redirect URL → real article URL."""
    if "news.google.com" not in gn_url:
        return gn_url

    art_url = gn_url.split("?")[0].replace("/rss/articles/", "/articles/")

    try:
        r = session.head(gn_url, headers=HEADERS, timeout=8, allow_redirects=False)
        loc = r.headers.get("location", "")
        if loc and "google.com" not in loc and loc.startswith("http"):
            return loc
    except Exception:
        pass

    try:
        r = session.get(art_url, headers=HEADERS, timeout=10, allow_redirects=True)
        if "google.com" not in r.url and r.url.startswith("http"):
            return r.url
        if r.status_code == 200:
            soup = BeautifulSoup(r.text[:4000], "html.parser")
            for tag, attr in [
                ({"property": "og:url"}, "content"),
                ({"name": "twitter:url"}, "content"),
            ]:
                el = soup.find("meta", tag)
                if el and el.get(attr) and "google.com" not in el[attr]:
                    return el[attr]
            canonical = soup.find("link", rel="canonical")
            if canonical and canonical.get("href") and "google.com" not in canonical["href"]:
                return canonical["href"]
    except Exception:
        pass

    return gn_url  # still clickable

# ─────────────────────────────────────────────────────────────────────────────
# FETCH METHODS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_via_gnews(cfg: dict, limit: int, since, session: requests.Session) -> list:
    """
    Fetch articles from Google News RSS directly via feedparser.
    Replaces the broken 'gnews' PyPI library which stopped working
    after Google changed their News RSS structure.
    """
    query   = cfg.get("gnews_query", "")
    encoded = quote(query)
    rss_url = (
        f"https://news.google.com/rss/search"
        f"?q={encoded}&hl=en-MV&gl=MV&ceid=MV:en&num=30"
    )

    try:
        r = session.get(rss_url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"     [gnews RSS] HTTP {r.status_code} for query: {query}")
            return []
        feed = feedparser.parse(r.text)
    except Exception as e:
        print(f"     [gnews RSS error] {e}")
        return []

    results, seen_t = [], set()

    for entry in feed.entries:
        title   = clean_title(entry.get("title", ""))
        raw_url = entry.get("link", "")
        date    = entry.get("published", entry.get("updated", ""))

        if not is_valid_title(title):         continue
        if title.lower() in seen_t:           continue
        if since and not is_recent(date, since): continue

        real_url = resolve_gn_url(raw_url, session)
        time.sleep(0.25)

        seen_t.add(title.lower())
        results.append({
            "method":  "gnews",
            "title":   title,
            "url":     real_url,
            "date":    date,
            "excerpt": clean_title(
                BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(strip=True)
            )[:300],
        })
        if len(results) >= limit:
            break

    return results


def fetch_via_rss(cfg: dict, limit: int, since, session: requests.Session) -> list:
    try:
        r = session.get(cfg["rss_url"], headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200: return []
        feed = feedparser.parse(r.text)
    except Exception as e:
        print(f"     [RSS error] {e}")
        return []

    results = []
    for e in feed.entries:
        date = e.get("published", e.get("updated", ""))
        if since and not is_recent(date, since): continue
        title = clean_title(e.get("title", ""))
        if not is_valid_title(title): continue
        results.append({
            "method":  "rss",
            "title":   title,
            "url":     e.get("link", ""),
            "date":    date,
            "excerpt": BeautifulSoup(
                e.get("summary", ""), "lxml"
            ).get_text(strip=True)[:300],
        })
        if len(results) >= limit:
            break
    return results


def fetch_via_html(cfg: dict, limit: int, since, session: requests.Session) -> list:
    try:
        r = session.get(cfg["home_url"], headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"     [HTML] HTTP {r.status_code}")
            return []
    except Exception as e:
        print(f"     [HTML error] {e}")
        return []

    soup   = BeautifulSoup(r.text, "lxml")
    domain = cfg["home_url"].split("/")[2]
    base   = "https://" + domain
    url_ok = URL_FILTERS.get(cfg.get("url_filter", ""), lambda p: True)

    seen_urls, seen_titles, results = set(), set(), []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("//"): href = "https:" + href
        elif href.startswith("/"): href = base + href
        elif not href.startswith("http"): continue

        if domain not in href: continue
        path = urlparse(href).path
        if not url_ok(path): continue
        if href in seen_urls: continue

        title = ""
        for el in [a] + list(a.parents)[:3]:
            for heading in el.find_all(["h1", "h2", "h3", "h4"], limit=1):
                candidate = clean_title(heading.get_text())
                if is_valid_title(candidate):
                    title = candidate
                    break
            if title: break

        if not title:
            title = clean_title(a.get_text())
        if not is_valid_title(title): continue
        if title.lower() in seen_titles: continue

        seen_urls.add(href)
        seen_titles.add(title.lower())
        results.append({
            "method":  "html",
            "title":   title,
            "url":     href,
            "date":    "",
            "excerpt": "",
        })
        if len(results) >= limit:
            break

    return results


def scrape_site(site_key: str, cfg: dict, limit: int, since, session: requests.Session) -> list:
    method = cfg["method"]
    posts  = []

    if method == "gnews":
        posts = fetch_via_gnews(cfg, limit, since, session)
        if not posts and cfg.get("home_url"):
            posts = fetch_via_html(cfg, limit, since, session)

    elif method == "rss":
        posts = fetch_via_rss(cfg, limit, since, session)
        if not posts:
            posts = fetch_via_gnews(cfg, limit, since, session)

    elif method == "html":
        posts = fetch_via_html(cfg, limit, since, session)
        if not posts:
            posts = fetch_via_gnews(cfg, limit, since, session)

    icon = "✓" if posts else "✗"
    print(f"   {icon} {cfg['label']:<30} {len(posts)} articles")
    return [{"source": site_key, **p} for p in posts]

# ─────────────────────────────────────────────────────────────────────────────
# DEDUPLICATION (seen_articles.json)
# ─────────────────────────────────────────────────────────────────────────────

def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text("utf-8"))
            return set(data.get("keys", []))
        except Exception:
            return set()
    return set()

def save_seen(seen: set):
    keys = list(seen)
    if len(keys) > MAX_SEEN:
        keys = keys[-MAX_SEEN:]  # keep newest entries
    SEEN_FILE.write_text(
        json.dumps(
            {"keys": keys, "count": len(keys), "updated": datetime.now(timezone.utc).isoformat()},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

def make_key(article: dict) -> str:
    """
    Stable, collision-resistant deduplication key.
    Prefers real URL. Falls back to source::title if URL is a Google redirect.
    """
    url = article.get("url", "").strip().rstrip("/").lower()
    if url and "news.google.com" not in url and len(url) > 15:
        return url
    title = re.sub(r"\s+", " ", article.get("title", "")).strip().lower()
    return f"{article['source']}::{title}"

# ─────────────────────────────────────────────────────────────────────────────
# ARTICLE TEXT FETCHER
# ─────────────────────────────────────────────────────────────────────────────

_STRIP_TAGS = {"script", "style", "nav", "header", "footer", "aside",
               "form", "noscript", "figure", "figcaption", "button"}

def fetch_article_text(url: str, session: requests.Session, max_chars: int = 4000) -> str:
    if not url or "news.google.com" in url:
        return ""
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(_STRIP_TAGS):
            tag.decompose()
        container = (
            soup.find("article")
            or soup.find("main")
            or soup.find("div", class_=re.compile(r"(article|content|post|entry|story)", re.I))
            or soup.body
        )
        if not container:
            return ""
        paragraphs = container.find_all("p")
        text = " ".join(
            p.get_text(" ", strip=True) for p in paragraphs
            if len(p.get_text(strip=True)) > 40
        )
        if len(text) < 200:
            text = container.get_text(" ", strip=True)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        print(f"     [fetch_article_text] {e}")
        return ""

# ─────────────────────────────────────────────────────────────────────────────
# CLAUDE AI SUMMARIZER
# ─────────────────────────────────────────────────────────────────────────────

_SUMMARIZE_SYSTEM = """\
You are a strict factual news summarizer for a Telegram news bot.

RULES — follow every one, no exceptions:
1. Summarize using ONLY the information present in the article text provided.
2. Do NOT add background knowledge, context, opinions, or any information not \
explicitly stated in the article.
3. Do NOT speculate, infer, or guess at anything beyond what the article states.
4. Write exactly 2 sentences in plain English, present tense.
5. Maximum 55 words total across both sentences.
6. If the article text is too short or unclear, write only what can be \
confirmed — even if that is just one sentence.
7. Never start with "The article says" or "According to". Just state the facts.
"""

def get_ai_summary(title: str, article_text: str, source_label: str) -> str:
    if not ANTHROPIC_API_KEY:
        return ""

    if article_text:
        user_content = (
            f"Source: {source_label}\n"
            f"Headline: {title}\n\n"
            f"Article text:\n{article_text}"
        )
    else:
        user_content = (
            f"Source: {source_label}\n"
            f"Headline: {title}\n\n"
            "No article body text is available. Write a single sentence that "
            "restates the headline as a factual statement. Do not add any details."
        )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":          ANTHROPIC_API_KEY,
                "anthropic-version":  "2023-06-01",
                "content-type":       "application/json",
            },
            json={
                "model":      "claude-sonnet-4-20250514",
                "max_tokens": 150,
                "system":     _SUMMARIZE_SYSTEM,
                "messages":   [{"role": "user", "content": user_content}],
            },
            timeout=20,
        )
        if resp.ok:
            data    = resp.json()
            summary = data["content"][0]["text"].strip()
            return summary
        else:
            print(f"     [Claude API ✗] {resp.status_code}: {resp.text[:200]}")
            return ""
    except Exception as e:
        print(f"     [Claude API ✗] {e}")
        return ""

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────────────────────────────────────────

def send_article(article: dict, ai_summary: str = "") -> bool:
    label    = SITES[article["source"]]["label"]
    emoji    = SITE_EMOJI.get(article["source"], "📰")
    title    = escape_html(article["title"])
    url      = article["url"]
    time_str = format_relative_time(article.get("date", ""))

    body     = ai_summary or (article.get("excerpt") or "")[:220].strip()
    body_html = escape_html(body)

    lines = [
        f"{emoji}  <b>{escape_html(label)}</b>",
        "",
        f"<b>{title}</b>",
    ]
    if body_html:
        lines += ["", body_html]
    if time_str:
        lines += ["", f"<i>{time_str}</i>"]

    keyboard = {
        "inline_keyboard": [[
            {"text": "📖  Read Article", "url": url}
        ]]
    }

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":                  TELEGRAM_CHAT_ID,
                "text":                     "\n".join(lines),
                "parse_mode":               "HTML",
                "reply_markup":             json.dumps(keyboard),
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if not resp.ok:
            print(f"     [Telegram ✗] {resp.status_code} — {resp.text[:200]}")
        return resp.ok
    except Exception as e:
        print(f"     [Telegram ✗] {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise SystemExit(
            "❌ Missing env vars.\n"
            "   Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID before running."
        )

    if not ANTHROPIC_API_KEY:
        print("⚠️  ANTHROPIC_API_KEY not set — AI summaries disabled, will use raw excerpts.")

    print(f"\n{'═' * 60}")
    print(f"  🤖 TAZZU BOT — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═' * 60}")

    seen  = load_seen()
    since = datetime.now(timezone.utc) - timedelta(days=FETCH_DAYS)

    session = requests.Session()

    # ── Scrape all sites ──────────────────────────────────────
    print(f"\n  Scraping (last {FETCH_DAYS} days)...\n")
    all_articles: list = []

    for site_key, cfg in SITES.items():
        try:
            arts = scrape_site(site_key, cfg, ARTICLES_PER_SITE, since, session)
            all_articles.extend(arts)
        except Exception as e:
            print(f"   ✗ {cfg['label']:<30} ERROR: {e}")
        time.sleep(1.2)

    # ── Filter out already-seen articles ──────────────────────
    new_articles = [a for a in all_articles if make_key(a) not in seen]

    print(f"\n  Scraped : {len(all_articles)} total")
    print(f"  Seen    : {len(seen)} already notified")
    print(f"  New     : {len(new_articles)} to send")

    if not new_articles:
        print("\n  ✓ Nothing new. All caught up!\n")
        save_seen(seen)
        return

    # ── Summarise & Send to Telegram ──────────────────────────
    use_ai = bool(ANTHROPIC_API_KEY)
    if use_ai:
        print(f"\n  🤖 AI summaries enabled (Claude)")
    else:
        print(f"\n  ℹ️  No ANTHROPIC_API_KEY — using raw excerpts")
    print()

    sent, failed = 0, 0

    for art in new_articles:
        key = make_key(art)
        src = SITES[art["source"]]["label"]
        print(f"  → [{src}] {art['title'][:55]}…")

        ai_summary = ""
        if use_ai:
            article_text = fetch_article_text(art["url"], session)
            if article_text:
                print(f"     ✓ Article text: {len(article_text)} chars")
            else:
                print(f"     ℹ️  No article text — summarising from headline")

            ai_summary = get_ai_summary(art["title"], article_text, src)
            if ai_summary:
                print(f"     ✓ Summary: {ai_summary[:60]}…")
            else:
                print(f"     ℹ️  Summary failed — falling back to excerpt")

            time.sleep(0.3)

        if send_article(art, ai_summary=ai_summary):
            seen.add(key)
            sent += 1
        else:
            failed += 1

        time.sleep(0.7)  # gentle Telegram rate limit

    save_seen(seen)

    print(f"\n{'═' * 60}")
    print(f"  ✓ Sent {sent}  |  ✗ Failed {failed}  |  Total seen: {len(seen)}")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
