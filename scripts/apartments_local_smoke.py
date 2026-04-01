import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

from browser_fetch import fetch_page_artifacts
from scrapers.apartments_com import ApartmentsComScraper


CRITERIA = {
    "zipcodes": ["11101"],
    "min_price": 3500,
    "max_price": 6000,
    "min_bedrooms": 1,
    "max_bedrooms": None,
    "min_bathrooms": 1,
}


async def main() -> None:
    scraper = ApartmentsComScraper(CRITERIA)
    zipcode = CRITERIA["zipcodes"][0]
    url = scraper._search_url(zipcode)

    print(f"URL: {url}")
    artifacts = await fetch_page_artifacts(
        url,
        site_name=None,
        session_id=None,
        wait_for_selector="article.placard",
    )

    html = artifacts["html"]
    json_ld = scraper._parse_json_ld(html, zipcode)
    placards = scraper._parse_html_cards(html, zipcode)
    combined = scraper._extract_from_html(html, zipcode)

    print(f"Final URL: {artifacts.get('final_url')}")
    print(f"Title: {artifacts.get('title')}")
    print(f"Challenge signals: {artifacts.get('challenge_signals') or []}")
    print(f"JSON-LD listings: {len(json_ld)}")
    print(f"Placard listings: {len(placards)}")
    print(f"Combined extracted listings: {len(combined)}")

    sample = combined[:5]
    for idx, listing in enumerate(sample, 1):
        print(
            f"[{idx}] {listing.get('title')} | ${listing.get('price')} | "
            f"{listing.get('bedrooms')} bd | {listing.get('address')}"
        )

    print("\nResponse summary:")
    for response in artifacts.get("responses", [])[:10]:
        print(
            json.dumps(
                {
                    "status": response.get("status"),
                    "type": response.get("resource_type"),
                    "url": response.get("url"),
                    "content_type": response.get("content_type"),
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
