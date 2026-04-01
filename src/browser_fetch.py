import asyncio

from playwright.async_api import async_playwright


DEFAULT_NAV_TIMEOUT_MS = 30000


async def fetch_page_html(
    url: str,
    *,
    wait_until: str = "domcontentloaded",
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                viewport={"width": 1440, "height": 1100},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            await asyncio.sleep(1.5)
            return await page.content()
        finally:
            await browser.close()
