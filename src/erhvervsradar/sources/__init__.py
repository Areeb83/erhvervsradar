from .base import Source
from .edc import EdcSource
from .savills import SavillsSource

# To add a site: write sources/<site>.py with a Source subclass and register it here.
SOURCES: dict[str, type[Source]] = {
    EdcSource.name: EdcSource,
    SavillsSource.name: SavillsSource,
}

__all__ = ["SOURCES", "Source"]
