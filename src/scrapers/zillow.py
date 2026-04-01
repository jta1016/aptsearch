"""
Zillow rental scraper.
Fetches search page and extracts listings from __NEXT_DATA__ JSON.
No browser required — uses httpx only.
"""
import re
import json
import httpx
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from browser_fetch import fetch_page_html

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
        if resp.status_code == 200:
            return self._parse(resp.text)

        print(f"[zillow] HTTP {resp.status_code} for {url}")
        try:
            html = await fetch_page_html(url)
            results = self._parse(html)
            if results:
                print(f"[zillow] Playwright fallback returned {len(results)} listings for {url}")
            return results
        except Exception as e:
            print(f"[zillow] Playwright fallback failed for {url}: {e}")
            return []

    def _parse(self, html: str) -> list[dict]:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                print("[zillow] failed to parse __NEXT_DATA__ JSON")
            else:
                list_results = self._extract_list_results(data)
                if list_results is not None:
                    listings = []
                    for item in list_results:
                        try:
                            listings.extend(self._expand_item(item))
                        except Exception:
                            continue
                    if listings:
                        return listings
                print("[zillow] could not find listResults in __NEXT_DATA__")
        else:
            print("[zillow] __NEXT_DATA__ script tag not found")

        listings = self._parse_html_cards(html)
        if listings:
            print(f"[zillow] HTML card parser returned {len(listings)} listings")
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

    def _parse_html_cards(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        listings: list[dict] = []
        seen_urls: set[str] = set()

        for anchor in soup.select('a[href]'):
            link_text = anchor.get_text(" ", strip=True)
            href = anchor.get("href", "").strip()
            if not href or href.startswith("#"):
                continue
            if link_text.startswith("$"):
                continue
            if link_text in {"Save", "More", "Check availability", "Previous page", "Next page"}:
                continue
            if "Page " in link_text or "Apartments for Rent" in link_text or "Houses for Rent" in link_text:
                continue
            if "|" not in link_text and not self._looks_like_listing_address(link_text):
                continue

            container = self._listing_container(anchor)
            if container is None:
                continue

            block_text = container.get_text(" ", strip=True)
            if "$" not in block_text:
                continue

            url = urljoin("https://www.zillow.com", href)
            if not self._looks_like_listing_url(url):
                continue
            if url in seen_urls:
                continue

            parsed = self._parse_html_listing(url, link_text, block_text, container)
            if not parsed:
                continue

            added = False
            for listing in parsed:
                listing_url = listing.get("url")
                dedupe_key = f"{listing_url}|{listing.get('bedrooms')}|{listing.get('price')}"
                if dedupe_key in seen_urls:
                    continue
                seen_urls.add(dedupe_key)
                listings.append(listing)
                added = True

            if added:
                seen_urls.add(url)

        return listings

    def _listing_container(self, anchor):
        node = anchor
        for _ in range(6):
            node = node.parent
            if node is None:
                return None
            text = node.get_text(" ", strip=True)
            if "$" in text and len(text) >= 40:
                return node
        return anchor.parent

    def _parse_html_listing(self, url: str, link_text: str, block_text: str, container) -> list[dict]:
        title, address = self._split_title_address(link_text)
        zipcode = self._extract_zipcode(address or block_text)
        image = None
        image_el = container.select_one("img")
        if image_el:
            image = image_el.get("src") or image_el.get("data-src")

        unit_matches = []
        for match in re.finditer(r"(\$[\d,]+(?:\+|/mo)?)\s*(Studio|\d+\s*bd\+?|\d+\s*bd)?", block_text, re.I):
            price_text = match.group(1)
            bed_text = match.group(2)
            beds = 0 if bed_text and bed_text.lower().startswith("studio") else _parse_beds(bed_text or "")
            unit_matches.append((_parse_price(price_text), beds))

        listings: list[dict] = []
        for price, beds in unit_matches:
            if not self._bedroom_matches(beds):
                continue
            listing_title = title or address or link_text
            if beds is not None and "|" in link_text:
                listing_title = f"{listing_title} - {beds}bd" if beds > 0 else f"{listing_title} - Studio"
            listings.append({
                "url": url,
                "title": listing_title,
                "price": price,
                "bedrooms": beds,
                "bathrooms": _parse_baths(block_text),
                "address": address or link_text,
                "zipcode": zipcode,
                "lat": None,
                "lng": None,
                "pets_allowed": None,
                "available_date": None,
                "source": "zillow",
                "image_url": image,
                "description": None,
            })

        if listings:
            return listings

        price = _parse_price(block_text)
        beds = _parse_beds(block_text)
        if not self._bedroom_matches(beds):
            return []

        return [{
            "url": url,
            "title": title or address or link_text,
            "price": price,
            "bedrooms": beds,
            "bathrooms": _parse_baths(block_text),
            "address": address or link_text,
            "zipcode": zipcode,
            "lat": None,
            "lng": None,
            "pets_allowed": None,
            "available_date": None,
            "source": "zillow",
            "image_url": image,
            "description": None,
        }]

    def _split_title_address(self, link_text: str) -> tuple[str, str]:
        if " | " in link_text:
            title, address = link_text.split(" | ", 1)
            return title.strip(), address.strip()
        if self._looks_like_listing_address(link_text):
            return link_text.strip(), link_text.strip()
        return link_text.strip(), ""

    def _bedroom_matches(self, beds: int | None) -> bool:
        min_beds = self.criteria.get("min_bedrooms")
        max_beds = self.criteria.get("max_bedrooms")
        if min_beds is not None and beds is not None and beds < min_beds:
            return False
        if max_beds is not None and beds is not None and beds > max_beds:
            return False
        return True

    def _looks_like_listing_url(self, url: str) -> bool:
        return any(
            token in url
            for token in ("/b/", "/homedetails/", "_zpid", "/apartments/", "/community/")
        )

    def _looks_like_listing_address(self, text: str) -> bool:
        return bool(re.search(r"\d", text) and "," in text)

    def _extract_zipcode(self, text: str) -> str | None:
        match = re.search(r"\b(\d{5})(?:-\d{4})?\b", text or "")
        return match.group(1) if match else None


def _parse_price(text: str) -> int | None:
    text = text.replace(",", "").replace("+", "")
    m = re.search(r"\$?([\d]+)", text)
    return int(m.group(1)) if m else None


def _parse_beds(text: str) -> int | None:
    if re.search(r"studio", text, re.I):
        return 0
    match = re.search(r"(\d+)\s*bd", text, re.I)
    return int(match.group(1)) if match else None


def _parse_baths(text: str) -> float | None:
    match = re.search(r"([\d.]+)\s*ba", text, re.I)
    return float(match.group(1)) if match else None
