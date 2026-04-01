import asyncio

from playwright.async_api import async_playwright

from proxy_support import build_playwright_proxy, get_proxy_url


DEFAULT_NAV_TIMEOUT_MS = 30000


async def fetch_page_html(
    url: str,
    *,
    wait_until: str = "domcontentloaded",
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    site_name: str | None = None,
    session_id: str | None = None,
    actor_proxy_input: dict | None = None,
) -> str:
    async with async_playwright() as p:
        proxy_url = await get_proxy_url(
            site_name,
            session_id=session_id,
            actor_proxy_input=actor_proxy_input,
        )
        launch_kwargs = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        proxy = build_playwright_proxy(proxy_url)
        if proxy:
            launch_kwargs["proxy"] = proxy

        browser = await p.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1100},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/Chicago",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            await context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4] });
                window.chrome = window.chrome || { runtime: {} };
                const originalQuery = window.navigator.permissions?.query;
                if (originalQuery) {
                  window.navigator.permissions.query = (parameters) => (
                    parameters && parameters.name === 'notifications'
                      ? Promise.resolve({ state: Notification.permission })
                      : originalQuery(parameters)
                  );
                }
                """
            )
            page = await context.new_page()
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            await page.mouse.move(300, 250)
            await page.mouse.wheel(0, 400)
            await asyncio.sleep(1.5)
            return await page.content()
        finally:
            if "context" in locals():
                await context.close()
            await browser.close()
