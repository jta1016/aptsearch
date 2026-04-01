"""
Apartments.com scraper.
Note: apartments.com returns 403 to non-browser requests.
This is a stub that returns empty rather than crashing the actor.
"""


class ApartmentsComScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        print("[apartments_com] skipped — site blocks non-browser requests (403)")
        return []
