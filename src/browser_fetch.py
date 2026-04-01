import asyncio
import json
from typing import Any

from playwright.async_api import async_playwright

from proxy_support import build_playwright_proxy, get_proxy_url


DEFAULT_NAV_TIMEOUT_MS = 30000
MAX_CAPTURED_EVENTS = 40
MAX_CAPTURED_BODIES = 8
MAX_SNIPPET_CHARS = 400
CHALLENGE_KEYWORDS = (
    "access denied",
    "captcha",
    "verify you are human",
    "verify you're human",
    "perimeterx",
    "akamai",
    "bot manager",
    "challenge",
)


def _clip(value: str | None, limit: int = MAX_SNIPPET_CHARS) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _detect_challenge_signals(*parts: str | None) -> list[str]:
    haystack = " ".join(part or "" for part in parts).lower()
    return [keyword for keyword in CHALLENGE_KEYWORDS if keyword in haystack]


def _should_capture_body(content_type: str, resource_type: str, url: str) -> bool:
    lowered = (content_type or "").lower()
    if "json" in lowered or resource_type in {"xhr", "fetch"}:
        return True
    return "services/search" in url


async def fetch_page_artifacts(
    url: str,
    *,
    wait_until: str = "domcontentloaded",
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    site_name: str | None = None,
    session_id: str | None = None,
    actor_proxy_input: dict | None = None,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {
        "html": "",
        "final_url": url,
        "title": "",
        "challenge_signals": [],
        "requests": [],
        "responses": [],
        "cookies": [],
        "storage_state": {},
    }

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

            captured_body_count = 0
            response_tasks: list[asyncio.Task] = []

            async def record_response(response) -> None:
                nonlocal captured_body_count
                request = response.request
                resource_type = request.resource_type
                content_type = response.headers.get("content-type", "")
                record = {
                    "status": response.status,
                    "url": response.url,
                    "resource_type": resource_type,
                    "content_type": content_type,
                }

                if len(artifacts["responses"]) < MAX_CAPTURED_EVENTS:
                    if captured_body_count < MAX_CAPTURED_BODIES and _should_capture_body(content_type, resource_type, response.url):
                        try:
                            record["body_preview"] = _clip(await response.text())
                            captured_body_count += 1
                        except Exception as exc:
                            record["body_preview_error"] = str(exc)
                    artifacts["responses"].append(record)

            def record_request(request) -> None:
                if len(artifacts["requests"]) >= MAX_CAPTURED_EVENTS:
                    return
                artifacts["requests"].append({
                    "method": request.method,
                    "url": request.url,
                    "resource_type": request.resource_type,
                })

            def record_navigation(frame) -> None:
                if frame != page.main_frame or len(artifacts["requests"]) >= MAX_CAPTURED_EVENTS:
                    return
                artifacts["requests"].append({
                    "method": "NAVIGATE",
                    "url": frame.url,
                    "resource_type": "document",
                })

            page.on("request", record_request)
            page.on("response", lambda response: response_tasks.append(asyncio.create_task(record_response(response))))
            page.on("framenavigated", record_navigation)

            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            await page.mouse.move(300, 250)
            await page.mouse.wheel(0, 400)
            await asyncio.sleep(1.5)
            if response_tasks:
                await asyncio.gather(*response_tasks, return_exceptions=True)

            artifacts["html"] = await page.content()
            artifacts["final_url"] = page.url
            artifacts["title"] = await page.title()
            artifacts["cookies"] = await context.cookies()
            artifacts["storage_state"] = await context.storage_state()
            artifacts["challenge_signals"] = _detect_challenge_signals(
                artifacts["title"],
                artifacts["html"][:2000],
                json.dumps(artifacts["responses"][:10]),
            )
            return artifacts
        finally:
            if "context" in locals():
                await context.close()
            await browser.close()


async def fetch_page_html(
    url: str,
    *,
    wait_until: str = "domcontentloaded",
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    site_name: str | None = None,
    session_id: str | None = None,
    actor_proxy_input: dict | None = None,
) -> str:
    artifacts = await fetch_page_artifacts(
        url,
        wait_until=wait_until,
        timeout_ms=timeout_ms,
        site_name=site_name,
        session_id=session_id,
        actor_proxy_input=actor_proxy_input,
    )
    return artifacts["html"]
