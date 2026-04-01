"""
Apartments.com scraper using Playwright to intercept their internal search API.
Direct API calls get 403'd — navigating a real browser session works.
"""
import re
from playwright.async_api import async_playwright


class ApartmentsComScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        listings = []
        urls = self._build_urls()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )

            for url in urls:
                try:
                    results = await self._scrape_page(context, url)
                    listings.extend(results)
                except Exception as e:
                    print(f"[apartments.com] error for {url}: {e}")

            await browser.close()

        seen = set()
        unique = []
        for l in listings:
            if l["url"] not in seen:
                seen.add(l["url"])
                unique.append(l)
        return unique

    def _build_urls(self) -> list[str]:
        suffix = self._build_filter_suffix()
        zipcodes = self.criteria.get("zipcodes", [])
        neighborhoods = self.criteria.get("neighborhoods", [])

        urls = []
        for z in zipcodes:
            urls.append(f"https://www.apartments.com/new-york-ny-{z}/{suffix}")
        for n in neighborhoods:
            slug = n.lower().replace(" ", "-")
            urls.append(f"https://www.apartments.com/{slug}-new-york-ny/{suffix}")

        if not urls:
            urls.append(f"https://www.apartments.com/new-york-ny/{suffix}")

        return urls

    def _build_filter_suffix(self) -> str:
        parts = []
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        if min_price or max_price:
            lo = min_price or 0
            hi = max_price or 999999
            parts.append(f"{lo}-to-{hi}")
        min_beds = self.criteria.get("min_bedrooms")
        if min_beds:
            parts.append(f"{min_beds}br")
        if self.criteria.get("pets_allowed"):
            parts.append("pet-friendly")
        return "/".join(parts) + "/" if parts else ""

    async def _scrape_page(self, context, url: str) -> list[dict]:
        captured = []
        page = await context.new_page()

        async def handle_response(response):
            if "apartments.com/services/search" in response.url:
                try:
                    data = await response.json()
                    captured.append(data)
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
        except Exception:
            pass

        await page.close()

        listings = []
        for data in captured:
            listings.extend(self._parse(data))
        return listings

    def _parse(self, data: dict) -> list[dict]:
        items = (
            data.get("items")
            or data.get("results")
            or data.get("data", {}).get("items", [])
            or []
        )
        listings = []
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
