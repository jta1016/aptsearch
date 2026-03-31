import asyncio
import sys
import os

# Make src/ importable when running as "python src/main.py"
sys.path.insert(0, os.path.dirname(__file__))

from apify import Actor
from scrapers.craigslist import CraigslistScraper
from scrapers.zillow import ZillowScraper
from scrapers.apartments_com import ApartmentsComScraper
from scrapers.realtor import RealtorScraper
from ranker import rank_listings

SCRAPER_MAP = {
    "craigslist": CraigslistScraper,
    "zillow": ZillowScraper,
    "apartments_com": ApartmentsComScraper,
    "realtor": RealtorScraper,
}


async def main():
    async with Actor:
        inp = await Actor.get_input() or {}

        criteria = {
            "zipcodes": inp.get("zipcodes", []),
            "neighborhoods": inp.get("neighborhoods", []),
            "min_price": inp.get("min_price"),
            "max_price": inp.get("max_price"),
            "target_price": inp.get("target_price"),
            "min_bedrooms": inp.get("min_bedrooms", 1),
            "max_bedrooms": inp.get("max_bedrooms"),
            "min_bathrooms": inp.get("min_bathrooms", 1),
            "pets_allowed": inp.get("pets_allowed", False),
            "availability_before": inp.get("availability_before"),
            "max_subway_distance_miles": inp.get("max_subway_distance_miles", 0.5),
            "preferred_subway_lines": inp.get("preferred_subway_lines", []),
            "results_per_run": inp.get("results_per_run", 5),
            "sites": inp.get("sites", ["craigslist", "zillow", "apartments_com", "realtor"]),
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
        top_n = ranked[: criteria["results_per_run"]]

        Actor.log.info(f"Returning top {len(top_n)} results")

        # Output clean summary for each result
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
                "nearest_subway": station.get("name"),
                "subway_distance_miles": station.get("distance_miles"),
                "subway_lines": station.get("lines"),
                "image_url": listing["image_url"],
                "score_detail": listing["_score_detail"],
            })

        await Actor.push_data(output)


asyncio.run(main())
