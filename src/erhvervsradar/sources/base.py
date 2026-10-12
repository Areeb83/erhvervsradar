"""What every site adapter has to provide.

An adapter answers two questions about one website:
  1. Where are the listings?            -> crawl()
  2. Which part of a page is which field? -> its parse functions

Downloading, cleaning numbers, saving, and scheduling are shared code,
so adding a new site means writing one new file in this folder.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..fetcher import Fetcher
from ..models import Listing


@dataclass
class CrawlStats:
    pages_fetched: int = 0
    listings_parsed: int = 0
    errors: list[str] = field(default_factory=list)
    # False if we stopped early (--limit) — then we must not mark unseen
    # listings as removed, because we simply didn't look at them.
    complete: bool = True


class Source(ABC):
    name: str
    min_delay: float = 2.0  # seconds between requests to this site

    def __init__(self, fetcher: Fetcher, limit: int | None = None, use_browser: bool = False):
        self.fetcher = fetcher
        self.limit = limit
        self.use_browser = use_browser
        self.stats = CrawlStats()

    @abstractmethod
    def crawl(self) -> Iterator[Listing]:
        """Yield every listing currently on the site."""
