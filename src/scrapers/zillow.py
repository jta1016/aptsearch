"""
Zillow rental scraper.
Fetches search page and extracts listings from __NEXT_DATA__ JSON.
No browser required — uses httpx only.
"""
import re
import json
import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Upgrade-Insecure-Requests": "1",
}

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
        listings = []
        zipcodes = self.criteria.get("zipcodes", [])

        # Prefer direct zip URL; fall back to neighborhood slugs if no zipcodes given.
        if zipcodes:
            urls = [self._zip_url(z) for z in zipcodes]
        else:
            slugs = self._get_slugs()
            urls = [self._slug_url(s) for s in slugs]

        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            for url in urls:
                try:
                    results = await self._fetch(client, url)
                    listings.extend(results)
                except Exception as e:
                    print(f"[zillow] error for {url}: {e}")

        seen = set()
        unique = []
        for listing in listings:
            key = listing["url"] + str(listing.get("bedrooms"))
            if key not in seen:
                seen.add(key)
                unique.append(listing)
        return unique

    def _get_slugs(self) -> list[str]:
        neighborhoods = self.criteria.get("neighborhoods", [])
        if neighborhoods:
            return [n.lower().replace(" ", "-") + "-new-york-ny" for n in neighborhoods]
        return [DEFAULT_SLUG]

    def _zip_url(self, zipcode: str) -> str:
        """Build a Zillow rentals URL using zip code directly."""
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
        """Build a Zillow rentals URL using a neighborhood slug."""
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

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> list[dict]:
        resp = await client.get(url)
        if resp.status_code != 200:
            print(f"[zillow] HTTP {resp.status_code} for {url}")
            return []
        return self._parse(resp.text)

    def _parse(self, html: str) -> list[dict]:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            print("[zillow] __NEXT_DATA__ script tag not found")
            return []
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            print("[zillow] failed to parse __NEXT_DATA__ JSON")
            return []

        list_results = self._extract_list_results(data)
        if list_results is None:
            print("[zillow] could not find listResults in __NEXT_DATA__")
            return []

        listings = []
        for item in list_results:
            try:
                listings.extend(self._expand_item(item))
            except Exception:
                continue
        return listings

    def _extract_list_results(self, data: dict) -> list | None:
        """Try multiple known JSON paths for listResults."""
        page_props = data.get("props", {}).get("pageProps", {})

        paths = [
            # Current Next.js structure
            ["searchPageState", "cat1", "searchResults", "listResults"],
            # Alternate path seen in 2024
            ["cat1", "searchResults", "listResults"],
            # Older structure
            ["initialData", "cat1", "searchResults", "listResults"],
            ["initialSearchData", "cat1", "searchResults", "listResults"],
        ]
        for path in paths:
            node = page_props
            for key in path:
                if isinstance(node, dict):
                    node = node.get(key)
                else:
                    node = None
                    break
            if isinstance(node, list):
                return node

        # Deep search: look for any key named listResults
        return self._deep_find(page_props, "listResults")

    def _deep_find(self, obj, key: str, depth: int = 0):
        """Recursively search for a key in nested dicts."""
        if depth > 8:
            return None
        if isinstance(obj, dict):
            if key in obj and isinstance(obj[key], list):
                return obj[key]
            for v in obj.values():
                result = self._deep_find(v, key, depth + 1)
                if result is not None:
                    return result
        return None

    def _expand_item(self, item: dict) -> list[dict]:
        """Buildings have a units array — expand into one listing per unit."""
        base_url = item.get("detailUrl", "")
        if not base_url:
            return []
        if not base_url.startswith("http"):
            base_url = "https://www.zillow.com" + base_url

        address = item.get("address", "")
        zipcode = item.get("addressZipcode", "") or None
        building_name = item.get("statusText", "") or item.get("buildingName", "")
        lat = item.get("latLong", {}).get("latitude") if item.get("latLong") else None
        lng = item.get("latLong", {}).get("longitude") if item.get("latLong") else None
        image = item.get("imgSrc") or None

        units = item.get("units", [])
        if not units:
            price_text = item.get("price", "") or item.get("unformattedPrice", "")
            price = _parse_price(str(price_text))
            beds_raw = item.get("beds")
            baths_raw = item.get("baths")
            return [{
                "url": base_url,
                "title": building_name or address,
                "price": price,
                "bedrooms": int(beds_raw) if beds_raw is not None else None,
                "bathrooms": float(baths_raw) if baths_raw is not None else None,
                "address": address,
                "zipcode": zipcode,
                "lat": lat, "lng": lng,
                "pets_allowed": None,
                "available_date": None,
                "source": "zillow",
                "image_url": image,
                "description": None,
            }]

        results = []
        for unit in units:
            price = _parse_price(str(unit.get("price", "")))
            beds_raw = unit.get("beds")
            beds = int(beds_raw) if beds_raw is not None else None

            min_beds = self.criteria.get("min_bedrooms")
            max_beds = self.criteria.get("max_bedrooms")
            if min_beds is not None and beds is not None and beds < min_beds:
                continue
            if max_beds is not None and beds is not None and beds > max_beds:
                continue

            title = f"{building_name} - {beds}bd" if building_name else f"{address} - {beds}bd"
            results.append({
                "url": base_url,
                "title": title,
                "price": price,
                "bedrooms": beds,
                "bathrooms": None,
                "address": address,
                "zipcode": zipcode,
                "lat": lat, "lng": lng,
                "pets_allowed": None,
                "available_date": None,
                "source": "zillow",
                "image_url": image,
                "description": None,
            })
        return results


def _parse_price(text: str) -> int | None:
    text = text.replace(",", "").replace("+", "")
    m = re.search(r"\$?([\d]+)", text)
    return int(m.group(1)) if m else None
