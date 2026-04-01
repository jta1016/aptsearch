"""
Zillow rental scraper.
Uses Zillow's internal XHR search API (GetSearchPageState.htm) instead of
scraping the HTML page, which is blocked by PerimeterX in cloud environments.
The XHR endpoint returns clean JSON and has historically been less protected
than the HTML page.
"""
import json
import re
from urllib.parse import urlencode

import httpx
from apify import Actor
from proxy_support import get_proxy_url

# Headers that mimic the XHR request the browser makes from within a Zillow page.
# Sec-Fetch-Mode: cors + Sec-Fetch-Site: same-origin is the key difference
# from a full-page load — it signals this is an internal AJAX request.
XHR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
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

        if zipcodes:
            targets = [("zip", z) for z in zipcodes]
        else:
            targets = [("slug", s) for s in self._get_slugs()]

        for kind, value in targets:
            try:
                results = await self._fetch(kind, value)
                listings.extend(results)
            except Exception as e:
                print(f"[zillow] error for {kind}={value}: {e}")

        seen = set()
        unique = []
        for listing in listings:
            key = listing["url"] + str(listing.get("bedrooms"))
            if key not in seen:
                seen.add(key)
                unique.append(listing)
        return unique

    async def _fetch(self, kind: str, value: str) -> list[dict]:
        xhr_url = self._xhr_url(kind, value)
        referer = self._page_url(kind, value)
        headers = {**XHR_HEADERS, "Referer": referer}

        proxy_url = await get_proxy_url("zillow", session_id=f"zillow-{value}")

        try:
            if proxy_url:
                async with httpx.AsyncClient(
                    headers=headers, timeout=30, follow_redirects=True, proxy=proxy_url
                ) as client:
                    resp = await client.get(xhr_url)
            else:
                async with httpx.AsyncClient(
                    headers=headers, timeout=30, follow_redirects=True
                ) as client:
                    resp = await client.get(xhr_url)
        except Exception as e:
            print(f"[zillow] request failed for {value}: {e}")
            return []

        print(f"[zillow] XHR HTTP {resp.status_code} for {value}")

        if resp.status_code != 200:
            preview = resp.text[:500].replace("\n", " ")
            print(f"[zillow] non-200 body preview: {preview}")
            return []

        try:
            data = resp.json()
        except Exception:
            preview = resp.text[:300].replace("\n", " ")
            print(f"[zillow] failed to parse JSON — body preview: {preview}")
            return []

        # XHR response is the searchPageState directly (no props.pageProps wrapper).
        # Structure: {"cat1": {"searchResults": {"listResults": [...]}}}
        list_results = (
            data.get("cat1", {})
                .get("searchResults", {})
                .get("listResults")
        )
        if not isinstance(list_results, list):
            # Try mapResults as fallback
            list_results = (
                data.get("cat1", {})
                    .get("searchResults", {})
                    .get("mapResults")
            )
        if not isinstance(list_results, list):
            print(f"[zillow] no listResults in XHR response for {value}")
            Actor.log.debug(f"[zillow] XHR response keys: {list(data.keys())}")
            return []

        listings = []
        for item in list_results:
            try:
                listings.extend(self._expand_item(item))
            except Exception:
                continue

        print(f"[zillow] parsed {len(listings)} listings for {value}")
        return listings

    def _xhr_url(self, kind: str, value: str) -> str:
        """Build the GetSearchPageState XHR URL."""
        search_query_state = {
            "pagination": {"currentPage": 1},
            "usersSearchTerm": value if kind == "zip" else value.replace("-", " ").title(),
            "filterState": self._filter_state(),
            "isListVisible": True,
            "isMapVisible": False,
        }
        wants = {"cat1": ["listResults", "mapResults"], "cat2": ["total"]}
        params = {
            "searchQueryState": json.dumps(search_query_state, separators=(",", ":")),
            "wants": json.dumps(wants, separators=(",", ":")),
            "requestId": "2",
        }
        return "https://www.zillow.com/search/GetSearchPageState.htm?" + urlencode(params)

    def _filter_state(self) -> dict:
        """Build Zillow filterState for a rental search with optional price/bed criteria."""
        state: dict = {
            "fr": {"value": True},    # for rent
            "fsba": {"value": False},  # not for sale by agent
            "fsbo": {"value": False},  # not for sale by owner
            "nc": {"value": False},    # not new construction sale
            "cmsn": {"value": False},  # not coming soon
            "auc": {"value": False},   # not auction
            "fore": {"value": False},  # not foreclosure
        }

        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        if min_price is not None or max_price is not None:
            mp: dict = {}
            if min_price is not None:
                mp["min"] = min_price
            if max_price is not None:
                mp["max"] = max_price
            state["mp"] = mp  # monthly price (rental)

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

    def _page_url(self, kind: str, value: str) -> str:
        """The HTML page URL used as the Referer header."""
        if kind == "zip":
            return f"https://www.zillow.com/homes/for_rent/{value}_rb/"
        return f"https://www.zillow.com/{value}/rentals/"

    def _get_slugs(self) -> list[str]:
        neighborhoods = self.criteria.get("neighborhoods", [])
        if neighborhoods:
            return [n.lower().replace(" ", "-") + "-new-york-ny" for n in neighborhoods]
        return [DEFAULT_SLUG]

    def _expand_item(self, item: dict) -> list[dict]:
        """Buildings have a units array — expand into one listing per unit."""
        base_url = item.get("detailUrl", "")
        if not base_url:
            return []
        if not base_url.startswith("http"):
            base_url = "https://www.zillow.com" + base_url

        address = item.get("address", "")
        zipcode = item.get("addressZipcode", "") or _extract_zipcode(address) or None
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
            beds = int(beds_raw) if beds_raw is not None else None
            if not self._bedroom_matches(beds):
                return []
            return [{
                "url": base_url,
                "title": building_name or address,
                "price": price,
                "bedrooms": beds,
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
            if not self._bedroom_matches(beds):
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

    def _bedroom_matches(self, beds: int | None) -> bool:
        min_beds = self.criteria.get("min_bedrooms")
        max_beds = self.criteria.get("max_bedrooms")
        if min_beds is not None and beds is not None and beds < min_beds:
            return False
        if max_beds is not None and beds is not None and beds > max_beds:
            return False
        return True


def _parse_price(text: str) -> int | None:
    text = text.replace(",", "").replace("+", "")
    m = re.search(r"\$?([\d]+)", text)
    return int(m.group(1)) if m else None


def _extract_zipcode(text: str) -> str | None:
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", text or "")
    return match.group(1) if match else None
