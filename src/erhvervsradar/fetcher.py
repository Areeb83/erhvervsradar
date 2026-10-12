"""Polite downloading: robots.txt, rate limits, retries, and an honest User-Agent.

Every site adapter downloads through this one class, so politeness rules
live in exactly one place and can't be forgotten in a new adapter.
"""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

# Identify the bot honestly and give site owners a way to reach you.
# Change the URL and e-mail to your own GitHub repo / address.
USER_AGENT = (
    "ErhvervsradarBot/0.1 (+https://github.com/Areeb83/erhvervsradar; "
    "student portfolio project; contact: areebabbasx@gmail.com)"
)

RETRY_STATUS = {429, 500, 502, 503, 504}


class BlockedByRobots(Exception):
    """The site's robots.txt asks crawlers not to fetch this URL."""


class Fetcher:
    ROBOTS_TTL = 3600.0  # re-read robots.txt hourly in long-running processes (the web app)

    def __init__(
        self,
        min_delay: float = 2.0,
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,  # lets tests fake the network
        host_delays: dict[str, float] | None = None,  # e.g. {"search.savills.com": 5}
    ):
        self.min_delay = min_delay
        self.max_retries = max_retries
        self.host_delays = host_delays or {}
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "da,en;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._robots: dict[str, tuple[RobotFileParser, float]] = {}
        self._last_request: dict[str, float] = {}
        self.requests_made = 0
        # The web app shares one Fetcher between all visitors, so the per-site
        # pause holds across everyone using it at the same time.
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}

    # -- robots.txt ---------------------------------------------------------

    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            cached = self._robots.get(host)
        if cached and time.monotonic() - cached[1] < self.ROBOTS_TTL:
            return cached[0]
        # Follows RFC 9309: a missing robots.txt (4xx) allows everything, but if it
        # can't be read because of a server or network error, we crawl nothing.
        parser = RobotFileParser()
        try:
            resp = self.client.get(f"{host}/robots.txt")
        except httpx.HTTPError as exc:
            log.warning("Could not reach robots.txt for %s (%s); not crawling it", host, exc)
            parser.disallow_all = True
        else:
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
            elif 400 <= resp.status_code < 500:
                parser.parse([])
            else:
                log.warning(
                    "robots.txt for %s returned %d; not crawling it", host, resp.status_code
                )
                parser.disallow_all = True
        with self._lock:
            self._robots[host] = (parser, time.monotonic())
        return parser

    def allowed(self, url: str) -> bool:
        return self._robots_for(url).can_fetch(USER_AGENT, url)

    def _delay_for(self, url: str) -> float:
        crawl_delay = self._robots_for(url).crawl_delay(USER_AGENT)
        own = self.host_delays.get(urlsplit(url).netloc, self.min_delay)
        return max(own, float(crawl_delay or 0))

    # -- downloading ---------------------------------------------------------

    def _wait_turn(self, url: str) -> None:
        """Never hit the same host faster than its delay allows."""
        host = urlsplit(url).netloc
        delay = self._delay_for(url)
        with self._lock:
            host_lock = self._host_locks.setdefault(host, threading.Lock())
        with host_lock:  # one request per site at a time, across all threads
            wait = delay - (time.monotonic() - self._last_request.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last_request[host] = time.monotonic()

    def get(self, url: str) -> httpx.Response:
        if not self.allowed(url):
            raise BlockedByRobots(url)

        for attempt in range(1, self.max_retries + 1):
            self._wait_turn(url)
            self.requests_made += 1
            try:
                resp = self.client.get(url)
            except httpx.TransportError as exc:
                log.warning("Network error on %s (attempt %d): %s", url, attempt, exc)
                if attempt == self.max_retries:
                    raise
            else:
                if resp.status_code not in RETRY_STATUS:
                    resp.raise_for_status()
                    return resp
                log.warning("HTTP %d on %s (attempt %d)", resp.status_code, url, attempt)
                if attempt == self.max_retries:
                    resp.raise_for_status()
            # Exponential backoff: 5s, 10s, 20s... gives an overloaded site room to recover.
            time.sleep(5 * 2 ** (attempt - 1))
        raise RuntimeError("unreachable")

    def get_text(self, url: str) -> str:
        return self.get(url).text

    def get_rendered(self, url: str) -> str:
        """Render the page in a headless browser and return the final HTML.

        Only for single-page apps that build their content with JavaScript.
        Slower and heavier than get(), so adapters use it as a fallback.
        """
        if not self.allowed(url):
            raise BlockedByRobots(url)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Rendering needs Playwright: "
                "pip install -e '.[browser]' && playwright install chromium"
            ) from exc

        self._wait_turn(url)
        self.requests_made += 1
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle")
            html = page.content()
            browser.close()
        return html

    def close(self) -> None:
        self.client.close()
