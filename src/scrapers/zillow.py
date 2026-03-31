"""
Zillow scraper using Playwright to intercept their internal search API.
Zillow is heavily anti-bot — Apify proxies are required for reliable results.
"""
import json
import re
from playwright.async_api import async_playwright, Page

SEARCH_URL = "https://www.zillow.com/homes/for_rent/{zipcode}_rb/"
FALLBACK_URL = "https://www.zillow.com/homes/for_rent/New-York-NY/"


class ZillowScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        listings = []
        zipcodes = self.criteria.get("zipcodes") or [None]

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )

            for zipcode in zipcodes:
                try:
                    results = await self._scrape_zip(context, zipcode)
                    listings.extend(results)
                except Exception as e:
                    print(f"[zillow] error for zip {zipcode}: {e}")

            await browser.close()

        return listings

    async def _scrape_zip(self, context, zipcode: str | None) -> list[dict]:
        captured = []
        page = await context.new_page()

        # Intercept the internal search API response
        async def handle_response(response):
            if "GetSearchPageState" in response.url or "search/GetSearchPageState" in response.url:
                try:
                    data = await response.json()
                    captured.append(data)
                except Exception:
                    pass

        page.on("response", handle_response)

        url = SEARCH_URL.format(zipcode=zipcode) if zipcode else FALLBACK_URL
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
        except Exception:
            pass

        await page.close()

        if not captured:
            return []

        return self._parse_api_response(captured[0])

    def _parse_api_response(self, data: dict) -> list[dict]:
        listings = []
        try:
            results = (
                data.get("cat1", {}).get("searchResults", {}).get("listResults", [])
                or data.get("cat1", {}).get("searchResults", {}).get("mapResults", [])
            )
        except AttributeError:
            return []

        for item in results:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                continue

        return listings

    def _parse_item(self, item: dict) -> dict | None:
        url = item.get("detailUrl", "")
        if url and not url.startswith("http"):
            url = "https://www.zillow.com" + url

        price_raw = item.get("price", "") or item.get("unformattedPrice", "")
        price = _parse_price(str(price_raw))

        beds_raw = item.get("beds") or item.get("minBeds")
        baths_raw = item.get("baths") or item.get("minBaths")

        lat = item.get("latLong", {}).get("latitude") if item.get("latLong") else None
        lng = item.get("latLong", {}).get("longitude") if item.get("latLong") else None

        address_parts = [
            item.get("address", ""),
            item.get("addressCity", ""),
            item.get("addressState", ""),
        ]
        address = ", ".join(p for p in address_parts if p)
        zipcode = item.get("addressZipcode", "")

        img = None
        if item.get("imgSrc"):
            img = item["imgSrc"]

        return {
            "url": url,
            "title": item.get("address", "Zillow Listing"),
            "price": price,
            "bedrooms": int(beds_raw) if beds_raw is not None else None,
            "bathrooms": float(baths_raw) if baths_raw is not None else None,
            "address": address,
            "zipcode": zipcode,
            "lat": lat,
            "lng": lng,
            "pets_allowed": None,  # not surfaced in search results
            "available_date": None,
            "source": "zillow",
            "image_url": img,
            "description": item.get("hdpData", {}).get("homeInfo", {}).get("description") if item.get("hdpData") else None,
        }


def _parse_price(text: str) -> int | None:
    m = re.search(r"[\d,]+", text.replace(",", ""))
    return int(m.group()) if m else None
