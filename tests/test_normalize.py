import pytest

from erhvervsradar.normalize import (
    building_key,
    clean_text,
    normalize_property_type,
    parse_danish_number,
    parse_range,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("382.700 kr./år", 382700),
        ("1.281 m²", 1281),
        ("2.150,50 kr.", 2150.5),
        ("178 m²", 178),
        ("1.402.695 kr.", 1402695),
        ("Afregnes overfor forsyningsselskabet", None),
        (None, None),
    ],
)
def test_parse_danish_number(text, expected):
    assert parse_danish_number(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("601 til 1.281 m²", (601, 1281)),
        ("288 til 288 m²", (288, 288)),
        ("Udbudspris 450.750 kr. -​ 1.402.695 kr.", (450750, 1402695)),
        ("696 m²", (696, 696)),
        ("", (None, None)),
    ],
)
def test_parse_range(text, expected):
    assert parse_range(text) == expected


def test_clean_text_removes_invisible_characters():
    assert clean_text("  450.750 kr.​\xa0-  1 ") == "450.750 kr. - 1"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kontor", "office"),
        ("lager-produktion", "warehouse_industrial"),
        ("logistik-og-industri", "warehouse_industrial"),
        ("Butik", "retail"),
        ("detail", "retail"),
        ("hotel-restaurant", "hospitality"),
        ("blandet-ejendom", "mixed"),
        ("other-commercial", "other"),
    ],
)
def test_normalize_property_type(raw, expected):
    assert normalize_property_type(raw) == expected


def test_building_key_matches_same_building_written_differently():
    a = building_key("Vester Voldgade 104 2 TV", "1552")
    b = building_key("Vester Voldgade 104, 2. tv.", "1552")
    assert a == b == "1552|vester voldgade 104"


def test_building_key_folds_danish_letters_and_truncation():
    assert building_key("Rygårds Allé 133…", "2900") == "2900|rygaards alle 133"
    assert building_key("Søborg Hovedgade 55A", "2860") == "2860|soeborg hovedgade 55a"


def test_building_key_needs_address_and_zip():
    assert building_key(None, "1552") is None
    assert building_key("Vester Voldgade 104", None) is None
