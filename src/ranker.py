"""
Ranks listings against search criteria and returns a scored, sorted list.

Scoring weights (total 100 pts):
  Price proximity     30
  Bedrooms match      20
  Bathrooms match     10
  Pets allowed        20  (only if pets_allowed=True in criteria)
  Subway proximity    15
  Availability date    5
  Amenities           20  (only if required_amenities is set in criteria)
"""
from datetime import datetime
from typing import Optional
from subway import subway_score

AMENITY_KEYWORDS: dict[str, list[str]] = {
    "gym": ["gym", "fitness center", "fitness room", "workout room"],
    "pool": ["pool", "swimming pool", "rooftop pool"],
    "doorman": ["doorman", "concierge", "attended lobby", "door man"],
    "central_air": ["central air", "central a/c", "central ac", "central cooling", "central hvac"],
    "laundry_in_unit": ["in-unit laundry", "in unit laundry", "w/d in unit", "washer/dryer in unit", "in-unit w/d", "washer dryer in unit"],
    "laundry_in_building": ["laundry in building", "laundry room", "shared laundry", "common laundry", "on-site laundry", "building laundry"],
    "dishwasher": ["dishwasher"],
    "elevator": ["elevator"],
    "parking": ["parking", "parking garage", "garage parking", "covered parking"],
}


def rank_listings(listings: list[dict], criteria: dict) -> list[dict]:
    scored = []
    for listing in listings:
        score, detail = score_listing(listing, criteria)
        scored.append({**listing, "_score": score, "_score_detail": detail})

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored


def score_listing(listing: dict, criteria: dict) -> tuple[float, dict]:
    detail = {}
    total = 0.0

    # --- Price (30 pts) ---
    price = listing.get("price")
    min_p = criteria.get("min_price")
    max_p = criteria.get("max_price")
    target_p = criteria.get("target_price")

    if price is not None:
        if (min_p and price < min_p) or (max_p and price > max_p):
            detail["price"] = 0
        elif target_p:
            spread = max(max_p - min_p, 1) if (min_p and max_p) else max_p or target_p or 1
            diff_ratio = abs(price - target_p) / spread
            pts = max(0.0, 30 * (1 - diff_ratio))
            detail["price"] = round(pts, 1)
            total += pts
        else:
            detail["price"] = 30
            total += 30
    else:
        detail["price"] = 15  # neutral if unknown
        total += 15

    # --- Bedrooms (20 pts) ---
    beds = listing.get("bedrooms")
    min_beds = criteria.get("min_bedrooms")
    max_beds = criteria.get("max_bedrooms")

    if beds is not None and min_beds is not None:
        if beds < min_beds:
            detail["bedrooms"] = 0
        elif max_beds and beds > max_beds:
            detail["bedrooms"] = max(0, 20 - (beds - max_beds) * 5)
            total += detail["bedrooms"]
        else:
            detail["bedrooms"] = 20
            total += 20
    else:
        detail["bedrooms"] = 10
        total += 10

    # --- Bathrooms (10 pts) ---
    baths = listing.get("bathrooms")
    min_baths = criteria.get("min_bathrooms")

    if baths is not None and min_baths is not None:
        if baths < min_baths:
            detail["bathrooms"] = 0
        else:
            detail["bathrooms"] = 10
            total += 10
    else:
        detail["bathrooms"] = 5
        total += 5

    # --- Pets (20 pts, only if required) ---
    if criteria.get("pets_allowed"):
        pets = listing.get("pets_allowed")
        if pets is True:
            detail["pets"] = 20
            total += 20
        elif pets is False:
            detail["pets"] = 0
        else:
            detail["pets"] = 5  # unknown, small credit
            total += 5
    else:
        detail["pets"] = 20  # not a factor, full credit
        total += 20

    # --- Subway proximity (15 pts) ---
    max_miles = criteria.get("max_subway_distance_miles", 0.5)
    preferred_lines = criteria.get("preferred_subway_lines", [])
    sub_score, station_info = subway_score(
        listing.get("lat"), listing.get("lng"), max_miles, preferred_lines
    )

    if sub_score == 0.0 and listing.get("lat") is not None:
        # Hard filter: too far from subway
        return 0.0, {**detail, "subway": 0, "subway_filtered": True}

    pts = round(15 * sub_score, 1)
    detail["subway"] = pts
    detail["nearest_station"] = station_info
    total += pts

    # --- Availability date (5 pts) ---
    avail_before = criteria.get("availability_before")
    avail_date_str = listing.get("available_date")

    if avail_before and avail_date_str:
        try:
            target_dt = datetime.fromisoformat(avail_before)
            avail_dt = _parse_date(avail_date_str)
            if avail_dt and avail_dt <= target_dt:
                detail["availability"] = 5
                total += 5
            else:
                detail["availability"] = 0
        except Exception:
            detail["availability"] = 2
            total += 2
    else:
        detail["availability"] = 5
        total += 5

    # --- Amenities (20 pts, only if required) ---
    required_amenities = [a.lower() for a in (criteria.get("required_amenities") or [])]
    if required_amenities:
        listing_amenities = " ".join(a.lower() for a in (listing.get("amenities") or []))
        desc = (listing.get("description") or "").lower()
        has_amenity_data = bool(listing_amenities or desc)
        searchable = listing_amenities + " " + desc

        matched = 0
        unknown = 0
        for amenity in required_amenities:
            keywords = AMENITY_KEYWORDS.get(amenity, [amenity])
            if any(kw in searchable for kw in keywords):
                matched += 1
            elif not has_amenity_data:
                unknown += 1  # no data to confirm or deny
        pts = round(20 * (matched + 0.4 * unknown) / len(required_amenities), 1)
        detail["amenities"] = pts
        total += pts
    else:
        detail["amenities"] = 20
        total += 20

    # --- Neighborhood fuzzy match (bonus: up to 10 pts) ---
    # Uses case-insensitive substring matching so "park slope", "Park Slope",
    # and partial matches like "slope" all work.
    neighborhoods = [n.strip() for n in criteria.get("neighborhoods", []) if n and n.strip()]
    if neighborhoods:
        searchable = (
            (listing.get("address") or "") + " " + (listing.get("title") or "")
        ).lower()
        if any(n.lower() in searchable for n in neighborhoods):
            detail["neighborhood"] = 10
            total += 10
        else:
            detail["neighborhood"] = 0

    detail["total"] = round(total, 1)
    return round(total, 1), detail


def _parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None
