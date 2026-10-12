"""Check one listing link: is it allowed, is it still online, and what does it say?

The web app calls check_url() once per spreadsheet row. It never raises:
every outcome becomes a status the user can read in the result column.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from .fetcher import BlockedByRobots, Fetcher
from .models import Listing
from .sources.edc import URL_PATTERN as EDC_LISTING_URL
from .sources.edc import parse_listing as parse_edc
from .sources.savills import LISTING_URL as SAVILLS_LISTING_URL
from .sources.savills import parse_listing_page as parse_savills

# Only these sites are crawled, and at most this often (seconds between requests).
HOST_DELAYS = {"www.edc.dk": 2.0, "edc.dk": 2.0, "search.savills.com": 5.0}

OK = "ok"  # listing read
INVALID_URL = "invalid_url"  # not a link at all
UNSUPPORTED_SITE = "unsupported_site"  # a site we don't crawl
NOT_A_LISTING = "not_a_listing"  # right site, but not a single listing
NOT_FOUND = "not_found"  # the listing is gone
BLOCKED = "blocked_by_robots"  # robots.txt asks us not to fetch it
ERROR = "error"  # network or server trouble; worth retrying later

MESSAGES = {
    INVALID_URL: "This is not a valid web address.",
    UNSUPPORTED_SITE: "Only edc.dk and search.savills.com listings are supported.",
    NOT_A_LISTING: "This page is on a supported site, but it isn't a single listing.",
    NOT_FOUND: "The listing no longer exists.",
    BLOCKED: "The site's robots.txt asks crawlers not to fetch this page.",
}


@dataclass
class CheckResult:
    status: str
    url: str
    listing: Listing | None = None
    message: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def parser_for(url: str) -> Callable[[str, str], Listing | None] | None:
    if urlsplit(url).netloc in ("www.edc.dk", "edc.dk") and EDC_LISTING_URL.search(url):
        return parse_edc
    if SAVILLS_LISTING_URL.match(url):
        return parse_savills
    return None


def normalize_url(raw: str) -> str | None:
    """'www.edc.dk/x?utm_source=y' -> 'https://www.edc.dk/x'. None if it isn't a link."""
    raw = (raw or "").strip()
    if raw.lower().startswith("www."):
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or "." not in parts.netloc or " " in raw:
        return None
    # Tracking parameters like ?utm_source=… are not part of the listing.
    return urlunsplit(("https", parts.netloc.lower(), parts.path, "", ""))


def quick_check(raw: str) -> CheckResult | None:
    """Answer rows that need no download at all (not a link, unsupported site)."""
    url = normalize_url(raw)
    if url is None:
        return CheckResult(INVALID_URL, raw, message=MESSAGES[INVALID_URL])
    if urlsplit(url).netloc not in HOST_DELAYS:
        return CheckResult(UNSUPPORTED_SITE, url, message=MESSAGES[UNSUPPORTED_SITE])
    if parser_for(url) is None:
        return CheckResult(NOT_A_LISTING, url, message=MESSAGES[NOT_A_LISTING])
    return None


def check_url(raw: str, fetcher: Fetcher) -> CheckResult:
    if early := quick_check(raw):
        return early
    url = normalize_url(raw)
    assert url is not None

    try:
        resp = fetcher.get(url)
    except BlockedByRobots:
        return CheckResult(BLOCKED, url, message=MESSAGES[BLOCKED])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410):
            return CheckResult(NOT_FOUND, url, message=MESSAGES[NOT_FOUND])
        return CheckResult(
            ERROR, url, message=f"The site answered with HTTP {exc.response.status_code}."
        )
    except httpx.TimeoutException:
        return CheckResult(ERROR, url, message="The site took too long to answer.")
    except httpx.HTTPError:
        return CheckResult(ERROR, url, message="Could not reach the site.")

    # Sites often redirect a removed listing to a search page.
    final_url = str(resp.url)
    parse = parser_for(final_url)
    if parse is None:
        return CheckResult(
            NOT_FOUND, url, message="The site redirected away from this listing, so it is gone."
        )
    listing = parse(resp.text, final_url)
    if listing is None:
        return CheckResult(ERROR, url, message="The page could not be read.")
    return CheckResult(OK, url, listing=listing)
