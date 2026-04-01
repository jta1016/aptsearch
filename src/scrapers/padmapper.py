"""
PadMapper rental scraper.

PadMapper exposes a server-rendered Redux state on box-based search pages, so we
search by geographic bounding box and then apply the user's filters locally.
"""
import json
from typing import Any

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

GEOCODE_HEADERS = {
    "User-Agent": "aptsearch/1.0",
    "Accept-Language": "en-US,en;q=0.9",
}

NYC_DEFAULT_BOX = (-74.05, 40.68, -73.85, 40.88)
ZIP_BOX_RADIUS = 0.02
NEIGHBORHOOD_BOX_RADIUS = 0.02


class PadmapperScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        listings: list[dict] = []

        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            boxes = await self._build_boxes(client)
            for box in boxes:
                try:
                    results = await self._fetch_box(client, box)
                    listings.extend(results)
                except Exception as e:
                    print(f"[padmapper] error for box {box}: {e}")

        seen = set()
        unique = []
        for listing in listings:
            key = f"{listing.get('url')}|{listing.get('price')}|{listing.get('bedrooms')}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(listing)
        return unique

    async def _build_boxes(self, client: httpx.AsyncClient) -> list[tuple[float, float, float, float]]:
        boxes: list[tuple[float, float, float, float]] = []

        for zipcode in self.criteria.get("zipcodes") or []:
            box = await self._zip_box(client, zipcode)
            if box:
                boxes.append(box)

        for neighborhood in self.criteria.get("neighborhoods") or []:
            box = await self._neighborhood_box(client, neighborhood)
            if box:
                boxes.append(box)

        if boxes:
            return boxes
        return [NYC_DEFAULT_BOX]

    async def _zip_box(
        self, client: httpx.AsyncClient, zipcode: str
    ) -> tuple[float, float, float, float] | None:
        resp = await client.get(f"https://api.zippopotam.us/us/{zipcode}")
        if resp.status_code != 200:
            print(f"[padmapper] zip geocode HTTP {resp.status_code} for {zipcode}")
            return None

        places = resp.json().get("places") or []
        if not places:
            return None

        lat = float(places[0]["latitude"])
        lng = float(places[0]["longitude"])
        return (
            lng - ZIP_BOX_RADIUS,
            lat - ZIP_BOX_RADIUS,
            lng + ZIP_BOX_RADIUS,
            lat + ZIP_BOX_RADIUS,
        )

    async def _neighborhood_box(
        self, client: httpx.AsyncClient, neighborhood: str
    ) -> tuple[float, float, float, float] | None:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": f"{neighborhood}, New York, NY",
                "format": "jsonv2",
                "limit": 1,
            },
            headers=GEOCODE_HEADERS,
        )
        if resp.status_code != 200:
            print(f"[padmapper] neighborhood geocode HTTP {resp.status_code} for {neighborhood}")
            return None

        results = resp.json() or []
        if not results:
            return None

        lat = float(results[0]["lat"])
        lng = float(results[0]["lon"])
        return (
            lng - NEIGHBORHOOD_BOX_RADIUS,
            lat - NEIGHBORHOOD_BOX_RADIUS,
            lng + NEIGHBORHOOD_BOX_RADIUS,
            lat + NEIGHBORHOOD_BOX_RADIUS,
        )

    async def _fetch_box(
        self, client: httpx.AsyncClient, box: tuple[float, float, float, float]
    ) -> list[dict]:
        url = self._search_url(box)
        resp = await client.get(url)
        print(f"[padmapper] page GET HTTP {resp.status_code} for {url}")
        if resp.status_code != 200:
            return []

        state = _extract_preloaded_state(resp.text)
        if not state:
            print(f"[padmapper] __PRELOADED_STATE__ missing for {url}")
            return []

        items = (
            state.get("currentSearch", {})
            .get("listables", {})
            .get("listables")
            or []
        )
        print(f"[padmapper] preloaded state returned {len(items)} listings for {url}")

        listings = []
        for item in items:
            listing = self._map_item(item)
            if listing and self._within_box(listing, box) and self._matches_criteria(listing, item):
                listings.append(listing)
        return listings

    def _search_url(self, box: tuple[float, float, float, float]) -> str:
        min_lng, min_lat, max_lng, max_lat = box
        return (
            "https://www.padmapper.com/apartments/new-york-ny"
            f"?box={min_lng:.6f},{min_lat:.6f},{max_lng:.6f},{max_lat:.6f}"
        )

    def _map_item(self, item: dict[str, Any]) -> dict | None:
        raw_url = item.get("padmapper_url") or item.get("url")
        if not raw_url:
            return None
        url = raw_url if raw_url.startswith("http") else f"https://www.padmapper.com{raw_url}"

        address_parts = [item.get("address"), item.get("city"), item.get("state")]
        address = ", ".join(part for part in address_parts if part)

        min_price = _as_int(item.get("min_price"))
        max_price = _as_int(item.get("max_price"))
        min_beds = _as_int(item.get("min_bedrooms"))
        max_beds = _as_int(item.get("max_bedrooms"))
        min_baths = _as_float(item.get("min_bathrooms"))
        max_baths = _as_float(item.get("max_bathrooms"))

        title = (
            item.get("building_name")
            or item.get("address")
            or item.get("neighborhood_name")
            or item.get("agent_name")
            or "PadMapper listing"
        )

        return {
            "url": url,
            "title": title,
            "price": min_price or max_price,
            "bedrooms": min_beds if min_beds == max_beds else min_beds,
            "bathrooms": min_baths if min_baths == max_baths else min_baths,
            "address": address,
            "zipcode": None,
            "lat": _as_float(item.get("lat")),
            "lng": _as_float(item.get("lng")),
            "pets_allowed": bool(item.get("pets")),
            "available_date": item.get("date_available"),
            "date_listed": item.get("listed_on") or item.get("created_on") or item.get("modified_on"),
            "source": "padmapper",
            "image_url": None,
            "description": item.get("neighborhood_name"),
        }

    def _matches_criteria(self, listing: dict, raw_item: dict[str, Any]) -> bool:
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        item_min_price = _as_int(raw_item.get("min_price"))
        item_max_price = _as_int(raw_item.get("max_price"))
        if min_price is not None and item_max_price is not None and item_max_price < min_price:
            return False
        if max_price is not None and item_min_price is not None and item_min_price > max_price:
            return False

        item_min_beds = _as_int(raw_item.get("min_bedrooms"))
        item_max_beds = _as_int(raw_item.get("max_bedrooms"))
        wanted_min_beds = self.criteria.get("min_bedrooms")
        wanted_max_beds = self.criteria.get("max_bedrooms")
        if wanted_min_beds is not None and item_max_beds is not None and item_max_beds < wanted_min_beds:
            return False
        if wanted_max_beds is not None and item_min_beds is not None and item_min_beds > wanted_max_beds:
            return False

        item_min_baths = _as_float(raw_item.get("min_bathrooms"))
        if self.criteria.get("min_bathrooms") is not None and item_min_baths is not None:
            if item_min_baths < float(self.criteria["min_bathrooms"]):
                return False

        if self.criteria.get("pets_allowed") and not listing.get("pets_allowed"):
            return False

        return True

    def _within_box(
        self, listing: dict, box: tuple[float, float, float, float]
    ) -> bool:
        lat = listing.get("lat")
        lng = listing.get("lng")
        if lat is None or lng is None:
            return True
        min_lng, min_lat, max_lng, max_lat = box
        return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def _extract_preloaded_state(html: str) -> dict[str, Any] | None:
    marker = "window.__PRELOADED_STATE__ = "
    start = html.find(marker)
    if start == -1:
        return None

    idx = start + len(marker)
    depth = 0
    in_string = False
    escaped = False
    begin = None

    for pos, ch in enumerate(html[idx:], start=idx):
        if begin is None:
            if ch.isspace():
                continue
            if ch != "{":
                return None
            begin = pos
            depth = 1
            continue

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[begin:pos + 1])
                except json.JSONDecodeError:
                    return None

    return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
