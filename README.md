# 🤖 Tazzu Bot — Maldives News → Telegram

Tazzu is an automated Telegram bot that monitors 7 Maldives English-language news sites and sends you a clean summary notification the moment a new article is published. **No duplicates. No raw links. Just a button.**

---

## What You Get in Telegram

```
☀️  Sun Online (EN)

New tourism tax framework approved by parliament

Parliament approved a revised tourism tax structure
that will take effect from July, affecting all resort
properties with more than 50 beds...

🕐 2h ago

[ 📖  Read Article ]
```

---

## Sites Monitored

| Site | Source |
|------|--------|
| 🌊 Maldives Independent | maldivesindependent.com |
| 📰 The Edition | edition.mv |
| ☀️ Sun Online (EN) | english.sun.mv |
| 🏝️ Raajje MV | raajje.mv |
| 📡 PSM News | psmnews.mv |
| 🔵 Avas | avas.mv |
| 📺 Channel News Maldives | cnm.mv |

---

## Setup Guide

### Step 1 — Create Your Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name, e.g. `Tazzu News`
4. Choose a username, e.g. `@TazzuMvBot`
5. Copy the **bot token** (looks like `7123456789:ABCdef...`)

### Step 2 — Get Your Telegram Chat ID

1. Start a conversation with your new bot (press Start)
2. Send it any message
3. Visit this URL in your browser (replace `YOUR_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
4. Find `"chat":{"id": 123456789}` — that number is your **Chat ID**

> 💡 **Group chat?** Add the bot to your group, send a message, then use the same URL. Group IDs are negative numbers like `-123456789`.

### Step 3 — Set Up GitHub Repository

1. Create a new **public** repository on GitHub (public = unlimited free Actions minutes)
2. Upload all these files to the root of the repo:
   ```
   tazzu_bot.py
   requirements.txt
   seen_articles.json
   .github/workflows/tazzu.yml
   ```

### Step 4 — Add Secrets

In your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these two secrets:

| Secret Name | Value |
|---|---|
| `TELEGRAM_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your chat ID from Step 2 |

### Step 5 — Test It

1. Go to your repo → **Actions** tab
2. Click **"🤖 Tazzu Bot — Maldives News"**
3. Click **"Run workflow"** → **"Run workflow"**
4. Watch the logs — within ~2 minutes you should receive your first Telegram messages!

---

## Schedule

The bot runs **every 30 minutes** automatically via GitHub Actions.

To change the frequency, edit `.github/workflows/tazzu.yml`:
```yaml
- cron: '*/30 * * * *'   # every 30 min  ← default
- cron: '*/15 * * * *'   # every 15 min  (uses more free minutes)
- cron: '0 * * * *'      # every hour    (most conservative)
```

---

## How Duplicates Are Prevented

Every article URL (or `source::title` if URL is unavailable) is stored in `seen_articles.json` after it's sent. On each run, new articles are compared against this list before sending. The file is automatically committed back to the repo after each run.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No messages received | Check secrets are correct in GitHub Settings |
| Bot doesn't respond | Make sure you sent `/start` to the bot in Telegram |
| Actions not running | Make sure the repo has Actions enabled (Settings → Actions) |
| "gnews not installed" error | Verify `requirements.txt` is in the repo root |
| Chat ID not found | Send the bot a message first, then check `/getUpdates` |

---

## Files

```
tazzu_bot.py          ← main bot logic + scraper
requirements.txt      ← Python dependencies
seen_articles.json    ← auto-updated dedup store
.github/
  workflows/
    tazzu.yml         ← GitHub Actions schedule
```
