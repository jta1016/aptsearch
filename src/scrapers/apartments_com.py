"""
Apartments.com scraper using their internal search API.
"""
import json
import re
import httpx
from bs4 import BeautifulSoup

SEARCH_URL = "https://www.apartments.com/services/search/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.apartments.com/",
    "Origin": "https://www.apartments.com",
    "Content-Type": "application/json",
}


class ApartmentsComScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        listings = []
        targets = self._build_targets()

        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            for params in targets:
                try:
                    results = await self._search(client, params)
                    listings.extend(results)
                except Exception as e:
                    print(f"[apartments.com] error: {e}")

        seen = set()
        unique = []
        for l in listings:
            if l["url"] not in seen:
                seen.add(l["url"])
                unique.append(l)
        return unique

    def _build_targets(self) -> list[dict]:
        base = {
            "ptAmenities": [],
            "coAmenities": [],
        }
        if self.criteria.get("min_price"):
            base["minRent"] = self.criteria["min_price"]
        if self.criteria.get("max_price"):
            base["maxRent"] = self.criteria["max_price"]
        if self.criteria.get("min_bedrooms") is not None:
            base["minBeds"] = self.criteria["min_bedrooms"]
        if self.criteria.get("max_bedrooms") is not None:
            base["maxBeds"] = self.criteria["max_bedrooms"]
        if self.criteria.get("min_bathrooms"):
            base["minBaths"] = self.criteria["min_bathrooms"]
        if self.criteria.get("pets_allowed"):
            base["ptAmenities"].append("petsAllowed")

        zipcodes = self.criteria.get("zipcodes", [])
        neighborhoods = self.criteria.get("neighborhoods", [])

        targets = []
        for z in zipcodes:
            targets.append({**base, "geography": {"location": z, "type": "postalCode"}})
        for n in neighborhoods:
            targets.append({**base, "geography": {"location": f"{n}, New York, NY", "type": "locality"}})

        if not targets:
            targets.append({**base, "geography": {"location": "New York, NY", "type": "locality"}})

        return targets

    async def _search(self, client: httpx.AsyncClient, payload: dict) -> list[dict]:
        resp = await client.post(SEARCH_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return self._parse(data)

    def _parse(self, data: dict) -> list[dict]:
        listings = []
        items = data.get("items", []) or data.get("results", [])
        for item in items:
            try:
                l = self._parse_item(item)
                if l:
                    listings.append(l)
            except Exception:
                continue
        return listings

    def _parse_item(self, item: dict) -> dict | None:
        url = item.get("listingUrl") or item.get("url") or item.get("propertyUrl", "")
        if not url:
            return None
        if not url.startswith("http"):
            url = "https://www.apartments.com" + url

        price = None
        rent = item.get("rentRange") or item.get("minRent") or item.get("maxRent")
        if isinstance(rent, dict):
            price = rent.get("min") or rent.get("max")
        elif isinstance(rent, (int, float)):
            price = int(rent)
        elif isinstance(rent, str):
            price = _parse_price(rent)

        beds = item.get("beds") or item.get("minBeds")
        baths = item.get("baths") or item.get("minBaths")

        geo = item.get("geography") or {}
        lat = geo.get("latitude") or item.get("latitude")
        lng = geo.get("longitude") or item.get("longitude")

        address = item.get("address") or item.get("formattedAddress") or ""
        zipcode = item.get("postalCode") or item.get("zipCode") or ""

        pets = None
        amenities = item.get("amenities", []) or []
        if isinstance(amenities, list):
            if "petsAllowed" in amenities or "Pets Allowed" in amenities:
                pets = True
            elif "noPets" in amenities:
                pets = False

        photos = item.get("photos") or item.get("images") or []
        img = photos[0] if photos and isinstance(photos[0], str) else None

        return {
            "url": url,
            "title": item.get("propertyName") or address or "Apartments.com Listing",
            "price": price,
            "bedrooms": int(beds) if beds is not None else None,
            "bathrooms": float(baths) if baths is not None else None,
            "address": address,
            "zipcode": str(zipcode),
            "lat": lat,
            "lng": lng,
            "pets_allowed": pets,
            "available_date": item.get("availableDate"),
            "source": "apartments_com",
            "image_url": img,
            "description": item.get("description"),
        }


def _parse_price(text: str) -> int | None:
    m = re.search(r"[\d,]+", text.replace(",", ""))
    return int(m.group()) if m else None
