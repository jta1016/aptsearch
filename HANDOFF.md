# Handoff

## Current Sync State

- Branch: `codex/update-scheduled-search-ui`
- Latest Git commit: `fafc1a4`
- Latest Apify build deployed from this branch: `0.1.25`
- Repo status before this handoff commit:
  - modified: `scripts/apartments_local_smoke.py`
  - untracked: `HANDOFF.md`

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
  - blocked by PerimeterX / captcha path
  - no real listings rendered in tested cloud runs
- `apartments_com`
  - blocked in Apify/cloud
  - also blocked in the tested local runs during this session
- `realtor`
  - GraphQL returned `401`
  - page path returned `429`
  - browser fallback still did not produce listings

## What Changed This Session

- Added a new `StreetEasy` scraper and wired it into:
  - `src/main.py`
  - `webapp/server.py`
  - `webapp/static/index.html`
  - `test_scrapers.py`
- Added browser/network instrumentation for blocked sources:
  - `zillow`
  - `apartments_com`
  - `streeteasy`
- Confirmed `StreetEasy` works with Apify residential proxy.
- Reworked `apartments_com` to be HTML-first:
  - prefer rendered HTML
  - extract from JSON-LD
  - extract from `article.placard`
  - wait for `article.placard` in Playwright
  - treat hidden API endpoints as fallback only
- Fixed Apartments.com bedroom path logic:
  - open-ended searches like `min_bedrooms=1` no longer force a narrow `1-bedrooms/` path
  - filtering is now done locally from parsed HTML
- Added local validator script:
  - `scripts/apartments_local_smoke.py`

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

- The repo and actor are functionally up to date through commit `fafc1a4` / build `0.1.25`.
- The strongest product state right now is:
  - Craigslist
  - PadMapper
  - StreetEasy
- Zillow / Apartments.com / Realtor are still anti-bot or auth blocked in the environments tested here.
- Apartments.com parser work is in place, but it has not yet been validated against a real, unblocked listing page in this session.

## If Continuing From Here

Best next moves:

1. Treat `StreetEasy` as the new high-value working source and improve mapping/coverage for more NYC neighborhoods and zips.
2. Decide whether `apartments_com` stays experimental or is dropped from default runs.
3. If revisiting blocked sources, do it only with a materially different access strategy, not more minor header or parser tweaks.

## Things Not To Reinvestigate First

- Do not spend more time on minor Zillow header tweaks.
- Do not assume Apartments.com is parser-broken when the page title is `Access Denied`.
- Do not assume Realtor is close to working without addressing the `401`/`429` behavior first.
