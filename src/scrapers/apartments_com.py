"""
Apartments.com scraper.

Strategy:
  1. Try the legacy search-service API.
  2. Parse the search page itself.
  3. Prefer rendered Playwright HTML in local environments, and fall back to
     it whenever the static page is blocked or empty.

Rendered HTML is parsed from:
  - embedded JSON blobs
  - JSON-LD listing blocks
  - placard/article cards and data-* attributes
"""
import re
import json
import os
import httpx
from apify import Actor
from bs4 import BeautifulSoup
from browser_fetch import fetch_page_artifacts
from proxy_support import get_proxy_url

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
        session_id = self._session_id(zipcode)
        proxy_url = await get_proxy_url("apartments_com", session_id=session_id)

        # Primary path: rendered HTML, including Playwright-rendered DOM.
        try:
            results = await self._page_search(client, zipcode, proxy_url=proxy_url, session_id=session_id)
            if results:
                print(f"[apartments_com] page HTML returned {len(results)} listings for {zipcode}")
                return results
        except Exception as e:
            print(f"[apartments_com] page HTML attempt failed for {zipcode}: {e}")

        # Secondary path: legacy search-service API.
        try:
            results = await self._api_search(client, zipcode, proxy_url=proxy_url)
            if results:
                print(f"[apartments_com] API returned {len(results)} listings for {zipcode}")
                return results
        except Exception as e:
            print(f"[apartments_com] API attempt failed for {zipcode}: {e}")

        print(f"[apartments_com] all attempts failed for {zipcode}")
        return []

    async def _api_search(self, client: httpx.AsyncClient, zipcode: str, *, proxy_url: str | None = None) -> list[dict]:
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

        resp = await self._request(
            client,
            "POST",
            "https://www.apartments.com/services/search/",
            headers=_API_HEADERS,
            json=payload,
            proxy_url=proxy_url,
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

    async def _page_search(
        self,
        client: httpx.AsyncClient,
        zipcode: str,
        *,
        proxy_url: str | None = None,
        session_id: str | None = None,
    ) -> list[dict]:
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
        local_browser_first = not (Actor.is_at_home() or os.environ.get("APIFY_TOKEN"))

        if local_browser_first:
            results = await self._playwright_search(url, zipcode, session_id=session_id)
            if results:
                return results

        resp = await self._request(client, "GET", url, headers=_HTML_HEADERS, proxy_url=proxy_url)
        print(f"[apartments_com] page GET HTTP {resp.status_code} for {url}")
        if resp.status_code == 200 and not self._looks_blocked(resp.text):
            html = resp.text
            results = self._extract_from_html(html, zipcode)
            if results:
                return results
        else:
            return await self._playwright_search(url, zipcode, session_id=session_id)

        return await self._playwright_search(url, zipcode, session_id=session_id)

    async def _playwright_search(self, url: str, zipcode: str, *, session_id: str | None = None) -> list[dict]:
        print(f"[apartments_com] switching to Playwright for {url}")
        artifacts = await fetch_page_artifacts(
            url,
            wait_for_selector="article.placard",
            site_name="apartments_com",
            session_id=session_id,
        )
        await self._store_browser_artifacts(zipcode, session_id, artifacts)
        self._log_browser_artifacts(url, artifacts)
        html = artifacts["html"]
        if self._looks_blocked(html):
            print(f"[apartments_com] Playwright still received a blocked page for {url}")
            return []
        return self._extract_from_html(html, zipcode)

    def _extract_from_html(self, html: str, zipcode: str) -> list[dict]:
        results = self._parse_json_ld(html, zipcode)
        if results:
            print(f"[apartments_com] JSON-LD parse found {len(results)} listings for {zipcode}")
            return results

        results = self._parse_html_cards(html, zipcode)
        if results:
            return results

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

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, proxy=proxy_url) as proxied_client:
            return await proxied_client.request(method, url, **kwargs)

    def _looks_blocked(self, html: str) -> bool:
        snippet = (html or "")[:1000].lower()
        return (
            "access denied" in snippet
            or "errors.edgesuite.net" in snippet
            or "verify you are human" in snippet
            or "captcha" in snippet
            or "perimeterx" in snippet
            or "akamai" in snippet
        )

    def _session_id(self, zipcode: str) -> str:
        min_price = self.criteria.get("min_price") or "na"
        max_price = self.criteria.get("max_price") or "na"
        min_beds = self.criteria.get("min_bedrooms") or "na"
        return f"apts-{zipcode}-{min_price}-{max_price}-{min_beds}"

    async def _store_browser_artifacts(self, zipcode: str, session_id: str | None, artifacts: dict) -> None:
        if not (Actor.is_at_home() or getattr(Actor, "config", None)):
            return

        key = f"apartments-com-browser-{session_id or zipcode}"
        compact_artifacts = {
            "zipcode": zipcode,
            "session_id": session_id,
            "final_url": artifacts.get("final_url"),
            "title": artifacts.get("title"),
            "challenge_signals": artifacts.get("challenge_signals") or [],
            "cookies": [
                {
                    "name": cookie.get("name"),
                    "domain": cookie.get("domain"),
                    "expires": cookie.get("expires"),
                }
                for cookie in artifacts.get("cookies", [])[:20]
            ],
            "requests": artifacts.get("requests", [])[:20],
            "responses": artifacts.get("responses", [])[:20],
            "html_preview": (artifacts.get("html") or "")[:2000],
        }
        try:
            await Actor.set_value(key, compact_artifacts)
            print(f"[apartments_com] stored browser artifacts in key-value store: {key}")
        except Exception as exc:
            print(f"[apartments_com] failed to store browser artifacts for {zipcode}: {exc}")

    def _log_browser_artifacts(self, url: str, artifacts: dict) -> None:
        challenge_signals = artifacts.get("challenge_signals") or []
        print(
            "[apartments_com] browser summary "
            f"url={url} final_url={artifacts.get('final_url')} "
            f"title={artifacts.get('title')!r} "
            f"challenge_signals={challenge_signals}"
        )

        interesting_responses = [
            response
            for response in artifacts.get("responses", [])
            if (
                response.get("resource_type") in {"document", "xhr", "fetch"}
                or "services/search" in (response.get("url") or "")
            )
        ]
        for response in interesting_responses[:8]:
            preview = response.get("body_preview")
            print(
                "[apartments_com] browser response "
                f"status={response.get('status')} "
                f"type={response.get('resource_type')} "
                f"url={response.get('url')} "
                f"content_type={response.get('content_type')}"
            )
            if preview:
                print(f"[apartments_com] browser body preview: {preview}")

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

    def _parse_json_ld(self, html: str, zipcode: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        listings = []
        seen = set()

        for script in soup.select('script[type="application/ld+json"]'):
            raw = script.string or script.get_text(strip=True)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue

            for node in self._iter_json_ld_nodes(data):
                listing = self._map_json_ld_listing(node, zipcode)
                if not listing or not listing.get("url"):
                    continue
                key = listing["url"]
                if key in seen:
                    continue
                seen.add(key)
                listings.append(listing)

        return listings

    def _parse_html_cards(self, html: str, zipcode: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        listings = []
        seen = set()
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
                if listing and listing["url"] and listing["url"] not in seen:
                    seen.add(listing["url"])
                    listings.append(listing)
            except Exception:
                continue

        print(f"[apartments_com] HTML card parse found {len(listings)} listings for {zipcode}")
        return listings

    def _parse_card(self, card, zipcode: str) -> dict | None:
        data_url = card.get("data-url") or card.get("data-linkurl")
        link = (
            card.select_one("a.property-link")
            or card.select_one('a[href*="apartments.com"]')
            or card.select_one("a[href]")
        )
        if not link and not data_url:
            return None
        url = data_url or link.get("href", "")
        if not url.startswith("http"):
            url = "https://www.apartments.com" + url

        title_el = card.select_one(".property-title, .js-placardTitle, h2.title, [class*=propertyName]")
        title = (
            card.get("data-propertyname")
            or card.get("data-listingname")
            or (title_el.get_text(strip=True) if title_el else "")
        )

        price_el = card.select_one(".price-range, .price, [class*=rentPrice], [class*=rent]")
        price = _parse_price(
            card.get("data-price")
            or card.get("data-rent")
            or (price_el.get_text(strip=True) if price_el else "")
        )

        bed_text = " ".join(filter(None, [
            card.get("data-beds"),
            card.get("data-bedrange"),
            card.get("data-minbeds"),
            card.get("data-maxbeds"),
        ]))
        bath_text = " ".join(filter(None, [
            card.get("data-baths"),
            card.get("data-bathrange"),
            card.get("data-minbaths"),
            card.get("data-maxbaths"),
        ]))
        beds_el = card.select_one("[class*=beds], [class*=bedroom], .bedTextBox")
        baths_el = card.select_one("[class*=baths], [class*=bathroom], .bathTextBox")
        beds = _parse_beds(bed_text or (beds_el.get_text(strip=True) if beds_el else ""))
        baths = _parse_baths(bath_text or (baths_el.get_text(strip=True) if baths_el else ""))

        addr_el = card.select_one(".property-address, address, [class*=address]")
        address = (
            card.get("data-streetaddress")
            or card.get("data-address")
            or (addr_el.get_text(strip=True) if addr_el else "")
        )

        lat = _as_float(card.get("data-lat")) or _as_float(card.get("data-latitude"))
        lng = _as_float(card.get("data-lng")) or _as_float(card.get("data-longitude"))

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
            "lat": lat, "lng": lng,
            "pets_allowed": None,
            "available_date": None,
            "source": "apartments_com",
            "image_url": image_url,
            "description": None,
        }

    def _iter_json_ld_nodes(self, data):
        if isinstance(data, list):
            for item in data:
                yield from self._iter_json_ld_nodes(item)
            return

        if not isinstance(data, dict):
            return

        if data.get("@type") in {"Apartment", "ApartmentComplex", "Residence", "Place", "Product"}:
            yield data

        graph = data.get("@graph")
        if graph:
            yield from self._iter_json_ld_nodes(graph)

        item_list = data.get("itemListElement")
        if item_list:
            for item in item_list:
                if isinstance(item, dict):
                    yield from self._iter_json_ld_nodes(item.get("item") or item)

    def _map_json_ld_listing(self, item: dict, zipcode: str) -> dict | None:
        url = item.get("url") or item.get("@id") or ""
        if not url:
            return None
        if not url.startswith("http"):
            url = "https://www.apartments.com" + url

        address_obj = item.get("address") or {}
        if isinstance(address_obj, str):
            address = address_obj
        else:
            address = ", ".join(
                filter(None, [
                    address_obj.get("streetAddress"),
                    address_obj.get("addressLocality"),
                    address_obj.get("addressRegion"),
                    address_obj.get("postalCode"),
                ])
            )

        geo = item.get("geo") or {}
        offers = item.get("offers") or {}
        if isinstance(offers, list):
            offer_prices = [_as_float(offer.get("price")) for offer in offers if isinstance(offer, dict)]
            offer_price = min((price for price in offer_prices if price is not None), default=None)
        else:
            offer_price = _as_float(offers.get("price"))

        price = _as_float(item.get("lowPrice")) or offer_price or _as_float(item.get("highPrice"))
        description = item.get("description")
        amenities = item.get("amenityFeature") or item.get("amenities")
        if isinstance(amenities, list):
            amenity_text = ", ".join(
                feature.get("name") if isinstance(feature, dict) else str(feature)
                for feature in amenities
                if feature
            )
            description = description or amenity_text

        return {
            "url": url,
            "title": item.get("name") or "",
            "price": int(price) if price is not None else None,
            "bedrooms": _parse_beds(json.dumps(item)),
            "bathrooms": _parse_baths(json.dumps(item)),
            "address": address,
            "zipcode": (address_obj.get("postalCode") if isinstance(address_obj, dict) else None) or zipcode,
            "lat": _as_float(geo.get("latitude")),
            "lng": _as_float(geo.get("longitude")),
            "pets_allowed": None,
            "available_date": None,
            "source": "apartments_com",
            "image_url": _extract_image_url(item.get("image")),
            "description": description,
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


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_image_url(image) -> str | None:
    if isinstance(image, str):
        return image
    if isinstance(image, list):
        for item in image:
            url = _extract_image_url(item)
            if url:
                return url
        return None
    if isinstance(image, dict):
        return (
            image.get("url")
            or image.get("contentUrl")
            or image.get("thumbnailUrl")
        )
    return None
