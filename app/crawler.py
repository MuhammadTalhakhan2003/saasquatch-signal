"""Polite, resilient async crawler.

Design choices
- Concurrency across domains (semaphore), sequential + delayed within a domain
  so we never hammer one small business's shared hosting.
- robots.txt is honoured; disallowed pages are skipped, not worked around.
- Retries with exponential backoff + jitter on 429/5xx/network errors,
  https -> http fallback, and optional proxy rotation on 403/429.
- CAPTCHA / bot-wall pages are *detected and flagged*, never solved; the lead
  is still returned with whatever input data we had, marked "blocked".
- Every fetched page is cached in SQLite (TTL), so re-running a list or
  overlapping lists costs ~0 network.
"""
from __future__ import annotations

import asyncio
import itertools
import random
import re
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from . import db
from .config import MAX_PAGES_PER_SITE, MAX_RETRIES, PROXIES, REQUEST_TIMEOUT, USER_AGENT
from .extract import discover_links, extract_page, merge_pages

CAPTCHA_MARKERS = re.compile(
    r"cf-challenge|challenge-platform|g-recaptcha|hcaptcha|captcha-delivery|"
    r"attention required! \| cloudflare|verify you are human|access denied",
    re.I,
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_proxy_cycle = itertools.cycle(PROXIES) if PROXIES else None
_robots_cache: dict[str, RobotFileParser | None] = {}


class FetchResult:
    __slots__ = ("url", "status", "html", "blocked", "error")

    def __init__(self, url, status=0, html="", blocked=False, error=""):
        self.url, self.status, self.html, self.blocked, self.error = url, status, html, blocked, error


async def _robots_allows(client: httpx.AsyncClient, url: str) -> bool:
    parts = urlparse(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        rp = RobotFileParser()
        try:
            r = await client.get(origin + "/robots.txt", timeout=5)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
                _robots_cache[origin] = rp
            else:
                _robots_cache[origin] = None  # no robots.txt -> allowed
        except httpx.HTTPError:
            _robots_cache[origin] = None
    rp = _robots_cache[origin]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


async def fetch(client: httpx.AsyncClient, url: str, use_cache: bool = True) -> FetchResult:
    if use_cache and (hit := db.cache_get(url)):
        status, html = hit
        return FetchResult(url, status, html, blocked=bool(CAPTCHA_MARKERS.search(html[:20000])))

    if not await _robots_allows(client, url):
        return FetchResult(url, error="Disallowed by robots.txt")

    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            if _proxy_cycle and attempt > 0:
                async with httpx.AsyncClient(
                    headers=HEADERS, follow_redirects=True, timeout=REQUEST_TIMEOUT, proxy=next(_proxy_cycle)
                ) as pc:
                    resp = await pc.get(url)
            else:
                resp = await client.get(url)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code in (429, 500, 502, 503, 504) or (resp.status_code == 403 and _proxy_cycle):
                last_error = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else (2 ** attempt) + random.random()
                await asyncio.sleep(min(delay, 8))
                continue
            if "html" not in ctype and ctype:
                return FetchResult(str(resp.url), resp.status_code, error=f"Non-HTML ({ctype.split(';')[0]})")
            html = resp.text
            blocked = resp.status_code in (403, 503) or bool(CAPTCHA_MARKERS.search(html[:20000]))
            if resp.status_code == 200 and not blocked:
                db.cache_put(url, resp.status_code, html)
            return FetchResult(str(resp.url), resp.status_code, html, blocked=blocked)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as e:
            last_error = type(e).__name__
            # https failed at the TLS/connect level -> try plain http once
            if url.startswith("https://") and attempt == 0 and isinstance(e, httpx.ConnectError):
                url = "http://" + url[len("https://"):]
                continue
            await asyncio.sleep((2 ** attempt) * 0.5 + random.random() * 0.5)
        except httpx.HTTPError as e:
            last_error = type(e).__name__
            break
    return FetchResult(url, error=last_error or "Fetch failed")


async def crawl_company(client: httpx.AsyncClient, domain: str) -> dict:
    """Crawl homepage + the most promising contact/about pages for one domain."""
    # Many SMB sites only have a valid cert / server on one of apex vs www.
    home = await fetch(client, f"https://{domain}/")
    if not home.html or home.status != 200:
        home_www = await fetch(client, f"https://www.{domain}/")
        if home_www.html and (home_www.status == 200 or not home.html):
            home = home_www
    crawl_meta = {
        "status": "ok",
        "http_status": home.status,
        "error": home.error,
        "final_url": home.url,
    }
    if home.blocked:
        crawl_meta["status"] = "blocked"
    if not home.html:
        crawl_meta["status"] = "unreachable"
        return {"profile": {}, "crawl": crawl_meta}

    pages = [extract_page(home.html, home.url)]
    if not home.blocked:
        for link in discover_links(home.html, home.url)[: MAX_PAGES_PER_SITE - 1]:
            await asyncio.sleep(0.3)  # per-host politeness
            sub = await fetch(client, link)
            if sub.html and not sub.blocked and sub.status == 200:
                pages.append(extract_page(sub.html, sub.url))

    profile = merge_pages(pages)
    # Parked / dead domains look "reachable" but have no business content.
    if profile.get("word_count", 1) == 0 or re.search(
        r"domain (?:is )?for sale|this domain may be for sale|parked free|buy this domain",
        profile.get("text_sample", ""), re.I,
    ):
        crawl_meta["status"] = "parked"
    elif crawl_meta["status"] == "ok" and profile.get("word_count", 0) < 80:
        # JS-rendered shell or error page: keep the lead, but flag partial data
        crawl_meta["status"] = "thin"
    return {"profile": profile, "crawl": crawl_meta}


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT,
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        http2=False,
    )
