"""
Realtor.com scraper using Playwright to intercept their internal GraphQL API.
Direct API calls get 403'd — navigating a real browser session works.
"""
import re
from playwright.async_api import async_playwright


class RealtorScraper:
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
                    print(f"[realtor] error for {url}: {e}")

            await browser.close()

        seen = set()
        unique = []
        for l in listings:
            if l["url"] not in seen:
                seen.add(l["url"])
                unique.append(l)
        return unique

    def _build_urls(self) -> list[str]:
        zipcodes = self.criteria.get("zipcodes", [])
        neighborhoods = self.criteria.get("neighborhoods", [])

        urls = []
        for z in zipcodes:
            urls.append(f"https://www.realtor.com/apartments/{z}/")
        for n in neighborhoods:
            slug = n.replace(" ", "_")
            urls.append(f"https://www.realtor.com/apartments/New_York_NY/{slug}/")

        if not urls:
            urls.append("https://www.realtor.com/apartments/New_York_NY/")

        return urls

    async def _scrape_page(self, context, url: str) -> list[dict]:
        captured = []
        page = await context.new_page()

        async def handle_response(response):
            if "realtor.com/api/v1/rdc_search_srp" in response.url or "realtor.com/api/v1/hulk" in response.url:
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
        results = (
            data.get("data", {}).get("home_search", {}).get("results", [])
            or data.get("data", {}).get("homes", [])
            or []
        )
        return [r for r in (self._parse_item(i) for i in results if i) if r]

    def _parse_item(self, item: dict) -> dict | None:
        href = item.get("href", "")
        if not href:
            return None
        if not href.startswith("http"):
            href = "https://www.realtor.com" + href

        desc = item.get("description") or {}
        beds = desc.get("beds")
        baths_full = desc.get("baths_full") or 0
        baths_half = desc.get("baths_half") or 0
        baths = baths_full + (0.5 * baths_half) if (baths_full or baths_half) else None

        loc = item.get("location") or {}
        addr = loc.get("address") or {}
        coord = addr.get("coordinate") or {}
        lat = coord.get("lat")
        lng = coord.get("lon")
        address = " ".join(filter(None, [
            addr.get("line", ""),
            addr.get("city", ""),
            addr.get("state_code", ""),
        ]))
        zipcode = addr.get("postal_code", "")

        pet_policy = item.get("pet_policy") or {}
        pets = None
        if pet_policy.get("pets_allowed") is True or pet_policy.get("cats") or pet_policy.get("dogs"):
            pets = True
        elif pet_policy.get("pets_allowed") is False:
            pets = False

        photos = item.get("photos") or []
        img = photos[0].get("href") if photos else None

        return {
            "url": href,
            "title": address or "Realtor.com Listing",
            "price": item.get("list_price"),
            "bedrooms": int(beds) if beds is not None else None,
            "bathrooms": float(baths) if baths is not None else None,
            "address": address,
            "zipcode": str(zipcode),
            "lat": lat,
            "lng": lng,
            "pets_allowed": pets,
            "available_date": item.get("list_date"),
            "source": "realtor",
            "image_url": img,
            "description": desc.get("text"),
        }
