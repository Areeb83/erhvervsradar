"""Savills Denmark (search.savills.com) — a JavaScript single-page app.

The search pages are a React app. Today the server also sends the listing
cards in the initial HTML, so a plain HTTP request is enough and much cheaper.
If that ever stops (the cards only appear after JavaScript runs), the adapter
falls back to rendering the page in a headless browser with --browser.

Savills asks for 5 seconds between requests, so we use that as our delay.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..html_utils import make_soup, visible_texts
from ..models import AVAILABLE, RENTED, Listing
from ..normalize import clean_text, normalize_property_type, parse_range, to_int
from .base import Source

log = logging.getLogger(__name__)

BASE = "https://search.savills.com"
LIST_URL = BASE + "/dk/da/liste/forretningsmaessig/ejendom-til-leje/{category}/danmark"
CATEGORIES = ["kontor", "logistik-og-industri", "detail", "other-commercial"]

DETAIL_LINK = re.compile(r"/ejendomsoplysninger/(?P<id>[0-9a-f-]{36})", re.I)
ZIP_CITY = re.compile(r"^(?P<zip>\d{4})\s+(?P<city>\D.*)$")
SIZE = re.compile(r"(\d[\d.]*)(?:\s*til\s*(\d[\d.]*))?\s*m²")
PRICE = re.compile(r"Udbudspris\s*(\d[\d.]*)\s*kr\.?(?:\s*-\s*(\d[\d.]*)\s*kr\.?)?")
MAX_PAGES_PER_CATEGORY = 30


class SavillsSource(Source):
    name = "savills"
    min_delay = 5.0

    def crawl(self) -> Iterator[Listing]:
        seen: set[str] = set()
        for category in CATEGORIES:
            url: str | None = LIST_URL.format(category=category)
            pages = 0
            while url and pages < MAX_PAGES_PER_CATEGORY:
                try:
                    html, listings = self._fetch_list_page(url, category)
                except Exception as exc:
                    log.warning("savills: failed on %s: %s", url, exc)
                    self.stats.errors.append(f"{url}: {exc}")
                    break
                pages += 1
                self.stats.pages_fetched += 1

                if not listings:
                    if pages == 1:
                        log.warning(
                            "savills: no listing cards on %s. Either the category is empty, or "
                            "the site now renders with JavaScript only (re-run with --browser).",
                            url,
                        )
                    break  # past the last page

                for listing in listings:
                    if listing.source_id in seen:
                        continue  # same property shown under two categories
                    seen.add(listing.source_id)
                    self.stats.listings_parsed += 1
                    yield listing
                    if self.limit is not None and len(seen) >= self.limit:
                        self.stats.complete = False
                        return

                url = next_page_url(html, url)

    def _fetch_list_page(self, url: str, category: str) -> tuple[str, list[Listing]]:
        """Cheap plain HTTP first; a real browser only when the HTML has no cards."""
        html = self.fetcher.get_text(url)
        listings = parse_list_page(html, category)
        if not listings and self.use_browser:
            log.info("savills: no cards in plain HTML, rendering %s in a headless browser", url)
            html = self.fetcher.get_rendered(url)
            listings = parse_list_page(html, category)
        return html, listings


def parse_list_page(html: str, category: str) -> list[Listing]:
    """Turn one search-results page into listings, one per result card."""
    soup = make_soup(html)
    listings = []
    for listing_id, card in _cards(soup).items():
        listing = _parse_card(card, listing_id, category)
        if listing:
            listings.append(listing)
    return listings


def _cards(soup: BeautifulSoup) -> dict[str, Tag]:
    """Find each result card without relying on CSS class names.

    Start from a link to a listing detail page and walk up the HTML tree to
    the biggest element that still belongs to that one listing only.
    """
    cards: dict[str, Tag] = {}
    for link in soup.find_all("a", href=DETAIL_LINK):
        listing_id = DETAIL_LINK.search(link["href"])["id"].lower()
        if listing_id in cards:
            continue
        card, node = None, link
        for _ in range(10):
            node = node.parent
            if node is None or node.name in ("body", "html"):
                break
            ids = {
                DETAIL_LINK.search(a["href"])["id"].lower()
                for a in node.find_all("a", href=DETAIL_LINK)
            }
            if len(ids) > 1:
                break  # we climbed into the list holding several cards
            if "m²" in node.get_text():
                card = node
        if card is not None:
            cards[listing_id] = card
    return cards


def _parse_card(card: Tag, listing_id: str, category: str) -> Listing | None:
    lines = [clean_text(t) for t in card.stripped_strings]
    lines = [t for t in lines if t]
    text = " ".join(lines)

    heading = card.find(["h2", "h3", "h4"])
    address = clean_text(heading.get_text()).rstrip("…").strip(" ,") if heading else None

    zip_code = city = None
    for line in lines:
        if m := ZIP_CITY.match(line):
            zip_code, city = m["zip"], m["city"]
            break

    size_min, size_max = (None, None)
    if m := SIZE.search(text):
        size_min, size_max = parse_range(m.group(0))

    rent_min, rent_max = (None, None)
    if m := PRICE.search(text):
        rent_min, rent_max = parse_range(m.group(0))
        if "Månedlig leje" in text:  # convert monthly prices to yearly
            rent_min, rent_max = (v * 12 if v else v for v in (rent_min, rent_max))

    link = card.find("a", href=DETAIL_LINK)
    url = urljoin(BASE, link["href"]) if link else f"{BASE}/dk/da/ejendomsoplysninger/{listing_id}"
    return Listing(
        source="savills",
        source_id=listing_id,
        url=url,
        title=address,
        address=address,
        zip_code=zip_code,
        city=city,
        property_type=normalize_property_type(category),
        size_m2_min=to_int(size_min),
        size_m2_max=to_int(size_max),
        yearly_rent_min=to_int(rent_min),
        yearly_rent_max=to_int(rent_max),
        extra={"is_new": "Ny" in lines},
    )


def next_page_url(html: str, current_url: str) -> str | None:
    """Follow the 'next page' link if the page has one."""
    soup = BeautifulSoup(html, "lxml")
    link = soup.find("a", rel="next") or soup.find(
        "a", attrs={"aria-label": re.compile(r"næste|next", re.I)}
    )
    if link is None:
        link = soup.find("a", string=re.compile(r"^\s*(Næste|Next)\s*$", re.I))
    if link and link.get("href"):
        nxt = urljoin(current_url, link["href"])
        return nxt if nxt != current_url else None
    return None


# -- Single listing pages -------------------------------------------------------
# Used by the web app, where people paste links to individual listings.

LISTING_URL = re.compile(
    r"^https://search\.savills\.com/dk/[a-z]{2}/ejendomsoplysninger/(?P<id>[0-9a-f-]{36})/?$", re.I
)
# <title>Rygårds Allé 133, 2900 Hellerup | Ejendom til leje | Savills</title>
LISTING_TITLE = re.compile(r"^(?P<address>.+?),\s*(?P<zip>\d{4})\s+(?P<city>[^|]+?)\s*\|")
# Type tags as Savills writes them on the listing itself.
LISTING_TYPES = {
    "Kontor",
    "Klinik",
    "Logistik og industri",
    "Lager",
    "Detail",
    "Butik",
    "Investering",
    "Projekt og Erhvervsgrund",
    "Øvrige ejendomstyper",
}
# Where the listing ends and site-wide sections begin. Not "Kontakt": the real page
# has a "Kontakt" button between the address and the size and price.
LISTING_END = ("Lokal information", "Erhvervsejendomme")


def parse_listing_page(html: str, url: str) -> Listing | None:
    """One Savills listing page -> Listing.

    Only the listing itself is read: from its address heading to the contact
    section. The site menu above it says "Kontorejendomme", "Lager & logistik"
    and so on, which would otherwise be mistaken for this listing's type.
    """
    match = LISTING_URL.match(url)
    if not match:
        return None
    soup = make_soup(html)
    title = clean_text(soup.title.string if soup.title else "")
    title_match = LISTING_TITLE.match(title)
    address = clean_text(title_match["address"]) if title_match else None

    texts = visible_texts(soup.body or soup)  # the body only: <title> also starts with the address
    start = next((i for i, t in enumerate(texts) if address and t.startswith(address)), 0)
    end = min(
        (texts.index(m, start + 1) for m in LISTING_END if m in texts[start + 1 :]),
        default=len(texts),
    )
    texts = texts[start:end]
    joined = " ".join(texts)

    size_min, size_max = parse_range(m.group(0)) if (m := SIZE.search(joined)) else (None, None)
    rent_min, rent_max = parse_range(m.group(0)) if (m := PRICE.search(joined)) else (None, None)
    if "Månedlig leje" in texts:
        rent_min, rent_max = (v * 12 if v else v for v in (rent_min, rent_max))

    return Listing(
        source="savills",
        source_id=match["id"].lower(),
        url=url,
        title=title or None,
        address=address,
        zip_code=title_match["zip"] if title_match else None,
        city=clean_text(title_match["city"]) if title_match else None,
        property_type=normalize_property_type(next((t for t in texts if t in LISTING_TYPES), "")),
        size_m2_min=to_int(size_min),
        size_m2_max=to_int(size_max),
        yearly_rent_min=to_int(rent_min),
        yearly_rent_max=to_int(rent_max),
        status=RENTED if "Udlejet" in texts else AVAILABLE,
    )
