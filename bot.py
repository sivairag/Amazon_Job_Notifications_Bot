#!/usr/bin/env python3
"""Amazon UK Jobs Telegram Bot.

Monitors jobsatamazon.co.uk and amazon.jobs for new UK warehouse and
hourly operations job postings, then sends Telegram notifications.
"""

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.async_api import async_playwright

CONFIG_FILE = Path(__file__).parent / "config.json"
DB_FILE = Path(__file__).parent / "seen_jobs.db"
LOG_FILE = Path(__file__).parent / "bot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Amazon.jobs API categories that cover the allowed roles.
WAREHOUSE_API_CATEGORIES = [
    "Fulfillment & Operations Management",
    "Amazon Logistics, Transportation, & Shipment",
    "Customer Service",
]

# Only these specific job titles will trigger an alert.
# Matching is case-insensitive substring, so shift variants like
# "Warehouse Operative - Nights" are still caught by "warehouse operative".
ALLOWED_JOB_TITLES = [
    "warehouse operative",
    "sortation operative",
    "customer service associate",
    "remote customer service associate",
    "amazon fresh warehouse",
]

# UK country identifiers and major cities used to verify job location.
UK_LOCATION_TERMS = {
    "uk", "united kingdom", "england", "scotland", "wales", "northern ireland",
    "great britain", "gb", "gbr",
    "london", "manchester", "birmingham", "leeds", "glasgow", "edinburgh",
    "liverpool", "bristol", "sheffield", "coventry", "belfast", "newcastle",
    "nottingham", "leicester", "brighton", "hull", "plymouth", "stoke",
    "wolverhampton", "derby", "swansea", "cardiff", "aberdeen", "cambridge",
    "oxford", "reading", "luton", "milton keynes", "portsmouth", "southampton",
    "exeter", "norwich", "peterborough", "york", "dundee", "bolton",
    "stockport", "wigan", "rochdale", "oldham", "salford", "doncaster",
    "barnsley", "rotherham", "wakefield", "bradford", "swindon", "bournemouth",
    "middlesbrough", "basildon", "chelmsford", "colchester", "ipswich",
    "guildford", "slough", "watford", "stevenage", "rugby", "nuneaton",
    "tamworth", "telford", "chester", "warrington", "blackpool", "burnley",
    "blackburn", "preston", "carlisle", "crawley", "eastbourne", "hastings",
    "worthing", "bath", "gloucester", "hereford", "worcester", "lincoln",
    "grimsby", "scunthorpe", "huddersfield", "halifax", "bury", "stockton",
    "hartlepool", "darlington", "gateshead", "sunderland", "durham",
    "inverness", "perth", "stirling", "falkirk", "paisley", "hamilton",
    "east kilbride", "livingston", "kirkcaldy", "dumfries", "londonderry",
}


def is_allowed_job_title(job: dict) -> bool:
    """Return True only if the job title matches one of the five allowed roles."""
    title = job.get("title", "").lower()
    return any(pattern in title for pattern in ALLOWED_JOB_TITLES)


def is_uk_location(job: dict) -> bool:
    """Return True if the job location is in the United Kingdom.

    The amazon.jobs API is already called with country[]=GBR, so most results
    are UK. This acts as a second guard against virtual/remote roles with no
    explicit country, cross-listed international positions, or malformed data.
    If location is blank we let it through (API already scoped to GBR).
    """
    location = job.get("location", "").lower().strip()
    if not location:
        return True  # trust the API's country filter
    return any(term in location for term in UK_LOCATION_TERMS)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_jobs (
            job_id    TEXT PRIMARY KEY,
            title     TEXT,
            location  TEXT,
            source    TEXT,
            first_seen TEXT
        )
    """)
    conn.commit()
    return conn


def is_new_job(conn: sqlite3.Connection, job_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    return row is None


def mark_seen(conn: sqlite3.Connection, job: dict) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen_jobs (job_id, title, location, source, first_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        (job["id"], job["title"], job["location"], job["source"],
         datetime.utcnow().isoformat()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_message(config: dict, text: str) -> None:
    token = config["bot_token"]
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chat_id in config["chat_ids"]:
        try:
            resp = requests.post(
                api_url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=15,
            )
            if not resp.ok:
                log.error("Telegram error for %s: %s", chat_id, resp.text)
        except Exception as exc:
            log.error("Telegram send failed (%s): %s", chat_id, exc)


def format_job(job: dict) -> str:
    source_label = {
        "amazon.jobs": "amazon.jobs",
        "jobsatamazon.co.uk": "jobsatamazon.co.uk",
    }.get(job["source"], job["source"])

    parts = [f"<b>New Amazon Job Alert</b>", f"<b>{job['title']}</b>"]
    if job.get("location"):
        parts.append(f"Location: {job['location']}")
    if job.get("job_type"):
        parts.append(f"Type: {job['job_type']}")
    if job.get("category"):
        parts.append(f"Category: {job['category']}")
    if job.get("posted_date"):
        parts.append(f"Posted: {job['posted_date']}")
    if job.get("url"):
        parts.append(f'<a href="{job["url"]}">Apply Here</a>')
    parts.append(f"Source: {source_label}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Source 1: amazon.jobs JSON API
# ---------------------------------------------------------------------------

def fetch_amazon_jobs_api(config: dict) -> list[dict]:
    cfg = config.get("amazon_jobs_api", {})
    filters = config.get("filters", {})

    # Build params as a list of tuples so we can repeat keys (e.g. category[]).
    # We request only warehouse / operations categories at the API level so the
    # response is already scoped — no need to fetch thousands of unrelated roles.
    categories = cfg.get("warehouse_categories", WAREHOUSE_API_CATEGORIES)
    params: list[tuple] = [
        ("country[]", cfg.get("country", "GBR")),
        ("result_limit", cfg.get("result_limit", 100)),
        ("offset", 0),
        ("sort", "recent"),
    ]
    for cat in categories:
        params.append(("category[]", cat))

    if filters.get("keywords"):
        params.append(("base_query", " ".join(filters["keywords"])))
    if filters.get("location"):
        params.append(("loc_query", filters["location"]))

    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json",
        "Referer": "https://www.amazon.jobs/",
    }
    resp = requests.get(
        "https://www.amazon.jobs/en/search.json",
        params=params,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for j in data.get("jobs", []):
        job_path = j.get("job_path", "")
        jobs.append({
            "id": f"amzjobs_{j.get('id', j.get('id_icims', ''))}",
            "title": j.get("title", "").strip(),
            "location": j.get("location", "").strip(),
            "job_type": j.get("job_schedule_type", "").strip(),
            "category": j.get("job_category", "").strip(),
            "posted_date": j.get("posted_date", "").strip(),
            "url": f"https://www.amazon.jobs{job_path}" if job_path else "",
            "source": "amazon.jobs",
        })
    return jobs


# ---------------------------------------------------------------------------
# Source 2: jobsatamazon.co.uk via Playwright (intercepts the internal API)
# ---------------------------------------------------------------------------

async def fetch_jobsatamazon_uk(config: dict) -> list[dict]:
    """Load jobsatamazon.co.uk in a headless browser and intercept the API
    response so we get structured data without brittle CSS selectors."""
    captured: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=BROWSER_UA)
        page = await context.new_page()

        async def on_response(response):
            url = response.url
            # Capture any JSON response that looks like a job listing API
            if response.status == 200 and (
                "/api/jobs" in url
                or "jobs" in url and "json" in response.headers.get("content-type", "")
            ):
                try:
                    body = await response.json()
                    captured.append({"url": url, "body": body})
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            await page.goto(
                "https://www.jobsatamazon.co.uk/app#/jobSearch",
                wait_until="networkidle",
                timeout=45_000,
            )
            # Extra wait to let lazy-loaded data settle
            await page.wait_for_timeout(4_000)
        except Exception as exc:
            log.warning("jobsatamazon.co.uk navigation issue: %s", exc)

        # --- Try to parse intercepted API responses ---
        jobs: list[dict] = []
        for item in captured:
            body = item["body"]
            raw_jobs = []
            if isinstance(body, list):
                raw_jobs = body
            elif isinstance(body, dict):
                for key in ("jobs", "data", "results", "items"):
                    if key in body and isinstance(body[key], list):
                        raw_jobs = body[key]
                        break

            for j in raw_jobs:
                if not isinstance(j, dict):
                    continue
                title = j.get("title") or j.get("jobTitle") or j.get("name") or ""
                location = (
                    j.get("location") or j.get("city") or j.get("address") or ""
                )
                if isinstance(location, dict):
                    location = location.get("displayName") or location.get("city") or ""
                job_id = (
                    str(j.get("id") or j.get("jobId") or j.get("requisitionId") or "")
                )
                url = j.get("url") or j.get("applyUrl") or j.get("jobUrl") or ""
                if url and url.startswith("/"):
                    url = "https://www.jobsatamazon.co.uk" + url

                if not title and not job_id:
                    continue

                jobs.append({
                    "id": f"jauk_{job_id or title[:30]}",
                    "title": str(title).strip(),
                    "location": str(location).strip(),
                    "job_type": str(j.get("jobType") or j.get("scheduleType") or "").strip(),
                    "category": str(j.get("category") or j.get("department") or "").strip(),
                    "posted_date": str(j.get("postedDate") or j.get("posted_date") or "").strip(),
                    "url": url,
                    "source": "jobsatamazon.co.uk",
                })

        # --- Fallback: scrape visible DOM if API interception got nothing ---
        if not jobs:
            log.info("No API data intercepted; falling back to DOM scraping")
            jobs = await _scrape_dom(page)

        await browser.close()

    return jobs


async def _scrape_dom(page) -> list[dict]:
    """Parse job cards directly from the rendered DOM as a fallback."""
    jobs = []
    selectors = [
        "[data-test='job-card']",
        ".job-tile",
        ".job-card",
        "article[class*='job']",
        "li[class*='job']",
    ]
    cards = []
    for sel in selectors:
        cards = await page.query_selector_all(sel)
        if cards:
            break

    if not cards:
        log.warning("No job card elements found on jobsatamazon.co.uk")
        return jobs

    for card in cards:
        try:
            title_el = await card.query_selector(
                "h3, h2, h4, [data-test='job-title'], .job-title, [class*='title']"
            )
            loc_el = await card.query_selector(
                "[data-test='location'], .location, [class*='location'], [class*='city']"
            )
            link_el = await card.query_selector("a[href]")

            title = (await title_el.inner_text()).strip() if title_el else ""
            location = (await loc_el.inner_text()).strip() if loc_el else ""
            href = await link_el.get_attribute("href") if link_el else ""

            job_id = href.rstrip("/").split("/")[-1] if href else title[:30]
            url = (
                "https://www.jobsatamazon.co.uk" + href
                if href and href.startswith("/")
                else href
            )
            jobs.append({
                "id": f"jauk_{job_id}",
                "title": title,
                "location": location,
                "job_type": "",
                "category": "",
                "posted_date": "",
                "url": url,
                "source": "jobsatamazon.co.uk",
            })
        except Exception as exc:
            log.debug("DOM card parse error: %s", exc)

    return jobs


# ---------------------------------------------------------------------------
# Main check loop
# ---------------------------------------------------------------------------

_check_running = False  # overlap guard


async def check_jobs(run_playwright: bool = True) -> None:
    global _check_running
    if _check_running:
        log.info("Previous check still running — skipping this cycle.")
        return
    _check_running = True

    try:
        config = load_config()
        conn = init_db()
        all_jobs: list[dict] = []

        # --- amazon.jobs API (fast: ~1-3 s) ---
        if config.get("sources", {}).get("amazon_jobs_api", True):
            try:
                jobs = fetch_amazon_jobs_api(config)
                before = len(jobs)
                jobs = [j for j in jobs if is_allowed_job_title(j) and is_uk_location(j)]
                log.info(
                    "amazon.jobs API: %d fetched, %d matched UK allowed titles",
                    before, len(jobs),
                )
                all_jobs.extend(jobs)
            except Exception as exc:
                log.error("amazon.jobs API failed: %s", exc)

        # --- jobsatamazon.co.uk via Playwright (slow: 20-45 s, runs less often) ---
        if run_playwright and config.get("sources", {}).get("jobsatamazon_uk", True):
            try:
                jobs = await fetch_jobsatamazon_uk(config)
                before = len(jobs)
                jobs = [j for j in jobs if is_allowed_job_title(j)]
                log.info(
                    "jobsatamazon.co.uk: %d fetched, %d matched allowed titles",
                    before, len(jobs),
                )
                all_jobs.extend(jobs)
            except Exception as exc:
                log.error("jobsatamazon.co.uk scraper failed: %s", exc)

        # Notify only for genuinely new jobs — no message sent if nothing is new
        new_count = 0
        for job in all_jobs:
            if is_new_job(conn, job["id"]):
                mark_seen(conn, job)
                send_message(config, format_job(job))
                new_count += 1
                await asyncio.sleep(0.4)  # stay within Telegram rate limits

        if new_count:
            log.info("%d new job(s) — notifications sent.", new_count)
        else:
            log.info("No new jobs found this cycle.")

        conn.close()
    finally:
        _check_running = False


async def main_async() -> None:
    config = load_config()
    api_interval = config.get("check_interval_seconds", 60)
    pw_interval = config.get("playwright_interval_seconds", 300)

    log.info(
        "Bot started — API check every %ds | Playwright check every %ds",
        api_interval, pw_interval,
    )

    last_playwright_run: float = 0.0

    while True:
        now = time.monotonic()
        run_pw = (now - last_playwright_run) >= pw_interval

        await check_jobs(run_playwright=run_pw)

        if run_pw:
            last_playwright_run = time.monotonic()

        await asyncio.sleep(api_interval)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
