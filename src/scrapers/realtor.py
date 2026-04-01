"""
Realtor.com scraper.
Note: realtor.com rate-limits non-browser requests (429).
This is a stub that returns empty rather than crashing the actor.
"""


class RealtorScraper:
    def __init__(self, criteria: dict):
        self.criteria = criteria

    async def scrape(self) -> list[dict]:
        print("[realtor] skipped — site rate-limits non-browser requests (429)")
        return []
