"""
Realtor.com scraper using their internal GraphQL API.
"""
import json
import re
import httpx

GRAPHQL_URL = "https://www.realtor.com/api/v1/rdc_search_srp"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Referer": "https://www.realtor.com/",
    "Origin": "https://www.realtor.com",
}

QUERY = """
query ConsumerSearchMainQuery($query: SearchHomeInput!, $limit: Int, $offset: Int) {
  home_search: home_search(query: $query, limit: $limit, offset: $offset, sort: [{field: list_date, direction: desc}]) {
    total
    results {
      property_id
      list_price
      href
      description {
        beds
        baths_full
        baths_half
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
      pet_policy {
        cats
        dogs
        pets_allowed
      }
      list_date
      photos(limit: 1) {
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
        targets = self._build_targets()

        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            for variables in targets:
                try:
                    results = await self._query(client, variables)
                    listings.extend(results)
                except Exception as e:
                    print(f"[realtor] error: {e}")

        seen = set()
        unique = []
        for l in listings:
            if l["url"] not in seen:
                seen.add(l["url"])
                unique.append(l)
        return unique

    def _build_targets(self) -> list[dict]:
        base_filters = {
            "listing_subtypes": ["rental"],
        }
        if self.criteria.get("min_price"):
            base_filters["list_price"] = {"min": self.criteria["min_price"]}
        if self.criteria.get("max_price"):
            base_filters.setdefault("list_price", {})["max"] = self.criteria["max_price"]
        if self.criteria.get("min_bedrooms") is not None:
            base_filters["beds"] = {"min": self.criteria["min_bedrooms"]}
        if self.criteria.get("max_bedrooms") is not None:
            base_filters.setdefault("beds", {})["max"] = self.criteria["max_bedrooms"]
        if self.criteria.get("min_bathrooms"):
            base_filters["baths"] = {"min": self.criteria["min_bathrooms"]}
        if self.criteria.get("pets_allowed"):
            base_filters["pets_allowed"] = True

        zipcodes = self.criteria.get("zipcodes", [])
        neighborhoods = self.criteria.get("neighborhoods", [])

        targets = []
        for z in zipcodes:
            targets.append({**base_filters, "postal_code": z})
        for n in neighborhoods:
            targets.append({**base_filters, "city": n, "state_code": "NY"})

        if not targets:
            targets.append({**base_filters, "city": "New York", "state_code": "NY"})

        return [{"query": t, "limit": 20, "offset": 0} for t in targets]

    async def _query(self, client: httpx.AsyncClient, variables: dict) -> list[dict]:
        payload = {
            "query": QUERY,
            "variables": variables,
            "operationName": "ConsumerSearchMainQuery",
        }
        resp = await client.post(
            GRAPHQL_URL,
            json=payload,
            params={"client_id": "rdc-search-for-rent-search", "schema": "vesta"},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("data", {}).get("home_search", {}).get("results", [])
        return [self._parse_item(r) for r in results if r]

    def _parse_item(self, item: dict) -> dict:
        href = item.get("href", "")
        if href and not href.startswith("http"):
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
