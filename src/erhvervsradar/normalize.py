"""Turn the messy text found on Danish listing pages into clean values.

Danish sites write numbers as "1.281" (one thousand two hundred eighty-one)
and "2.150,50" (decimal comma), sizes as "601 til 1.281 m²" and prices as
"450.750 kr. - 1.402.695 kr.". Everything here is plain, testable functions.
"""

from __future__ import annotations

import re
import unicodedata

# Invisible characters that sites sprinkle into text and that break regexes.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍﻿\xad"), None)

_NUMBER = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?"


def clean_text(text: str | None) -> str:
    """Collapse whitespace and remove invisible characters."""
    if not text:
        return ""
    text = text.translate(_INVISIBLE).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_danish_number(text: str | None) -> float | None:
    """'382.700 kr.' -> 382700.0, '2.150,50' -> 2150.5, '178 m²' -> 178.0."""
    match = re.search(_NUMBER, clean_text(text))
    if not match:
        return None
    return float(match.group(0).replace(".", "").replace(",", "."))


def parse_range(text: str | None) -> tuple[float | None, float | None]:
    """Parse a single value or a range into (min, max).

    '601 til 1.281 m²'               -> (601, 1281)
    '450.750 kr. - 1.402.695 kr.'    -> (450750, 1402695)
    '178 m²'                         -> (178, 178)
    """
    numbers = [parse_danish_number(n) for n in re.findall(_NUMBER, clean_text(text))]
    numbers = [n for n in numbers if n is not None]
    if not numbers:
        return None, None
    return min(numbers[:2]), max(numbers[:2])


def to_int(value: float | None) -> int | None:
    return None if value is None else int(round(value))


# Each site uses its own words for property types. We map them all onto
# a small shared vocabulary so listings from different sites can be compared.
_PROPERTY_TYPES = {
    "office": ["kontor", "klinik", "kontorhotel", "coworking"],
    "warehouse_industrial": ["lager", "produktion", "logistik", "industri", "værksted"],
    "retail": ["butik", "detail", "retail", "showroom"],
    "hospitality": ["hotel", "restaurant", "cafe"],
    "mixed": ["blandet"],
}


def normalize_property_type(raw: str | None) -> str:
    raw = clean_text(raw).lower()
    for normalized, keywords in _PROPERTY_TYPES.items():
        if any(k in raw for k in keywords):
            return normalized
    return "other"


def ascii_fold(text: str) -> str:
    """'Søborg Hovedgade' -> 'soeborg hovedgade' (how Danish URLs spell things)."""
    text = text.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def building_key(address: str | None, zip_code: str | None) -> str | None:
    """A key that is identical for the same building on different sites.

    'Vester Voldgade 104 2 TV', '1552'  -> '1552|vester voldgade 104'
    'Vester Voldgade 104, 2. tv.', '1552' -> '1552|vester voldgade 104'

    Floors and doors are dropped on purpose: sites write them inconsistently,
    so we match at building level and treat a hit as a *possible* duplicate.
    """
    if not address or not zip_code:
        return None
    folded = ascii_fold(clean_text(address)).replace("…", "")
    match = re.match(r"([a-z .'-]+?)\s*(\d+\s*[a-z]?)\b", folded)
    if not match:
        return None
    street = re.sub(r"\s+", " ", match.group(1)).strip(" .-")
    number = match.group(2).replace(" ", "")
    return f"{zip_code}|{street} {number}"
