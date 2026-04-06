"""
Craigslist NYC apartment scraper.
Uses the HTML search page — no browser required.
"""
import re
import httpx
from bs4 import BeautifulSoup
from proxy_support import get_proxy_url

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_URL = "https://newyork.craigslist.org/search/apa"


class CraigslistScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        listings = []
        targets = self._build_targets()

        for params in targets:
            session_id = f"craigslist_{params.get('postal', 'nyc')}"
            proxy_url = await get_proxy_url("craigslist", session_id=session_id)
            client_kwargs = dict(headers=HEADERS, timeout=30, follow_redirects=True)
            if proxy_url:
                client_kwargs["proxy"] = proxy_url

            async with httpx.AsyncClient(**client_kwargs) as client:
                try:
                    results = await self._fetch_page(client, params)
                    print(f"[craigslist] params={params} -> {len(results)} results")
                    listings.extend(results)
                except Exception as e:
                    print(f"[craigslist] error for params {params}: {type(e).__name__}: {e}")

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
            base_params["bathrooms"] = self.criteria["min_bathrooms"]
        if self.criteria.get("pets_allowed"):
            base_params["pets_cat"] = 1
            base_params["pets_dog"] = 1

        zipcodes = self.criteria.get("zipcodes", [])
        if zipcodes:
            return [{**base_params, "postal": z, "search_distance": 1} for z in zipcodes]
        else:
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

        # Current CL design (2025+): li.cl-static-search-result
        items = soup.select("li.cl-static-search-result")
        if not items:
            # Fallback selectors for older designs
            items = soup.select("li.cl-search-result") or soup.select("li.result-row")

        print(f"[craigslist] _parse: found {len(items)} raw items")

        for item in items:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                continue

        return listings

    def _parse_item(self, item) -> dict | None:
        link_el = item.find("a")
        if not link_el:
            return None

        url = link_el.get("href", "")
        if not url:
            return None
        if url and not url.startswith("http"):
            url = "https://newyork.craigslist.org" + url

        # Only keep apartment listings (filter out cars, furniture, etc.)
        if "/apa/" not in url and "/aap/" not in url:
            return None

        # Title — try .title div first, then link text
        title_el = item.select_one(".title") or item.select_one("a.posting-title") or item.select_one("a.result-title")
        title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)

        # Price
        price_el = item.select_one(".price") or item.select_one(".priceinfo") or item.select_one(".result-price")
        price = _parse_price(price_el.get_text()) if price_el else None

        # Location
        loc_el = item.select_one(".location") or item.select_one(".meta .separator + span") or item.select_one(".result-hood")
        address = loc_el.get_text(strip=True).strip("() ") if loc_el else ""

        # Beds/baths from full text — match BR, BD, bed, bedroom formats
        meta_text = item.get_text(" ", strip=True)
        bedrooms = _extract_beds(meta_text)
        bathrooms = _extract_baths(meta_text)

        # Image — Craigslist includes thumbnails in search results
        image_url = None
        img_el = item.select_one("img")
        if img_el:
            src = img_el.get("src") or img_el.get("data-src") or ""
            if "images.craigslist.org" in src or "cl-img" in src:
                image_url = re.sub(r"_\d+x\d+\.jpg$", "_600x450.jpg", src) if re.search(r"_\d+x\d+\.jpg$", src) else src

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
            "pets_allowed": None,
            "available_date": None,
            "source": "craigslist",
            "image_url": image_url,
            "description": None,
        }


def _parse_price(text: str) -> int | None:
    m = re.search(r"\$?([\d,]+)", text.replace(",", ""))
    return int(m.group(1)) if m else None


def _extract_beds(text: str) -> int | None:
    # Match "3BR", "3 BR", "3 bed", "3 bedroom", "3bd", "3 BD"
    m = re.search(r"(\d+)\s*(?:bd|br|bed|bedroom)s?\b", text, re.I)
    if m:
        return int(m.group(1))
    if re.search(r"\bstudio\b", text, re.I):
        return 0
    return None


def _extract_baths(text: str) -> float | None:
    m = re.search(r"([\d.]+)\s*(?:ba|bath|bathroom)s?\b", text, re.I)
    return float(m.group(1)) if m else None
