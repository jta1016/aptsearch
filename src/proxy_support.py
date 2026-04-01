import os
import re
from urllib.parse import unquote, urlparse

from apify import Actor


_PROXY_CONFIGURATION_CACHE: dict[str, object | None] = {}

_SITE_PROXY_OPTIONS: dict[str, list[dict]] = {
    "apartments_com": [
        {"groups": ["RESIDENTIAL"], "country_code": "US"},
        {"country_code": "US"},
        {},
    ],
    "realtor": [
        {"groups": ["RESIDENTIAL"], "country_code": "US"},
        {"country_code": "US"},
        {},
    ],
    "streeteasy": [
        {"groups": ["RESIDENTIAL"], "country_code": "US"},
        {"country_code": "US"},
        {},
    ],
    "zillow": [
        {"groups": ["RESIDENTIAL"], "country_code": "US"},
        {"country_code": "US"},
        {},
    ],
    "default": [
        {"country_code": "US"},
        {},
    ],
}


async def get_proxy_url(
    site_name: str | None = None,
    *,
    session_id: str | None = None,
    actor_proxy_input: dict | None = None,
) -> str | None:
    if not (Actor.is_at_home() or os.environ.get("APIFY_TOKEN")):
        return None

    cache_key = site_name or "default"
    proxy_configuration = _PROXY_CONFIGURATION_CACHE.get(cache_key)

    if proxy_configuration is None:
        proxy_configuration = await _create_proxy_configuration(
            site_name=site_name,
            actor_proxy_input=actor_proxy_input,
        )
        _PROXY_CONFIGURATION_CACHE[cache_key] = proxy_configuration

    if not proxy_configuration:
        return None

    try:
        return await proxy_configuration.new_url(session_id=_normalize_session_id(session_id))
    except Exception as exc:
        Actor.log.warning(f"Failed to create proxy URL for {cache_key}: {exc}")
        return None


async def _create_proxy_configuration(
    *,
    site_name: str | None = None,
    actor_proxy_input: dict | None = None,
):
    attempts: list[dict] = []
    if actor_proxy_input:
        attempts.append({"actor_proxy_input": actor_proxy_input})
    attempts.extend(_SITE_PROXY_OPTIONS.get(site_name or "", _SITE_PROXY_OPTIONS["default"]))

    for options in attempts:
        try:
            proxy_configuration = await Actor.create_proxy_configuration(**options)
            if proxy_configuration:
                if options:
                    Actor.log.info(f"Using proxy configuration for {site_name or 'default'}: {options}")
                else:
                    Actor.log.info(f"Using default proxy configuration for {site_name or 'default'}")
                return proxy_configuration
        except Exception as exc:
            Actor.log.warning(f"Proxy configuration attempt failed for {site_name or 'default'} with {options}: {exc}")

    Actor.log.info(f"No proxy configuration available for {site_name or 'default'}")
    return None


def build_playwright_proxy(proxy_url: str | None) -> dict | None:
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return None

    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server = f"{server}:{parsed.port}"

    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def _normalize_session_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    cleaned = re.sub(r"[^\w._~]+", "_", session_id)
    return cleaned[:50] or "aptsearch_session"
