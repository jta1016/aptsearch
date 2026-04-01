"""
Apartments.com scraper.
Strategy:
  1. POST to the internal search-service API (/services/search/) for JSON results.
  2. If that fails, GET the search results page and extract embedded JSON
     (window.__INITIAL_STATE__ / window.resultsList / etc.).
  3. If both fail, parse listing cards from the HTML with BeautifulSoup.
No browser required — uses httpx only.
"""
import re
import json
import httpx
from bs4 import BeautifulSoup
from browser_fetch import fetch_page_html

# Headers that mimic a real Chrome browser.
_HTML_HEADERS = {
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

_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.apartments.com",
    "Referer": "https://www.apartments.com/",
    "X-Requested-With": "XMLHttpRequest",
}

_BED_LABELS = {
    0: "Studio", 1: "OneBedroom", 2: "TwoBedroom",
    3: "ThreeBedroom", 4: "FourPlusBedroom",
}


class ApartmentsComScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        listings = []
        zipcodes = self.criteria.get("zipcodes", [])
        if not zipcodes:
            zipcodes = ["10001"]

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for zipcode in zipcodes:
                try:
                    results = await self._fetch(client, zipcode)
                    listings.extend(results)
                except Exception as e:
                    print(f"[apartments_com] error for {zipcode}: {e}")

        return listings

    # ------------------------------------------------------------------
    # Primary: internal search-service API
    # ------------------------------------------------------------------

    async def _fetch(self, client: httpx.AsyncClient, zipcode: str) -> list[dict]:
        # Try 1: JSON search service
        try:
            results = await self._api_search(client, zipcode)
            if results:
                print(f"[apartments_com] API returned {len(results)} listings for {zipcode}")
                return results
        except Exception as e:
            print(f"[apartments_com] API attempt failed for {zipcode}: {e}")

        # Try 2: fetch page HTML → extract embedded JSON
        try:
            results = await self._page_search(client, zipcode)
            if results:
                print(f"[apartments_com] page JSON returned {len(results)} listings for {zipcode}")
                return results
        except Exception as e:
            print(f"[apartments_com] page JSON attempt failed for {zipcode}: {e}")

        print(f"[apartments_com] all attempts failed for {zipcode}")
        return []

    async def _api_search(self, client: httpx.AsyncClient, zipcode: str) -> list[dict]:
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        min_beds = self.criteria.get("min_bedrooms", 0) or 0

        bed_label = _BED_LABELS.get(min_beds, "OneBedroom")

        payload = {
            "t": "city",
            "s": "searchResults",
            "l": zipcode,
            "highlights": [],
            "m": None,
            "mapView": None,
            "f": {
                "minRent": min_price,
                "maxRent": max_price,
                "beds": bed_label,
            },
            "ab": {},
        }

        resp = await client.post(
            "https://www.apartments.com/services/search/",
            json=payload,
            headers=_API_HEADERS,
        )
        print(f"[apartments_com] search API HTTP {resp.status_code} for {zipcode}")
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")

        data = resp.json()
        items = (
            data.get("data", {}).get("items")
            or data.get("items")
            or data.get("results")
            or []
        )
        return [self._map_api_item(item, zipcode) for item in items if item]

    def _map_api_item(self, item: dict, zipcode: str) -> dict:
        # Handle both the "classic" and newer API shapes.
        geo = item.get("geography") or {}
        coord = geo.get("location") or {}
        price_raw = item.get("minRent") or item.get("rent") or item.get("price")

        return {
            "url": item.get("url") or item.get("listingUrl") or "",
            "title": item.get("name") or item.get("propertyName") or "",
            "price": int(price_raw) if price_raw else None,
            "bedrooms": item.get("minBeds") or item.get("beds"),
            "bathrooms": item.get("minBaths") or item.get("baths"),
            "address": item.get("address") or geo.get("address") or "",
            "zipcode": item.get("zip") or zipcode,
            "lat": coord.get("lat") or coord.get("latitude"),
            "lng": coord.get("lon") or coord.get("longitude"),
            "pets_allowed": item.get("petsAllowed") or item.get("cats") or None,
            "available_date": item.get("availableDate"),
            "source": "apartments_com",
            "image_url": item.get("imageUrl") or item.get("photo"),
            "description": item.get("description"),
        }

    # ------------------------------------------------------------------
    # Fallback: fetch HTML page, extract embedded JSON
    # ------------------------------------------------------------------

    async def _page_search(self, client: httpx.AsyncClient, zipcode: str) -> list[dict]:
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        min_beds = self.criteria.get("min_bedrooms", 0) or 0

        # Apartments.com URL for zip code search with filters in the path.
        # e.g. https://www.apartments.com/11101/min-price-3500-max-price-6000-1-bedrooms/
        price_seg = ""
        if min_price:
            price_seg += f"min-price-{min_price}-"
        if max_price:
            price_seg += f"max-price-{max_price}-"
        bed_seg = f"{min_beds}-bedrooms/" if min_beds else ""
        url = f"https://www.apartments.com/{zipcode}/{price_seg}{bed_seg}"

        resp = await client.get(url, headers=_HTML_HEADERS)
        print(f"[apartments_com] page GET HTTP {resp.status_code} for {url}")
        if resp.status_code == 200:
            html = resp.text
        else:
            print(f"[apartments_com] switching to Playwright for {url}")
            html = await fetch_page_html(url)

        # Try several embedded-JSON patterns.
        for pattern, extractor in [
            (r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;', self._extract_initial_state),
            (r'window\.resultsList\s*=\s*(\[.+?\])\s*;', self._extract_results_list),
            (r'window\.placardData\s*=\s*(\{.+?\})\s*;', self._extract_placard_data),
        ]:
            m = re.search(pattern, html, re.S)
            if m:
                try:
                    obj = json.loads(m.group(1))
                    results = extractor(obj, zipcode)
                    if results:
                        return results
                except Exception:
                    pass

        # Last resort: BeautifulSoup card parsing.
        return self._parse_html_cards(html, zipcode)

    def _extract_initial_state(self, data: dict, zipcode: str) -> list[dict]:
        items = (
            data.get("searchResults", {}).get("listings")
            or data.get("listings")
            or []
        )
        return [self._map_api_item(i, zipcode) for i in items if i]

    def _extract_results_list(self, data: list, zipcode: str) -> list[dict]:
        return [self._map_api_item(i, zipcode) for i in data if isinstance(i, dict)]

    def _extract_placard_data(self, data: dict, zipcode: str) -> list[dict]:
        items = data.get("items") or []
        return [self._map_api_item(i, zipcode) for i in items if i]

    def _parse_html_cards(self, html: str, zipcode: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        listings = []
        selectors = [
            "article.placard",
            "li.mortar-wrapper",
            "div[data-listingid]",
            "div.property-card",
        ]
        cards = []
        for sel in selectors:
            cards = soup.select(sel)
            if cards:
                break

        for card in cards:
            try:
                listing = self._parse_card(card, zipcode)
                if listing and listing["url"]:
                    listings.append(listing)
            except Exception:
                continue

        print(f"[apartments_com] HTML card parse found {len(listings)} listings for {zipcode}")
        return listings

    def _parse_card(self, card, zipcode: str) -> dict | None:
        link = (
            card.select_one("a.property-link")
            or card.select_one('a[href*="apartments.com"]')
            or card.select_one("a[href]")
        )
        if not link:
            return None
        url = link.get("href", "")
        if not url.startswith("http"):
            url = "https://www.apartments.com" + url

        title_el = card.select_one(".property-title, .js-placardTitle, h2.title, [class*=propertyName]")
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = card.select_one(".price-range, .price, [class*=rentPrice], [class*=rent]")
        price = _parse_price(price_el.get_text(strip=True) if price_el else "")

        beds_el = card.select_one("[class*=beds], [class*=bedroom]")
        baths_el = card.select_one("[class*=baths], [class*=bathroom]")
        beds = _parse_beds(beds_el.get_text(strip=True) if beds_el else "")
        baths = _parse_baths(baths_el.get_text(strip=True) if baths_el else "")

        addr_el = card.select_one(".property-address, address, [class*=address]")
        address = addr_el.get_text(strip=True) if addr_el else ""

        img_el = card.select_one("img")
        image_url = None
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src")

        return {
            "url": url,
            "title": title,
            "price": price,
            "bedrooms": beds,
            "bathrooms": baths,
            "address": address,
            "zipcode": zipcode,
            "lat": None, "lng": None,
            "pets_allowed": None,
            "available_date": None,
            "source": "apartments_com",
            "image_url": image_url,
            "description": None,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_price(text: str) -> int | None:
    text = text.replace(",", "").replace("+", "")
    m = re.search(r"\$?([\d]+)", text)
    return int(m.group(1)) if m else None


def _parse_beds(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:bed|bd)", text, re.I)
    if m:
        return int(m.group(1))
    if re.search(r"studio", text, re.I):
        return 0
    return None


def _parse_baths(text: str) -> float | None:
    m = re.search(r"([\d.]+)\s*(?:bath|ba)", text, re.I)
    return float(m.group(1)) if m else None
