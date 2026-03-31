"""
Craigslist NYC apartment scraper.
Uses the HTML search page — no browser required.
"""
import re
import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_URL = "https://newyork.craigslist.org/search/aap"


class CraigslistScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        listings = []
        targets = self._build_targets()

        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            for params in targets:
                try:
                    results = await self._fetch_page(client, params)
                    listings.extend(results)
                except Exception as e:
                    print(f"[craigslist] error for params {params}: {e}")

        # Deduplicate by URL
        seen = set()
        unique = []
        for l in listings:
            if l["url"] not in seen:
                seen.add(l["url"])
                unique.append(l)

        return unique

    def _build_targets(self) -> list[dict]:
        """Build one param set per zip code (or one general search if no zips)."""
        base_params = {}

        if self.criteria.get("min_price"):
            base_params["min_price"] = self.criteria["min_price"]
        if self.criteria.get("max_price"):
            base_params["max_price"] = self.criteria["max_price"]
        if self.criteria.get("min_bedrooms") is not None:
            base_params["min_bedrooms"] = self.criteria["min_bedrooms"]
        if self.criteria.get("max_bedrooms") is not None:
            base_params["max_bedrooms"] = self.criteria["max_bedrooms"]
        if self.criteria.get("min_bathrooms"):
            # CL uses: 1, 1.5, 2, 2.5, 3, 3.5, 4
            base_params["bathrooms"] = self.criteria["min_bathrooms"]
        if self.criteria.get("pets_allowed"):
            base_params["pets_cat"] = 1
            base_params["pets_dog"] = 1

        zipcodes = self.criteria.get("zipcodes", [])
        if zipcodes:
            return [{**base_params, "postal": z, "search_distance": 1} for z in zipcodes]
        else:
            # Fall back to neighborhood keyword search
            neighborhoods = self.criteria.get("neighborhoods", [])
            if neighborhoods:
                return [{**base_params, "query": n} for n in neighborhoods]
            return [base_params]

    async def _fetch_page(self, client: httpx.AsyncClient, params: dict) -> list[dict]:
        resp = await client.get(BASE_URL, params=params)
        resp.raise_for_status()
        return self._parse(resp.text)

    def _parse(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        listings = []

        # New CL design uses <li class="cl-search-result">
        items = soup.select("li.cl-search-result")
        if not items:
            # Fallback to old design
            items = soup.select("li.result-row")

        for item in items:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                continue

        return listings

    def _parse_item(self, item) -> dict | None:
        # URL + title
        link_el = item.select_one("a.posting-title") or item.select_one("a.result-title")
        if not link_el:
            return None

        url = link_el.get("href", "")
        if url and not url.startswith("http"):
            url = "https://newyork.craigslist.org" + url
        title = link_el.get_text(strip=True)

        # Price
        price_el = item.select_one(".priceinfo") or item.select_one(".result-price")
        price = None
        if price_el:
            price = _parse_price(price_el.get_text())

        # Beds/baths from meta
        meta_text = item.get_text(" ", strip=True)
        bedrooms = _extract_beds(meta_text)
        bathrooms = _extract_baths(meta_text)

        # Location
        hood_el = item.select_one(".meta .separator + span") or item.select_one(".result-hood")
        address = hood_el.get_text(strip=True).strip("() ") if hood_el else ""

        # Pets — CL doesn't surface this in search results, needs listing page
        # We'll mark as unknown; ranker gives partial credit
        pets_allowed = None

        return {
            "url": url,
            "title": title,
            "price": price,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "address": address,
            "zipcode": None,
            "lat": None,
            "lng": None,
            "pets_allowed": pets_allowed,
            "available_date": None,
            "source": "craigslist",
            "image_url": None,
            "description": None,
        }


def _parse_price(text: str) -> int | None:
    m = re.search(r"\$?([\d,]+)", text.replace(",", ""))
    return int(m.group(1)) if m else None


def _extract_beds(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:br|bed|bedroom)", text, re.I)
    if m:
        return int(m.group(1))
    if re.search(r"\bstudio\b", text, re.I):
        return 0
    return None


def _extract_baths(text: str) -> float | None:
    m = re.search(r"([\d.]+)\s*(?:ba|bath|bathroom)", text, re.I)
    return float(m.group(1)) if m else None
