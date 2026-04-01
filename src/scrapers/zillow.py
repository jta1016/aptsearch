"""
Zillow rental scraper.
Delegates to maxcopell/zillow-scraper Apify actor to bypass PerimeterX.

Both the HTML page and the internal XHR API (async-create-search-page-state)
are blocked by PerimeterX even with residential proxies. The actor handles
its own bot-evasion and is the only viable approach without a browser session.

Cost: ~$2/1,000 results (within Apify free tier for typical usage).
"""
import re

from apify import Actor

ZILLOW_ACTOR_ID = "maxcopell/zillow-scraper"
MAX_ITEMS_PER_RUN = 200

ZIP_TO_SLUG = {
    "10001": "chelsea-new-york-ny", "10002": "lower-east-side-new-york-ny",
    "10003": "east-village-new-york-ny", "10007": "tribeca-new-york-ny",
    "10009": "east-village-new-york-ny", "10010": "gramercy-park-new-york-ny",
    "10011": "chelsea-new-york-ny", "10012": "soho-new-york-ny",
    "10013": "soho-new-york-ny", "10014": "west-village-new-york-ny",
    "10016": "murray-hill-new-york-ny", "10017": "midtown-east-new-york-ny",
    "10018": "hell-s-kitchen-new-york-ny", "10019": "midtown-west-new-york-ny",
    "10021": "upper-east-side-new-york-ny", "10022": "midtown-east-new-york-ny",
    "10023": "upper-west-side-new-york-ny", "10024": "upper-west-side-new-york-ny",
    "10025": "upper-west-side-new-york-ny", "10026": "harlem-new-york-ny",
    "10027": "harlem-new-york-ny", "10028": "upper-east-side-new-york-ny",
    "10029": "east-harlem-new-york-ny", "10030": "harlem-new-york-ny",
    "10031": "harlem-new-york-ny", "10032": "washington-heights-new-york-ny",
    "10033": "washington-heights-new-york-ny", "10034": "inwood-new-york-ny",
    "10036": "hell-s-kitchen-new-york-ny", "10037": "harlem-new-york-ny",
    "10038": "financial-district-new-york-ny", "10040": "inwood-new-york-ny",
    "10065": "upper-east-side-new-york-ny", "10075": "upper-east-side-new-york-ny",
    "10128": "upper-east-side-new-york-ny", "10280": "battery-park-city-new-york-ny",
    "11201": "brooklyn-heights-brooklyn-ny", "11205": "fort-greene-brooklyn-ny",
    "11206": "williamsburg-brooklyn-ny", "11211": "williamsburg-brooklyn-ny",
    "11215": "park-slope-brooklyn-ny", "11217": "boerum-hill-brooklyn-ny",
    "11221": "bushwick-brooklyn-ny", "11222": "greenpoint-brooklyn-ny",
    "11225": "crown-heights-brooklyn-ny", "11231": "carroll-gardens-brooklyn-ny",
    "11237": "bushwick-brooklyn-ny", "11238": "prospect-heights-brooklyn-ny",
    "11101": "long-island-city-new-york-ny", "11102": "astoria-new-york-ny",
    "11103": "astoria-new-york-ny", "11104": "sunnyside-new-york-ny",
    "11105": "astoria-new-york-ny", "11106": "astoria-new-york-ny",
    "11377": "woodside-new-york-ny", "11385": "ridgewood-new-york-ny",
}

DEFAULT_SLUG = "new-york-ny"


class ZillowScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        zipcodes = self.criteria.get("zipcodes", [])
        if zipcodes:
            urls = [self._zip_url(z) for z in zipcodes]
        else:
            urls = [self._slug_url(s) for s in self._get_slugs()]

        if not urls:
            return []

        return await self._run_actor(urls)

    async def _run_actor(self, urls: list[str]) -> list[dict]:
        client = Actor.new_client()
        start_urls = [{"url": url} for url in urls]
        print(f"[zillow] calling {ZILLOW_ACTOR_ID} with {len(start_urls)} URL(s)")

        try:
            run = await client.actor(ZILLOW_ACTOR_ID).call(
                run_input={
                    "searchUrls": start_urls,
                    "maxItems": MAX_ITEMS_PER_RUN,
                },
                timeout_secs=300,
            )
        except Exception as e:
            print(f"[zillow] actor run failed: {e}")
            return []

        if not run or not run.get("defaultDatasetId"):
            print("[zillow] actor run returned no dataset ID")
            return []

        dataset_id = run["defaultDatasetId"]
        print(f"[zillow] actor finished, fetching dataset {dataset_id}")

        try:
            dataset_page = await client.dataset(dataset_id).list_items()
            raw_items = dataset_page.items
        except Exception as e:
            print(f"[zillow] failed to fetch dataset: {e}")
            return []

        print(f"[zillow] actor returned {len(raw_items)} raw items")

        listings = []
        for item in raw_items:
            try:
                parsed = self._map_item(item)
                if parsed and self._matches_criteria(parsed):
                    listings.append(parsed)
            except Exception:
                continue

        seen = set()
        unique = []
        for listing in listings:
            key = listing["url"] + str(listing.get("bedrooms"))
            if key not in seen:
                seen.add(key)
                unique.append(listing)

        print(f"[zillow] returning {len(unique)} listings after filtering/dedup")
        return unique

    def _map_item(self, item: dict) -> dict | None:
        url = item.get("url") or item.get("detailUrl") or ""
        if not url:
            return None
        if not url.startswith("http"):
            url = "https://www.zillow.com" + url

        address = item.get("address") or item.get("streetAddress") or ""
        price_raw = item.get("price") or item.get("unformattedPrice") or ""
        price = _parse_price(str(price_raw))
        beds_raw = item.get("bedrooms") or item.get("beds")
        baths_raw = item.get("bathrooms") or item.get("baths")
        zipcode = item.get("zipcode") or item.get("addressZipcode") or _extract_zipcode(address)
        lat = item.get("latitude") or item.get("lat")
        lng = item.get("longitude") or item.get("lng")

        return {
            "url": url,
            "title": item.get("name") or item.get("buildingName") or address,
            "price": price,
            "bedrooms": int(beds_raw) if beds_raw is not None else None,
            "bathrooms": float(baths_raw) if baths_raw is not None else None,
            "address": address,
            "zipcode": zipcode,
            "lat": float(lat) if lat is not None else None,
            "lng": float(lng) if lng is not None else None,
            "pets_allowed": item.get("petsAllowed") or None,
            "available_date": item.get("availableDate") or None,
            "source": "zillow",
            "image_url": item.get("imgSrc") or item.get("imageUrl") or None,
            "description": item.get("description") or None,
        }

    def _matches_criteria(self, listing: dict) -> bool:
        beds = listing.get("bedrooms")
        min_beds = self.criteria.get("min_bedrooms")
        max_beds = self.criteria.get("max_bedrooms")
        if min_beds is not None and beds is not None and beds < min_beds:
            return False
        if max_beds is not None and beds is not None and beds > max_beds:
            return False
        price = listing.get("price")
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        if min_price is not None and price is not None and price < min_price:
            return False
        if max_price is not None and price is not None and price > max_price:
            return False
        return True

    def _get_slugs(self) -> list[str]:
        neighborhoods = self.criteria.get("neighborhoods", [])
        if neighborhoods:
            return [n.lower().replace(" ", "-") + "-new-york-ny" for n in neighborhoods]
        return [DEFAULT_SLUG]

    def _zip_url(self, zipcode: str) -> str:
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        min_beds = self.criteria.get("min_bedrooms")
        max_beds = self.criteria.get("max_bedrooms")
        params = []
        if min_price or max_price:
            params.append(f"price={min_price or 0}-{max_price or 99999}")
        if min_beds is not None or max_beds is not None:
            params.append(f"beds={min_beds or 0}-{max_beds or 8}")
        query = ("?" + "&".join(params)) if params else ""
        return f"https://www.zillow.com/homes/for_rent/{zipcode}_rb/{query}"

    def _slug_url(self, slug: str) -> str:
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        min_beds = self.criteria.get("min_bedrooms")
        max_beds = self.criteria.get("max_bedrooms")
        params = []
        if min_price or max_price:
            params.append(f"price={min_price or 0}-{max_price or 99999}")
        if min_beds is not None or max_beds is not None:
            params.append(f"beds={min_beds or 0}-{max_beds or 8}")
        query = ("?" + "&".join(params)) if params else ""
        return f"https://www.zillow.com/{slug}/rentals/{query}"


def _parse_price(text: str) -> int | None:
    text = text.replace(",", "").replace("+", "")
    m = re.search(r"\$?([\d]+)", text)
    return int(m.group(1)) if m else None


def _extract_zipcode(text: str) -> str | None:
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", text or "")
    return match.group(1) if match else None
