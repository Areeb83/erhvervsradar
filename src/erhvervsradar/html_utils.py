"""Small helpers for reading HTML in a way that survives redesigns.

Instead of depending on CSS class names (which change often), we read the
page's visible text in order and look for the *labels* a human reads,
like "Årlig leje" followed by its value. Labels change far less often.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .normalize import clean_text


def make_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return soup


def visible_texts(soup: BeautifulSoup, stop_at: str | None = None) -> list[str]:
    """All visible text pieces in page order. Optionally stop at a heading
    such as 'Lignende ejendomme' so related-listing widgets don't leak in."""
    texts = [clean_text(t) for t in soup.stripped_strings]
    texts = [t for t in texts if t]
    if stop_at and stop_at in texts:
        texts = texts[: texts.index(stop_at)]
    return texts


def values_after_labels(texts: list[str], labels: set[str]) -> dict[str, str]:
    """{'Årlig leje': '382.700 kr./år', ...} — the first value after each label.

    Works for <dt>/<dd>, table rows, or two <div>s side by side, because it
    only relies on the label coming right before its value in reading order.
    """
    found: dict[str, str] = {}
    for i, text in enumerate(texts):
        if text in labels and text not in found:
            for candidate in texts[i + 1 : i + 3]:
                if candidate not in labels:
                    found[text] = candidate
                    break
    return found
