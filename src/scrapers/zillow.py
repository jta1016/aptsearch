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
            url_results = [await self._zip_url(z) for z in zipcodes]
        else:
            url_results = [await self._slug_url(s) for s in self._get_slugs()]

        urls = [u for u in url_results if u]
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

    async def _zip_url(self, zipcode: str) -> str | None:
        """Build a Zillow search URL with mapBounds from zippopotam geocoding.
        maxcopell/zillow-scraper needs searchQueryState with mapBounds to return results."""
        import json
        from urllib.parse import urlencode
        bounds = await _geocode_zip(zipcode)
        if bounds is None:
            print(f"[zillow] could not geocode {zipcode}, skipping")
            return None
        west, south, east, north = bounds
        sqs = {
            "pagination": {},
            "usersSearchTerm": zipcode,
            "mapBounds": {"west": west, "east": east, "south": south, "north": north},
            "filterState": self._filter_state(),
            "isListVisible": True,
            "isMapVisible": False,
        }
        return "https://www.zillow.com/homes/for_rent/?" + urlencode(
            {"searchQueryState": json.dumps(sqs, separators=(",", ":"))}
        )

    async def _slug_url(self, slug: str) -> str | None:
        """Build a Zillow search URL with mapBounds from Nominatim geocoding."""
        import json
        from urllib.parse import urlencode
        search_term = slug.replace("-new-york-ny", "").replace("-brooklyn-ny", "").replace("-", " ")
        bounds = await _geocode_neighborhood(search_term + ", New York, NY")
        if bounds is None:
            print(f"[zillow] could not geocode slug {slug}, skipping")
            return None
        west, south, east, north = bounds
        sqs = {
            "pagination": {},
            "usersSearchTerm": search_term.title(),
            "mapBounds": {"west": west, "east": east, "south": south, "north": north},
            "filterState": self._filter_state(),
            "isListVisible": True,
            "isMapVisible": False,
        }
        return "https://www.zillow.com/homes/for_rent/?" + urlencode(
            {"searchQueryState": json.dumps(sqs, separators=(",", ":"))}
        )

    def _filter_state(self) -> dict:
        state: dict = {
            "fr": {"value": True},
            "fsba": {"value": False},
            "fsbo": {"value": False},
            "nc": {"value": False},
            "cmsn": {"value": False},
            "auc": {"value": False},
            "fore": {"value": False},
        }
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        if min_price is not None or max_price is not None:
            mp: dict = {}
            if min_price is not None:
                mp["min"] = min_price
            if max_price is not None:
                mp["max"] = max_price
            state["mp"] = mp
        min_beds = self.criteria.get("min_bedrooms")
        max_beds = self.criteria.get("max_bedrooms")
        if min_beds is not None or max_beds is not None:
            beds: dict = {}
            if min_beds is not None:
                beds["min"] = min_beds
            if max_beds is not None:
                beds["max"] = max_beds
            state["beds"] = beds
        return state


_GEOCODE_RADIUS = 0.02  # ~2km, same as padmapper

_GEOCODE_HEADERS = {
    "User-Agent": "aptsearch/1.0 (apartment search tool; contact via github)",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _geocode_zip(zipcode: str) -> tuple[float, float, float, float] | None:
    """Return (west, south, east, north) bounds for a US zip code via zippopotam.us."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.zippopotam.us/us/{zipcode}")
        if resp.status_code != 200:
            return None
        places = resp.json().get("places") or []
        if not places:
            return None
        lat = float(places[0]["latitude"])
        lng = float(places[0]["longitude"])
        r = _GEOCODE_RADIUS
        return lng - r, lat - r, lng + r, lat + r
    except Exception as e:
        print(f"[zillow] geocode error for zip {zipcode}: {e}")
        return None


async def _geocode_neighborhood(query: str) -> tuple[float, float, float, float] | None:
    """Return (west, south, east, north) bounds for a place name via Nominatim."""
    import httpx
    try:
        async with httpx.AsyncClient(headers=_GEOCODE_HEADERS, timeout=10) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "jsonv2", "limit": 1},
            )
        if resp.status_code != 200:
            return None
        results = resp.json() or []
        if not results:
            return None
        lat = float(results[0]["lat"])
        lng = float(results[0]["lon"])
        r = _GEOCODE_RADIUS
        return lng - r, lat - r, lng + r, lat + r
    except Exception as e:
        print(f"[zillow] geocode error for '{query}': {e}")
        return None


def _parse_price(text: str) -> int | None:
    text = text.replace(",", "").replace("+", "")
    m = re.search(r"\$?([\d]+)", text)
    return int(m.group(1)) if m else None


def _extract_zipcode(text: str) -> str | None:
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", text or "")
    return match.group(1) if match else None
