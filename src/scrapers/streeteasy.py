"""
StreetEasy rental scraper.

StreetEasy search pages are server-rendered enough to extract listing cards from
HTML, so this scraper builds neighborhood-based search URLs and parses the
visible search results.
"""
import re
from urllib.parse import quote, urljoin

import httpx
from apify import Actor
from bs4 import BeautifulSoup
from browser_fetch import fetch_page_artifacts
from proxy_support import get_proxy_url


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

ZIP_TO_AREA = {
    "10001": "chelsea",
    "10002": "lower-east-side",
    "10003": "east-village",
    "10007": "tribeca",
    "10009": "east-village",
    "10010": "gramercy-park",
    "10011": "chelsea",
    "10012": "soho",
    "10013": "soho",
    "10014": "west-village",
    "10016": "murray-hill",
    "10017": "midtown-east",
    "10018": "hells-kitchen",
    "10019": "midtown-west",
    "10021": "upper-east-side",
    "10022": "midtown-east",
    "10023": "upper-west-side",
    "10024": "upper-west-side",
    "10025": "upper-west-side",
    "10026": "harlem",
    "10027": "harlem",
    "10028": "upper-east-side",
    "10029": "east-harlem",
    "10030": "harlem",
    "10031": "harlem",
    "10032": "washington-heights",
    "10033": "washington-heights",
    "10034": "inwood",
    "10036": "hells-kitchen",
    "10037": "harlem",
    "10038": "financial-district",
    "10040": "inwood",
    "10065": "upper-east-side",
    "10075": "upper-east-side",
    "10128": "upper-east-side",
    "10280": "battery-park-city",
    "11101": "long-island-city",
    "11102": "astoria",
    "11103": "astoria",
    "11104": "sunnyside",
    "11105": "astoria",
    "11106": "astoria",
    "11201": "brooklyn-heights",
    "11205": "fort-greene",
    "11206": "williamsburg",
    "11211": "williamsburg",
    "11215": "park-slope",
    "11217": "boerum-hill",
    "11221": "bushwick",
    "11222": "greenpoint",
    "11225": "crown-heights",
    "11231": "carroll-gardens",
    "11237": "bushwick",
    "11238": "prospect-heights",
    "11377": "woodside",
    "11385": "ridgewood",
}

DEFAULT_AREA = "nyc"


class StreetEasyScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        listings: list[dict] = []

        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            for url in self._build_urls():
                try:
                    results = await self._fetch_page(client, url)
                    listings.extend(results)
                except Exception as exc:
                    print(f"[streeteasy] error for {url}: {exc}")

        seen = set()
        unique = []
        for listing in listings:
            key = f"{listing.get('url')}|{listing.get('price')}|{listing.get('bedrooms')}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(listing)
        return unique

    def _build_urls(self) -> list[str]:
        areas: list[str] = []

        for zipcode in self.criteria.get("zipcodes") or []:
            area = ZIP_TO_AREA.get(zipcode)
            if area:
                areas.append(area)

        for neighborhood in self.criteria.get("neighborhoods") or []:
            areas.append(
                neighborhood.lower().replace(",", "").replace(" ", "-")
            )

        if not areas:
            areas = [DEFAULT_AREA]

        deduped = list(dict.fromkeys(areas))
        return [self._search_url(area) for area in deduped]

    def _search_url(self, area: str) -> str:
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        min_beds = self.criteria.get("min_bedrooms")
        max_beds = self.criteria.get("max_bedrooms")

        filters: list[str] = []
        if min_price is not None or max_price is not None:
            price_part = f"{min_price or ''}-{max_price or ''}"
            filters.append(f"price:{price_part}")

        if min_beds is not None or max_beds is not None:
            bed_part = f"{min_beds or ''}-{max_beds or ''}"
            filters.append(f"beds:{bed_part}")

        if self.criteria.get("pets_allowed"):
            filters.append("pets:1")

        if min_beds == max_beds == 1:
            prefix = "1-bedroom-apartments-for-rent"
        elif min_beds == max_beds == 2:
            prefix = "2-bedroom-apartments-for-rent"
        elif min_beds == max_beds == 3:
            prefix = "3-bedroom-apartments-for-rent"
        elif min_beds == max_beds == 0:
            prefix = "studio-apartments-for-rent"
        else:
            prefix = "for-rent"

        base = f"https://streeteasy.com/{prefix}/{area}"
        if not filters:
            return base
        return f"{base}/{quote('|'.join(filters), safe='')}"

    async def _fetch_page(self, client: httpx.AsyncClient, url: str) -> list[dict]:
        proxy_url = await get_proxy_url("streeteasy", session_id=self._session_id(url))
        resp = await self._request(client, "GET", url, proxy_url=proxy_url)
        print(f"[streeteasy] page GET HTTP {resp.status_code} for {url}")
        html = resp.text if resp.status_code == 200 else ""
        if resp.status_code != 200:
            artifacts = await fetch_page_artifacts(
                url,
                site_name="streeteasy",
                session_id=self._session_id(url),
            )
            await self._store_browser_artifacts(url, artifacts)
            self._log_browser_artifacts(url, artifacts)
            html = artifacts["html"]

        listings = self._parse_html(html)
        if listings:
            print(f"[streeteasy] parsed {len(listings)} listings for {url}")
            return listings

        await self._store_html_preview(url, html)
        print(f"[streeteasy] no listings parsed for {url}")
        return []

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        proxy_url: str | None = None,
        **kwargs,
    ) -> httpx.Response:
        if not proxy_url:
            return await client.request(method, url, **kwargs)
        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True, proxy=proxy_url) as proxied:
            return await proxied.request(method, url, **kwargs)

    def _parse_html(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")

        listings = self._parse_candidate_containers(soup.select("article, li"))
        if listings:
            return listings

        listings = self._parse_anchor_containers(soup)
        return listings

    def _parse_candidate_containers(self, containers) -> list[dict]:
        listings: list[dict] = []
        for container in containers:
            listing = self._parse_container(container)
            if listing:
                listings.append(listing)
        return listings

    def _parse_anchor_containers(self, soup: BeautifulSoup) -> list[dict]:
        listings: list[dict] = []
        seen = set()

        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "").strip()
            if not self._looks_like_listing_href(href):
                continue
            container = anchor
            for _ in range(5):
                container = container.parent
                if container is None:
                    break
                text = container.get_text(" ", strip=True)
                if "$" in text and (" bed" in text.lower() or "studio" in text.lower()):
                    break
            if container is None:
                continue

            listing = self._parse_container(container)
            if not listing:
                continue
            key = listing.get("url")
            if key in seen:
                continue
            seen.add(key)
            listings.append(listing)

        return listings

    def _parse_container(self, container) -> dict | None:
        text = container.get_text(" ", strip=True)
        lowered = text.lower()
        if "$" not in text:
            return None
        if "listing by" not in lowered and "rental unit" not in lowered and "condo in" not in lowered:
            return None

        link = None
        for anchor in container.select("a[href]"):
            href = anchor.get("href", "").strip()
            if self._looks_like_listing_href(href):
                link = anchor
                break
        if link is None:
            return None

        url = urljoin("https://streeteasy.com", link.get("href", "").strip())
        price = _parse_price(text)
        bedrooms = _parse_beds(text)
        if not self._matches_bedrooms(bedrooms):
            return None

        bathrooms = _parse_baths(text)
        address = self._extract_address(container, text)
        title = address or link.get_text(" ", strip=True) or "StreetEasy listing"
        image = None
        image_el = container.select_one("img")
        if image_el:
            image = image_el.get("src") or image_el.get("data-src")

        if self.criteria.get("min_price") is not None and price is not None and price < self.criteria["min_price"]:
            return None
        if self.criteria.get("max_price") is not None and price is not None and price > self.criteria["max_price"]:
            return None

        return {
            "url": url,
            "title": title,
            "price": price,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "address": address,
            "zipcode": _extract_zipcode(text),
            "lat": None,
            "lng": None,
            "pets_allowed": None,
            "available_date": None,
            "date_listed": None,
            "source": "streeteasy",
            "image_url": image,
            "description": None,
        }

    def _extract_address(self, container, text: str) -> str:
        for anchor in container.select("a[href]"):
            candidate = anchor.get_text(" ", strip=True)
            if _looks_like_address(candidate):
                return candidate

        for string in container.stripped_strings:
            if _looks_like_address(string):
                return string

        match = re.search(r"(\d[\w\s.\-']+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Place|Pl|Lane|Ln|Court|Ct)[^$]*)", text, re.I)
        if match:
            return match.group(1).strip()
        return ""

    def _looks_like_listing_href(self, href: str) -> bool:
        return any(token in href for token in ("/rental/", "/property/", "/building/"))

    def _matches_bedrooms(self, bedrooms: int | None) -> bool:
        min_beds = self.criteria.get("min_bedrooms")
        max_beds = self.criteria.get("max_bedrooms")
        if min_beds is not None and bedrooms is not None and bedrooms < min_beds:
            return False
        if max_beds is not None and bedrooms is not None and bedrooms > max_beds:
            return False
        return True

    async def _store_html_preview(self, url: str, html: str) -> None:
        if not (Actor.is_at_home() or getattr(Actor, "config", None)):
            return
        key_suffix = re.sub(r"[^\w]+", "-", url).strip("-").lower()[:90]
        key = f"streeteasy-preview-{key_suffix}"
        value = {"url": url, "html_preview": html[:4000]}
        try:
            await Actor.set_value(key, value)
            print(f"[streeteasy] stored HTML preview in key-value store: {key}")
        except Exception as exc:
            print(f"[streeteasy] failed to store HTML preview for {url}: {exc}")

    async def _store_browser_artifacts(self, url: str, artifacts: dict) -> None:
        if not (Actor.is_at_home() or getattr(Actor, "config", None)):
            return
        key_suffix = re.sub(r"[^\w]+", "-", url).strip("-").lower()[:90]
        key = f"streeteasy-browser-{key_suffix}"
        value = {
            "url": url,
            "final_url": artifacts.get("final_url"),
            "title": artifacts.get("title"),
            "challenge_signals": artifacts.get("challenge_signals") or [],
            "requests": artifacts.get("requests", [])[:20],
            "responses": artifacts.get("responses", [])[:20],
            "html_preview": (artifacts.get("html") or "")[:3000],
        }
        try:
            await Actor.set_value(key, value)
            print(f"[streeteasy] stored browser artifacts in key-value store: {key}")
        except Exception as exc:
            print(f"[streeteasy] failed to store browser artifacts for {url}: {exc}")

    def _log_browser_artifacts(self, url: str, artifacts: dict) -> None:
        print(
            "[streeteasy] browser summary "
            f"url={url} final_url={artifacts.get('final_url')} "
            f"title={artifacts.get('title')!r} "
            f"challenge_signals={artifacts.get('challenge_signals') or []}"
        )
        for response in artifacts.get("responses", [])[:8]:
            preview = response.get("body_preview")
            print(
                "[streeteasy] browser response "
                f"status={response.get('status')} "
                f"type={response.get('resource_type')} "
                f"url={response.get('url')} "
                f"content_type={response.get('content_type')}"
            )
            if preview:
                print(f"[streeteasy] browser body preview: {preview}")

    def _session_id(self, url: str) -> str:
        return re.sub(r"[^\w]+", "_", url)[:50]


def _parse_price(text: str) -> int | None:
    match = re.search(r"\$([\d,]+)", text.replace(",", ""))
    return int(match.group(1)) if match else None


def _parse_beds(text: str) -> int | None:
    if re.search(r"\bstudio\b", text, re.I):
        return 0
    match = re.search(r"(\d+)\s*bed", text, re.I)
    return int(match.group(1)) if match else None


def _parse_baths(text: str) -> float | None:
    match = re.search(r"([\d.]+)\s*bath", text, re.I)
    return float(match.group(1)) if match else None


def _extract_zipcode(text: str) -> str | None:
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", text or "")
    return match.group(1) if match else None


def _looks_like_address(text: str) -> bool:
    if not text:
        return False
    return bool(
        re.search(
            r"\d.*(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Place|Pl|Lane|Ln|Court|Ct)",
            text,
            re.I,
        )
    )
