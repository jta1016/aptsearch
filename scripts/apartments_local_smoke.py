import argparse
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

from browser_fetch import fetch_page_artifacts
from scrapers.apartments_com import ApartmentsComScraper


DEFAULT_CRITERIA = {
    "zipcodes": ["11101"],
    "min_price": 3500,
    "max_price": 6000,
    "min_bedrooms": 1,
    "max_bedrooms": None,
    "min_bathrooms": 1,
}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zipcode")
    parser.add_argument("--url")
    parser.add_argument("--min-price", type=int, default=DEFAULT_CRITERIA["min_price"])
    parser.add_argument("--max-price", type=int, default=DEFAULT_CRITERIA["max_price"])
    parser.add_argument("--min-beds", type=int, default=DEFAULT_CRITERIA["min_bedrooms"])
    parser.add_argument("--max-beds", type=int, default=DEFAULT_CRITERIA["max_bedrooms"])
    parser.add_argument("--min-baths", type=float, default=DEFAULT_CRITERIA["min_bathrooms"])
    args = parser.parse_args()

    criteria = {
        "zipcodes": [args.zipcode] if args.zipcode else list(DEFAULT_CRITERIA["zipcodes"]),
        "min_price": args.min_price,
        "max_price": args.max_price,
        "min_bedrooms": args.min_beds,
        "max_bedrooms": args.max_beds,
        "min_bathrooms": args.min_baths,
    }

    scraper = ApartmentsComScraper(criteria)
    zipcode = criteria["zipcodes"][0]
    url = args.url or scraper._search_url(zipcode)

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
