"""
Realtor.com rental scraper.
Strategy:
  1. POST to the public GraphQL endpoint at graph.realtor.com/graphql.
  2. If that fails, GET the search results page and extract __NEXT_DATA__ JSON.
No browser required — uses httpx only.
"""
import re
import json
import httpx

_GRAPHQL_URL = "https://graph.realtor.com/graphql"

_GRAPHQL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.realtor.com",
    "Referer": "https://www.realtor.com/",
}

_HTML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Upgrade-Insecure-Requests": "1",
}

# Minimal GraphQL query for rental home search.
_QUERY = """
query ConsumerSearchQuery(
  $query: SearchQuery
  $limit: Int
  $offset: Int
  $sort: [SortInput]
) {
  home_search(query: $query, limit: $limit, offset: $offset, sort: $sort) {
    count
    results {
      property_id
      listing_id
      permalink
      list_price
      status
      description {
        beds
        baths
        text
      }
      location {
        address {
          line
          city
          state_code
          postal_code
          coordinate {
            lat
            lon
          }
        }
      }
      primary_photo {
        href
      }
    }
  }
}
"""


class RealtorScraper:
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
                    print(f"[realtor] error for {zipcode}: {e}")

        return listings

    async def _fetch(self, client: httpx.AsyncClient, zipcode: str) -> list[dict]:
        # Try 1: GraphQL API
        try:
            results = await self._graphql_search(client, zipcode)
            if results:
                print(f"[realtor] GraphQL returned {len(results)} listings for {zipcode}")
                return results
        except Exception as e:
            print(f"[realtor] GraphQL failed for {zipcode}: {e}")

        # Try 2: Parse __NEXT_DATA__ from the page
        try:
            results = await self._page_search(client, zipcode)
            if results:
                print(f"[realtor] page __NEXT_DATA__ returned {len(results)} listings for {zipcode}")
                return results
        except Exception as e:
            print(f"[realtor] page search failed for {zipcode}: {e}")

        print(f"[realtor] all attempts failed for {zipcode}")
        return []

    # ------------------------------------------------------------------
    # Primary: GraphQL API
    # ------------------------------------------------------------------

    async def _graphql_search(self, client: httpx.AsyncClient, zipcode: str) -> list[dict]:
        min_price = self.criteria.get("min_price")
        max_price = self.criteria.get("max_price")
        min_beds = self.criteria.get("min_bedrooms")

        query_obj: dict = {
            "status": ["for_rent"],
            "postal_code": zipcode,
        }
        if min_price or max_price:
            query_obj["list_price"] = {}
            if min_price:
                query_obj["list_price"]["min"] = min_price
            if max_price:
                query_obj["list_price"]["max"] = max_price
        if min_beds:
            query_obj["beds"] = {"min": min_beds}

        payload = {
            "query": _QUERY,
            "variables": {
                "query": query_obj,
                "limit": 42,
                "offset": 0,
                "sort": [{"field": "list_date", "direction": "desc"}],
            },
        }

        resp = await client.post(_GRAPHQL_URL, json=payload, headers=_GRAPHQL_HEADERS)
        print(f"[realtor] GraphQL HTTP {resp.status_code} for {zipcode}")
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")

        body = resp.json()
        results = (
            body.get("data", {})
            .get("home_search", {})
            .get("results") or []
        )
        return [self._map_graphql(r, zipcode) for r in results if r]

    def _map_graphql(self, item: dict, zipcode: str) -> dict:
        addr = item.get("location", {}).get("address", {})
        coord = addr.get("coordinate", {})
        desc = item.get("description", {})

        permalink = item.get("permalink", "")
        url = (
            f"https://www.realtor.com/realestateandhomes-detail/{permalink}"
            if permalink else ""
        )
        address = ", ".join(filter(None, [
            addr.get("line", ""),
            addr.get("city", ""),
            addr.get("state_code", ""),
        ]))

        return {
            "url": url,
            "title": address or f"Listing {item.get('property_id', '')}",
            "price": item.get("list_price"),
            "bedrooms": desc.get("beds"),
            "bathrooms": desc.get("baths"),
            "address": address,
            "zipcode": addr.get("postal_code") or zipcode,
            "lat": coord.get("lat"),
            "lng": coord.get("lon"),
            "pets_allowed": None,
            "available_date": None,
            "source": "realtor",
            "image_url": (item.get("primary_photo") or {}).get("href"),
            "description": desc.get("text"),
        }

    # ------------------------------------------------------------------
    # Fallback: parse __NEXT_DATA__ from the search page HTML
    # ------------------------------------------------------------------

    async def _page_search(self, client: httpx.AsyncClient, zipcode: str) -> list[dict]:
        url = f"https://www.realtor.com/apartments/{zipcode}"
        resp = await client.get(url, headers=_HTML_HEADERS)
        print(f"[realtor] page GET HTTP {resp.status_code} for {url}")
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")

        return self._parse_next_data(resp.text, zipcode)

    def _parse_next_data(self, html: str, zipcode: str) -> list[dict]:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []

        props = data.get("props", {}).get("pageProps", {})

        # Try several known paths for rental results.
        candidates = [
            props.get("initialProps", {}).get("componentProps", {}).get("listings") or [],
            props.get("componentProps", {}).get("listings") or [],
            props.get("listings") or [],
            props.get("searchResults", {}).get("properties") or [],
        ]
        for items in candidates:
            if items:
                return [self._map_page_result(r, zipcode) for r in items if r]
        return []

    def _map_page_result(self, item: dict, zipcode: str) -> dict:
        addr = item.get("location", {}).get("address", {})
        coord = addr.get("coordinate", {})
        desc = item.get("description", {})

        permalink = item.get("permalink", "")
        url = (
            f"https://www.realtor.com/realestateandhomes-detail/{permalink}"
            if permalink else ""
        )
        address = ", ".join(filter(None, [
            addr.get("line", ""),
            addr.get("city", ""),
            addr.get("state_code", ""),
        ]))

        return {
            "url": url,
            "title": address or "",
            "price": item.get("list_price"),
            "bedrooms": desc.get("beds"),
            "bathrooms": desc.get("baths"),
            "address": address,
            "zipcode": addr.get("postal_code") or zipcode,
            "lat": coord.get("lat"),
            "lng": coord.get("lon"),
            "pets_allowed": None,
            "available_date": None,
            "source": "realtor",
            "image_url": (item.get("primary_photo") or {}).get("href"),
            "description": desc.get("text"),
        }
