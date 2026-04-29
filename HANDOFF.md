# Handoff

## Current Sync State

- Branch: `claude/fix-email-drafts-iWvhI` (1 commit ahead of `main`)
- Latest Git commit: `e11048a` — "Add CLAUDE.md: document digest architecture and ban error Gmail drafts"
- Latest Apify build deployed: `0.1.25` (built from `ff9e049`)
- Working tree: clean

## What Is Working

- `craigslist`
  - stable
- `padmapper`
  - stable
- `streeteasy`
  - working live on Apify
  - confirmed run on build `0.1.23` returned `12 listings` for `11101`

## What Is Not Working

- `zillow`
  - ~~blocked by PerimeterX / captcha path on HTML pages~~
  - **reworked**: now hits `GetSearchPageState.htm` XHR API instead of HTML page
  - uses residential proxies (same config as streeteasy/apartments_com)
  - XHR endpoint returns clean JSON; historically less protected than HTML page
  - not yet validated against a live cloud run — needs a deploy + test
  - if still blocked: fall back to `maxcopell/zillow-scraper` actor ($2/1k results)
- `apartments_com`
  - blocked in Apify/cloud
  - also blocked in the tested local runs during this session
- `realtor`
  - GraphQL returned `401`
  - page path returned `429`
  - browser fallback still did not produce listings

## What Changed This Session

### Previous session (build 0.1.25 / commit ff9e049)
- Added a new `StreetEasy` scraper and wired it into `src/main.py`, `webapp/server.py`, `webapp/static/index.html`, `test_scrapers.py`
- Added browser/network instrumentation for blocked sources (zillow, apartments_com, streeteasy)
- Confirmed StreetEasy works with Apify residential proxy
- Reworked Apartments.com to HTML-first (JSON-LD → `article.placard` → API fallback)
- Fixed Apartments.com bedroom path logic (open-ended searches no longer force a narrow path)
- Added `scripts/apartments_local_smoke.py`

### This session (commit e11048a)
- **Fixed recurring Gmail error-draft bug**: A scheduled Claude Code agent was trying to
  call `api.apify.com` directly (blocked in Claude Code's network sandbox), using the wrong
  actor ID `jta93~aptsearch` (retired), and creating a Gmail draft every time it failed.
  Three drafts accumulated (Apr 25, Apr 26, Apr 28/29).
- Created `CLAUDE.md` documenting the correct architecture, the correct actor IDs, and
  explicitly prohibiting future agent sessions from creating Gmail drafts as error notifications.
- **Pending manual step**: delete the 3 "Daily Apt Digest" error drafts from Gmail manually
  (Gmail MCP token expired before they could be trashed programmatically).
- **Open question left with user**: do you want to add `api.apify.com` to the Claude Code
  network allowlist so agent sessions can optionally call Apify, or keep it firewalled and
  rely solely on Apify's native scheduling?

## Important Live Evidence

### StreetEasy

- Live Apify run on build `0.1.23`:
  - page GET returned `200`
  - parser returned `12 listings`

### Zillow

- Live Apify run showed:
  - document `403`
  - title similar to `Access to this page has been denied`
  - PerimeterX/captcha-related assets loaded
  - no listings rendered

### Apartments.com

- Live Apify runs showed:
  - document `403`
  - title `Access Denied`
  - browser never reached a listing page in cloud
- Local smoke runs during this session also returned:
  - title `Access Denied`
  - `403`
  - no JSON-LD
  - no placards

## Plain-English Conclusion

- The digest is working end-to-end: Craigslist + PadMapper + StreetEasy run on Apify's schedule
  and email results directly via SMTP. No Claude Code involvement needed in the loop.
- Zillow / Apartments.com / Realtor remain anti-bot blocked. Don't revisit without a materially
  different strategy.
- The CLAUDE.md now prevents future agent sessions from causing the draft-accumulation bug again.

## To Pick Up on Mac Mini

```bash
git clone https://github.com/jta1016/aptsearch
cd aptsearch
git checkout claude/fix-email-drafts-iWvhI   # or merge to main if preferred

pip install -r requirements.txt
cd webapp && uvicorn server:app --reload --port 8787
```

Env vars needed locally: `APIFY_TOKEN`

## If Continuing From Here

1. **Answer the open question**: add `api.apify.com` to `.claude/settings.json` allowlist?
   Run `yes` if you want agent sessions to be able to inspect Apify runs directly.
2. **Delete the 3 Gmail error drafts** manually (Daily Apt Digest — Apr 25, Apr 26, Apr 28/29).
3. **StreetEasy coverage**: expand neighborhood/zip mapping for more NYC areas.
4. **Decide on `apartments_com`**: drop from default runs or keep as experimental.

## Things Not To Reinvestigate First

- Do not spend more time on Zillow minor header tweaks — actor delegation is the strategy if revisiting.
- Do not assume Apartments.com is parser-broken when the page title is `Access Denied` — it's the bot block.
- Do not assume Realtor is close to working without addressing the `401`/`429` behavior first.
