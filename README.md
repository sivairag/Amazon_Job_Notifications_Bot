# Amazon UK Jobs Telegram Bot

An automated job monitoring bot that continuously scrapes Amazon UK job listings and delivers real-time notifications to a Telegram channel. Built for job seekers targeting warehouse, sortation, and customer service roles across the United Kingdom.

---

## Overview

This bot monitors two Amazon job platforms simultaneously:

- **amazon.jobs** — via the official JSON API
- **jobsatamazon.co.uk** — via headless browser automation (Playwright)

When a new matching job is detected, a formatted notification is instantly sent to your configured Telegram channel or user, including the job title, location, type, category, posted date, and a direct application link.

---

## Features

- **Dual-source monitoring** — covers both Amazon job platforms in one bot
- **Smart deduplication** — SQLite database ensures each job is only notified once, never twice
- **Title filtering** — only alerts for specific allowed roles (no noise from unrelated jobs)
- **UK location filtering** — automatically filters out non-UK listings
- **Configurable intervals** — separate check intervals for the fast API and the slower Playwright scraper
- **Telegram HTML formatting** — clean, readable notifications with clickable apply links
- **Auto-restart** — designed to run as a systemd service with automatic crash recovery
- **Overlap protection** — built-in guard prevents concurrent check cycles

---

## Monitored Job Titles

The bot only sends alerts for the following roles:

- Warehouse Operative
- Sortation Operative
- Customer Service Associate
- Remote Customer Service Associate
- Amazon Fresh Warehouse

---

## How It Works

```
┌─────────────────────────────────────────────────────┐
│                   Bot Startup                       │
│         Loads config.json + initialises DB          │
└───────────────────┬─────────────────────────────────┘
                    │
                    ▼ every 60 seconds
┌─────────────────────────────────────────────────────┐
│           Source 1: amazon.jobs API                 │
│   HTTP request → filter by title + UK location      │
└───────────────────┬─────────────────────────────────┘
                    │
                    ▼ every 5 minutes
┌─────────────────────────────────────────────────────┐
│       Source 2: jobsatamazon.co.uk (Playwright)     │
│  Headless Chromium → intercept API → parse jobs     │
│  Fallback: DOM scraping if API interception fails   │
└───────────────────┬─────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────┐
│              Deduplication Check                    │
│     Is this job ID already in seen_jobs.db?         │
└──────────┬──────────────────────────┬───────────────┘
           │ New job                  │ Already seen
           ▼                          ▼
┌──────────────────────┐     ┌─────────────────────┐
│  Save to database    │     │       Skip          │
│  Send Telegram alert │     └─────────────────────┘
└──────────────────────┘
```

---

## Telegram Notification Format

```
New Amazon Job Alert
Warehouse Operative - Days

Location: Coventry, England
Type: Full-Time
Category: Fulfillment & Operations Management
Posted: May 25, 2026

Apply Here → [link]
Source: amazon.jobs
```

---

## Project Structure

```
amazon-jobs-bot/
├── bot.py              # Main bot — scraping, filtering, Telegram, scheduler
├── config.json         # Bot token, chat IDs, intervals, source settings
├── requirements.txt    # Python dependencies
├── seen_jobs.db        # SQLite database (auto-created on first run)
├── bot.log             # Log file (auto-created on first run)
└── README.md           # This file
```

---

## Configuration

Edit `config.json` to customise the bot:

```json
{
    "bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
    "chat_ids": [
        "YOUR_USER_ID",
        "@your_channel_name"
    ],
    "check_interval_seconds": 60,
    "playwright_interval_seconds": 300,
    "sources": {
        "amazon_jobs_api": true,
        "jobsatamazon_uk": true
    },
    "filters": {
        "keywords": [],
        "location": ""
    },
    "amazon_jobs_api": {
        "result_limit": 100,
        "country": "GBR",
        "warehouse_categories": [
            "Fulfillment & Operations Management",
            "Amazon Logistics, Transportation, & Shipment"
        ]
    }
}
```

| Field | Description |
|---|---|
| `bot_token` | Your Telegram bot token from @BotFather |
| `chat_ids` | List of Telegram user IDs or channel usernames to notify |
| `check_interval_seconds` | How often to check the amazon.jobs API (default: 60s) |
| `playwright_interval_seconds` | How often to run the Playwright scraper (default: 300s) |
| `sources` | Enable or disable each source independently |
| `filters.keywords` | Optional keywords to narrow API results |
| `filters.location` | Optional location filter for the API |

---

## Requirements

- Python 3.10+
- `playwright` — headless browser automation
- `requests` — HTTP client for the amazon.jobs API
- Chromium browser (installed via `playwright install chromium`)

Install dependencies:

```bash
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

---

## Running Locally

```bash
python bot.py
```

---

## Deploying to a Server (24/7)

### Recommended: Oracle Cloud Always Free VM

Oracle Cloud offers a permanently free Linux VM (Ubuntu 22.04, 1GB RAM) — sufficient to run this bot indefinitely at no cost.

**1. Upload files to the server:**
```bash
scp -i your-key.key -r ./amazon-jobs-bot ubuntu@YOUR_SERVER_IP:/opt/amazon-jobs-bot
```

**2. Install dependencies on the server:**
```bash
cd /opt/amazon-jobs-bot
pip3 install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

**3. Create a systemd service for auto-start and crash recovery:**

```bash
sudo nano /etc/systemd/system/amazon-jobs-bot.service
```

```ini
[Unit]
Description=Amazon Jobs Telegram Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/amazon-jobs-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**4. Enable and start the service:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable amazon-jobs-bot
sudo systemctl start amazon-jobs-bot
```

**5. Check status and view logs:**
```bash
sudo systemctl status amazon-jobs-bot
sudo journalctl -u amazon-jobs-bot -f
```

---

## Security Note

Keep your `config.json` private. It contains your Telegram bot token — do not commit it to a public repository or share it publicly.

---

## License

This project is for personal use. Not affiliated with or endorsed by Amazon.
