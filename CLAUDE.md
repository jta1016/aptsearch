# Apt Search — Claude Code Guide

## Architecture Overview

This project has two layers:

1. **Apify actor** (`comfy-classmate~aptsearch`, raw ID `j9k3vdmUiKPvnneH7`) — runs the scrapers,
   ranks results, and emails them directly via SMTP.  It lives in `src/` and is deployed to
   Apify's cloud.

2. **Web frontend** (`webapp/static/index.html` + `webapp/server.py`) — lets users configure
   preferences and Apify-native schedules.  The scheduled runs are owned and triggered by
   **Apify's built-in scheduler**, not by this agent.

## Daily Digest — How It Actually Works

```
Apify scheduler  →  comfy-classmate~aptsearch runs  →  actor emails results via SMTP
```

The actor sends results emails itself (see `src/main.py:send_results_email`).  SMTP credentials
(`SMTP_USER`, `SMTP_PASS`) are configured as Apify environment variables.

**The Claude Code agent is NOT part of this flow.**  Do not attempt to trigger the actor,
poll for run results, or send/draft digest emails from a Claude Code session.

## What This Agent Should (and Should Not) Do

### DO
- Make code changes to `src/`, `webapp/`, `api/`, `netlify/`, `scripts/`
- Run local tests (`pytest`, `python test_scrapers.py`)
- Help debug scraper logic or ranking logic

### DO NOT
- Call `api.apify.com` directly — the host is not reachable from the Claude Code sandbox
- Attempt to start or poll an Apify actor run
- Create Gmail drafts as error notifications when Apify is unreachable
- Use the Gmail MCP tools (`create_draft`, etc.) for operational error reporting

If the Apify API is unreachable, **stop and explain the limitation** to the user.  Do not
create Gmail drafts or any other side-effect as a substitute for a failed operation.

## Correct Actor ID

| Field | Value |
|---|---|
| Slug | `comfy-classmate~aptsearch` |
| Raw ID (for schedule API) | `j9k3vdmUiKPvnneH7` |

The old actor (`jta93~aptsearch`) has been retired.  Any reference to it should be updated
to the values above.

## Proxy Endpoints

`api.apify.com` is not directly reachable from this environment.  All Apify calls from the
**browser/frontend** go through one of these proxy routes:

| Platform | Endpoint |
|---|---|
| Vercel | `/api/apify` (see `api/apify.js`) |
| Netlify | `/api/apify` (see `netlify/functions/apify.mjs`) |
| Local dev | `http://localhost:8787/api/apify` (see `webapp/server.py`) |

These proxies exist for the frontend only.  Do not use them from a Claude Code agent session
to operate the digest — the digest is Apify's responsibility.

## Working Sources (as of last handoff)

| Source | Status |
|---|---|
| Craigslist | Stable |
| PadMapper | Stable |
| StreetEasy | Working on Apify (12 listings confirmed on build 0.1.23) |
| Zillow | Blocked (PerimeterX) |
| Apartments.com | Blocked (anti-bot 403) |
| Realtor.com | Blocked (401 / 429) |

See `HANDOFF.md` for full details.

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the webapp locally
cd webapp && uvicorn server:app --reload --port 8787

# Run scraper smoke tests (requires APIFY_TOKEN env var)
python test_scrapers.py
```
