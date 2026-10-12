"""EDC Erhverv (edc.dk) — a classic server-rendered site.

Discovery: EDC publishes a sitemap of all its cases. We keep only commercial
lease URLs (/erhverv/erhvervslejemaal/...) changed recently, instead of
clicking through search pages. That is fewer requests and kinder to the site.

Parsing: each listing page shows label/value pairs ("Årlig leje" -> "382.700 kr./år"),
so we read values by label rather than by CSS class.
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from lxml import etree

from ..html_utils import make_soup, values_after_labels, visible_texts
from ..models import AVAILABLE, RENTED, Listing
from ..normalize import (
    clean_text,
    normalize_property_type,
    parse_danish_number,
    to_int,
)
from .base import Source

log = logging.getLogger(__name__)

SITEMAP_URL = "https://www.edc.dk/sitemap-cases.xml"

# https://www.edc.dk/erhverv/erhvervslejemaal/kontor/1552-koebenhavn-v/vester-voldgade-104/114058334/
URL_PATTERN = re.compile(
    r"/erhverv/erhvervslejemaal/(?P<type>[^/]+)/(?P<zip>\d{4})-[^/]+/[^/]+/(?P<id>\d+)/?$"
)

# <title>Vester Voldgade 104 2 TV, Kontor til leje i København V</title>
TITLE_PATTERN = re.compile(r"^(?P<address>.+?), (?P<kind>.+?) til leje i (?P<city>.+)$")

LABELS = {
    "Adresse",
    "Sagsnr.",
    "Ejendomstype",
    "Etageareal",
    "Areal",
    "Årlig leje",
    "Månedlig leje",
    "Årlig leje pr. m²",
    "Depositum",
    "Overtagelse",
    "Energimærke",
    "Byggeår / Ombygget",
}


class EdcSource(Source):
    name = "edc"
    min_delay = 2.0

    def __init__(self, *args, since_days: int = 180, **kwargs):
        super().__init__(*args, **kwargs)
        self.since_days = since_days

    # 1. Where are the listings? -------------------------------------------

    def discover_urls(self) -> list[str]:
        xml = self.fetcher.get(SITEMAP_URL).content
        self.stats.pages_fetched += 1
        return urls_from_sitemap(xml, since_days=self.since_days)

    # 2. Which part of the page is which field? ----------------------------

    def crawl(self) -> Iterator[Listing]:
        urls = self.discover_urls()
        log.info("edc: %d lease URLs in sitemap (last %d days)", len(urls), self.since_days)
        if self.limit is not None and len(urls) > self.limit:
            urls = urls[: self.limit]
            self.stats.complete = False

        for url in urls:
            try:
                html = self.fetcher.get_text(url)
                self.stats.pages_fetched += 1
                listing = parse_listing(html, url)
            except Exception as exc:  # one broken page must not stop the whole run
                log.warning("edc: failed on %s: %s", url, exc)
                self.stats.errors.append(f"{url}: {exc}")
                continue
            if listing:
                self.stats.listings_parsed += 1
                yield listing


def urls_from_sitemap(xml: bytes, since_days: int | None = None) -> list[str]:
    """Commercial lease URLs from EDC's sitemap, newest first.

    The sitemap also contains homes, farms, and cases from 2010, so we filter
    by URL pattern and by <lastmod>.
    """
    cutoff = datetime.now(UTC) - timedelta(days=since_days) if since_days else None
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    found: list[tuple[str, str]] = []

    for _, node in etree.iterparse(io.BytesIO(xml), tag=f"{ns}url"):
        loc = (node.findtext(f"{ns}loc") or "").strip()
        lastmod = (node.findtext(f"{ns}lastmod") or "").strip()
        node.clear()
        if not URL_PATTERN.search(loc):
            continue
        if cutoff and lastmod:
            try:
                if datetime.fromisoformat(lastmod.replace("Z", "+00:00")) < cutoff:
                    continue
            except ValueError:
                pass  # unparseable date: keep the URL rather than silently drop it
        found.append((lastmod, loc))

    found.sort(reverse=True)
    return [loc for _, loc in found]


def parse_listing(html: str, url: str) -> Listing | None:
    match = URL_PATTERN.search(url)
    if not match:
        return None

    soup = make_soup(html)
    # Stop before the "similar properties" widget so its prices don't leak in.
    texts = visible_texts(soup, stop_at="Lignende ejendomme")
    fields = values_after_labels(texts, LABELS)

    title = clean_text(soup.title.string if soup.title else "")
    title_match = TITLE_PATTERN.match(title)

    address = fields.get("Adresse") or (title_match and title_match["address"]) or None
    city = title_match["city"] if title_match else None
    raw_type = fields.get("Ejendomstype") or match["type"]

    size = parse_danish_number(fields.get("Etageareal") or fields.get("Areal"))
    yearly = parse_danish_number(fields.get("Årlig leje"))
    if yearly is None and fields.get("Månedlig leje"):
        yearly = parse_danish_number(fields["Månedlig leje"]) * 12

    # EDC keeps let properties online with an "Udlejet" badge.
    status = RENTED if "Udlejet" in texts else AVAILABLE

    return Listing(
        source="edc",
        source_id=match["id"],
        url=url,
        title=title or None,
        address=address,
        zip_code=match["zip"],
        city=city,
        property_type=normalize_property_type(raw_type),
        size_m2_min=to_int(size),
        size_m2_max=to_int(size),
        yearly_rent_min=to_int(yearly),
        yearly_rent_max=to_int(yearly),
        status=status,
        extra={
            k: fields[k]
            for k in ("Depositum", "Overtagelse", "Energimærke", "Byggeår / Ombygget")
            if k in fields
        },
    )
