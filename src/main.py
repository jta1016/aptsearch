import asyncio
import sys
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Make src/ importable when running as "python src/main.py"
sys.path.insert(0, os.path.dirname(__file__))

from apify import Actor
from scrapers.craigslist import CraigslistScraper
from scrapers.zillow import ZillowScraper
from scrapers.apartments_com import ApartmentsComScraper
from scrapers.padmapper import PadmapperScraper
from scrapers.realtor import RealtorScraper
from scrapers.streeteasy import StreetEasyScraper
from ranker import rank_listings

SCRAPER_MAP = {
    "craigslist": CraigslistScraper,
    "padmapper": PadmapperScraper,
    "zillow": ZillowScraper,
    "apartments_com": ApartmentsComScraper,
    "realtor": RealtorScraper,
    "streeteasy": StreetEasyScraper,
}


def _diverse_top_n(listings: list[dict], n: int, active_sites: list[str]) -> list[dict]:
    """
    Return top n listings with per-source diversity: no single source can
    contribute more than half the results. Remaining slots are filled from
    overflow in score order so quality is preserved.
    """
    num_sources = max(1, len(active_sites))
    cap = max(3, n // 2)  # at most 50% from any one source
    from collections import defaultdict
    counts: dict = defaultdict(int)
    result, overflow = [], []
    for listing in listings:
        src = listing.get("source", "")
        if counts[src] < cap:
            counts[src] += 1
            result.append(listing)
        else:
            overflow.append(listing)
        if len(result) >= n:
            break
    # Fill any remaining slots (happens when some sources have very few results)
    for listing in overflow:
        if len(result) >= n:
            break
        result.append(listing)
    return result[:n]


def make_safe_store_key(prefix: str, raw_value: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!-_.'()")
    cleaned = "".join(ch if ch in allowed else "-" for ch in (raw_value or "default"))
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-") or "default"
    return f"{prefix}{cleaned[:220]}"


async def main():
    async with Actor:
        inp = await Actor.get_input() or {}

        criteria = {
            "zipcodes": inp.get("zipcodes") or [],
            "neighborhoods": inp.get("neighborhoods") or [],
            "min_price": inp.get("min_price"),
            "max_price": inp.get("max_price"),
            "target_price": inp.get("target_price"),
            "min_bedrooms": inp.get("min_bedrooms"),
            "max_bedrooms": inp.get("max_bedrooms"),
            "min_bathrooms": inp.get("min_bathrooms"),
            "pets_allowed": inp.get("pets_allowed", False),
            "availability_before": inp.get("availability_before"),
            "max_subway_distance_miles": inp.get("max_subway_distance_miles"),
            "preferred_subway_lines": inp.get("preferred_subway_lines") or [],
            "required_amenities": inp.get("required_amenities") or [],
            "results_per_run": inp.get("results_per_run", 20),
            "sites": inp.get("sites") or ["craigslist", "padmapper", "streeteasy", "zillow", "apartments_com", "realtor"],
        }

        Actor.log.info(f"Search criteria: {criteria}")

        # Run all scrapers in parallel
        tasks = []
        site_names = []
        for site in criteria["sites"]:
            cls = SCRAPER_MAP.get(site)
            if cls:
                tasks.append(cls(criteria).scrape())
                site_names.append(site)
            else:
                Actor.log.warning(f"Unknown site: {site}")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_listings = []
        for site, result in zip(site_names, results):
            if isinstance(result, Exception):
                Actor.log.warning(f"{site} failed: {result}")
            else:
                Actor.log.info(f"{site}: {len(result)} listings")
                all_listings.extend(result)

        Actor.log.info(f"Total before ranking: {len(all_listings)}")

        ranked = rank_listings(all_listings, criteria)
        digest_id = inp.get("digest_id") or (normalize_recipients(inp)[0] if normalize_recipients(inp) else "default")
        ranked, new_count = await prioritize_new_listings(digest_id, ranked)
        top_n = _diverse_top_n(ranked, criteria["results_per_run"], criteria["sites"])
        await remember_seen_listings(digest_id, top_n)

        Actor.log.info(f"Returning top {len(top_n)} results")

        # Build output
        output = []
        for i, listing in enumerate(top_n, 1):
            station = listing.get("_score_detail", {}).get("nearest_station") or {}
            output.append({
                "rank": i,
                "score": listing["_score"],
                "url": listing["url"],
                "source": listing["source"],
                "title": listing["title"],
                "price": listing["price"],
                "bedrooms": listing["bedrooms"],
                "bathrooms": listing["bathrooms"],
                "address": listing["address"],
                "pets_allowed": listing["pets_allowed"],
                "available_date": listing["available_date"],
                "date_listed": listing.get("date_listed"),
                "nearest_subway": station.get("name"),
                "subway_distance_miles": station.get("distance_miles"),
                "subway_lines": station.get("lines"),
                "image_url": listing["image_url"],
                "score_detail": listing["_score_detail"],
            })

        await Actor.push_data(output)

        # Send results email if one or more recipients are configured
        recipients = normalize_recipients(inp)
        if recipients and output:
            send_results_email(recipients, output, criteria, inp, new_count)
        elif recipients and not output:
            Actor.log.info("No results to email.")
        else:
            Actor.log.info("No email recipient configured — skipping email.")


def normalize_recipients(inp: dict) -> list[str]:
    recipients: list[str] = []

    raw_emails = inp.get("emails")
    if isinstance(raw_emails, list):
        recipients.extend(raw_emails)
    elif isinstance(raw_emails, str):
        recipients.extend(raw_emails.split(","))

    raw_email = inp.get("email") or os.environ.get("RESULTS_EMAIL")
    if isinstance(raw_email, str):
        recipients.extend(raw_email.split(","))

    deduped: list[str] = []
    seen: set[str] = set()
    for value in recipients:
        email = value.strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(email)
    return deduped


async def prioritize_new_listings(digest_id: str, listings: list[dict]) -> tuple[list[dict], int]:
    seen_key = make_safe_store_key("seen-", digest_id)
    seen_record = await Actor.get_value(seen_key) or {}
    seen_urls = set(seen_record.get("urls") or [])
    fresh: list[dict] = []
    seen_again: list[dict] = []

    for listing in listings:
        is_new = listing.get("url") not in seen_urls
        listing["_is_new"] = is_new
        if is_new:
            fresh.append(listing)
        else:
            seen_again.append(listing)

    return fresh + seen_again, len(fresh)


async def remember_seen_listings(digest_id: str, listings: list[dict]) -> None:
    seen_key = make_safe_store_key("seen-", digest_id)
    seen_record = await Actor.get_value(seen_key) or {}
    existing = list(seen_record.get("urls") or [])
    merged = []
    seen = set()

    for url in [item.get("url") for item in listings if item.get("url")] + existing:
        if url in seen:
            continue
        seen.add(url)
        merged.append(url)

    await Actor.set_value(seen_key, {"urls": merged[:1000]})


def send_results_email(recipients: list[str], listings: list, criteria: dict, inp: dict, new_count: int):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    if not smtp_host or "@" in smtp_host:
        smtp_host = "smtp.gmail.com"
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")

    if not smtp_user or not smtp_pass:
        Actor.log.warning(
            "Email skipped: set SMTP_USER and SMTP_PASS in Apify environment variables."
        )
        return

    if new_count:
        subject = f"Apt Search: {new_count} new listing{'s' if new_count != 1 else ''}"
    else:
        subject = f"Apt Search: {len(listings)} listing{'s' if len(listings) != 1 else ''} found"
    html = _build_email_html(listings, criteria, inp, new_count)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg, to_addrs=recipients)
        Actor.log.info(f"Email sent to {', '.join(recipients)}")
    except Exception as e:
        Actor.log.warning(f"Email failed: {e}")


def _build_email_html(listings: list, criteria: dict, inp: dict, new_count: int) -> str:
    source_labels = {
        "craigslist": "Craigslist",
        "padmapper": "PadMapper",
        "zillow": "Zillow",
        "streeteasy": "StreetEasy",
        "apartments_com": "Apartments.com",
        "realtor": "Realtor.com",
    }

    rows = []
    for item in listings:
        price = f"${item['price']:,}/mo" if item.get("price") else "Price N/A"
        beds = f"{item['bedrooms']} bd" if item.get("bedrooms") is not None else ""
        baths = f"{item['bathrooms']} ba" if item.get("bathrooms") is not None else ""
        meta = " · ".join(filter(None, [beds, baths]))
        source = source_labels.get(item.get("source", ""), item.get("source", ""))
        subway = ""
        if item.get("nearest_subway"):
            subway = f" · 🚇 {item['nearest_subway']} ({item.get('subway_distance_miles', 0):.2f} mi)"
        img_html = (
            f'<img src="{item["image_url"]}" width="280" style="border-radius:8px;display:block;margin-bottom:8px" />'
            if item.get("image_url")
            else ""
        )
        new_badge = (
            '<span style="display:inline-block;background:#dcfce7;color:#166534;padding:2px 8px;'
            'border-radius:999px;font-size:11px;font-weight:700;margin-bottom:8px">NEW</span>'
            if item.get("_is_new")
            else ""
        )
        rows.append(f"""
        <tr>
          <td style="padding:16px;border-bottom:1px solid #e5e5e3;vertical-align:top">
            {img_html}
            {new_badge}
            <div style="font-size:20px;font-weight:700;margin-bottom:4px">{price}</div>
            <div style="font-size:13px;color:#6b7280;margin-bottom:4px">{meta}{subway}</div>
            <div style="font-size:13px;margin-bottom:8px">{item.get('address') or item.get('title') or '—'}</div>
            <div style="font-size:11px;color:#6b7280;margin-bottom:8px">#{item['rank']} · {source}</div>
            <a href="{item['url']}" style="display:inline-block;background:#2563eb;color:#fff;padding:8px 16px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:500">View Listing →</a>
          </td>
        </tr>""")

    neighborhoods = ", ".join(criteria.get("neighborhoods") or [])
    zipcodes = ", ".join(criteria.get("zipcodes") or [])
    location_summary = neighborhoods or zipcodes or "New York City"
    manage_url = inp.get("manage_url") or os.environ.get("APP_BASE_URL") or ""
    unsubscribe_url = inp.get("unsubscribe_url") or ""
    digest_summary = (
        f"{new_count} new listing{'s' if new_count != 1 else ''} first"
        if new_count
        else "No brand-new listings this run"
    )
    action_links = []
    if manage_url:
        action_links.append(
            f'<a href="{manage_url}" style="color:#2563eb;text-decoration:none">Edit preferences</a>'
        )
    if unsubscribe_url:
        action_links.append(
            f'<a href="{unsubscribe_url}" style="color:#2563eb;text-decoration:none">Unsubscribe this digest</a>'
        )
    footer_links = " · ".join(action_links) if action_links else "Manage your saved searches in Apt Search"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8" /></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7f7f5;margin:0;padding:24px">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto">
    <tr>
      <td style="background:#2563eb;padding:20px 24px;border-radius:12px 12px 0 0">
        <div style="color:#fff;font-size:20px;font-weight:700">🏠 Apt Search Digest</div>
        <div style="color:#bfdbfe;font-size:13px;margin-top:4px">{len(listings)} listing{'s' if len(listings) != 1 else ''} · {location_summary}</div>
        <div style="color:#dbeafe;font-size:13px;margin-top:8px">{digest_summary}</div>
      </td>
    </tr>
    <tr>
      <td style="background:#fff;border-radius:0 0 12px 12px">
        <table width="100%" cellpadding="0" cellspacing="0">
          {''.join(rows)}
        </table>
      </td>
    </tr>
    <tr>
      <td style="padding:16px;text-align:center;font-size:12px;color:#9ca3af">
        Sent by Apt Search · {footer_links}
      </td>
    </tr>
  </table>
</body>
</html>"""


asyncio.run(main())
