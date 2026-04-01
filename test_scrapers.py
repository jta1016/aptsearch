"""
Quick smoke-test for all three scrapers.
Usage: python test_scrapers.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from scrapers.zillow import ZillowScraper
from scrapers.apartments_com import ApartmentsComScraper
from scrapers.realtor import RealtorScraper

CRITERIA = {
    "zipcodes": ["11101"],
    "min_price": 3500,
    "max_price": 6000,
    "min_bedrooms": 1,
}


async def main():
    scrapers = [
        ("Zillow", ZillowScraper(CRITERIA)),
        ("Apartments.com", ApartmentsComScraper(CRITERIA)),
        ("Realtor.com", RealtorScraper(CRITERIA)),
    ]

    for name, scraper in scrapers:
        print(f"\n{'='*50}")
        print(f"Testing {name}...")
        print("=" * 50)
        try:
            listings = await scraper.scrape()
            print(f"\n>>> {name}: {len(listings)} listings returned")
            for i, l in enumerate(listings[:3], 1):
                print(f"  [{i}] {l.get('title', 'N/A')} | ${l.get('price')} | {l.get('bedrooms')}bd | {l.get('address')}")
        except Exception as e:
            print(f">>> {name}: EXCEPTION — {e}")


if __name__ == "__main__":
    asyncio.run(main())
