from pathlib import Path

from erhvervsradar.models import AVAILABLE, RENTED
from erhvervsradar.sources.edc import parse_listing, urls_from_sitemap
from erhvervsradar.sources.savills import next_page_url, parse_list_page

FIXTURES = Path(__file__).parent / "fixtures"
EDC_URL = (
    "https://www.edc.dk/erhverv/erhvervslejemaal/kontor/1552-koebenhavn-v/"
    "vester-voldgade-104/114058334/"
)


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# -- EDC ----------------------------------------------------------------------


def test_edc_parses_all_core_fields():
    listing = parse_listing(read("edc_listing_sample.html"), EDC_URL)

    assert listing.source_id == "114058334"
    assert listing.address == "Vester Voldgade 104 2 TV"
    assert listing.zip_code == "1552"
    assert listing.city == "København V"
    assert listing.property_type == "office"
    assert listing.size_m2_min == listing.size_m2_max == 178
    assert listing.yearly_rent_min == 382700
    assert listing.rent_per_m2 == 2150.0
    assert listing.status == AVAILABLE
    assert listing.extra["Energimærke"] == "C"


def test_edc_ignores_similar_listings_widget():
    """The 'Lignende ejendomme' block has its own price and an 'Udlejet' badge;
    neither may leak into the main listing."""
    listing = parse_listing(read("edc_listing_sample.html"), EDC_URL)
    assert listing.yearly_rent_min != 1_000_000
    assert listing.status == AVAILABLE


def test_edc_detects_rented_listing_without_price():
    url = (
        "https://www.edc.dk/erhverv/erhvervslejemaal/lager-produktion/6230-roedekro/"
        "kometvej-10/62001228/"
    )
    listing = parse_listing(read("edc_rented_sample.html"), url)
    assert listing.status == RENTED
    assert listing.property_type == "warehouse_industrial"
    assert listing.size_m2_min == 1250
    assert listing.yearly_rent_min is None
    assert listing.city == "Rødekro"


def test_edc_sitemap_keeps_only_commercial_leases():
    xml = (FIXTURES / "edc_sitemap_sample.xml").read_bytes()
    urls = urls_from_sitemap(xml, since_days=None)
    assert len(urls) == 3
    assert all("/erhvervslejemaal/" in u for u in urls)
    assert urls[0].endswith("/27801563/")  # newest first


def test_edc_sitemap_drops_old_cases():
    xml = (FIXTURES / "edc_sitemap_sample.xml").read_bytes()
    urls = urls_from_sitemap(xml, since_days=1000)
    assert not any("67011161" in u for u in urls)  # last changed in 2010


def test_edc_rejects_non_lease_urls():
    assert parse_listing("<html></html>", "https://www.edc.dk/alle-boliger/villa/x/y/1/") is None


# -- Savills ------------------------------------------------------------------


def test_savills_parses_one_listing_per_card():
    listings = parse_list_page(read("savills_list_sample.html"), "kontor")
    assert [x.source_id for x in listings] == [
        "a80bac85-cb55-4f31-8b55-1fc457e395cb",
        "1087c4ba-9c56-4aae-80c8-b90dcf9edab3",
        "dff8a9c0-92ab-4c6e-afce-f1d7e327c393",
    ]


def test_savills_card_fields_and_ranges():
    by_id = {x.source_id: x for x in parse_list_page(read("savills_list_sample.html"), "kontor")}

    single = by_id["a80bac85-cb55-4f31-8b55-1fc457e395cb"]
    assert single.address == "Rosenkæret 13 2.th."
    assert (single.zip_code, single.city) == ("2860", "Søborg")
    assert (single.size_m2_min, single.size_m2_max) == (288, 288)
    assert single.yearly_rent_min == 228470
    assert single.property_type == "office"
    assert single.url.startswith("https://search.savills.com/dk/da/ejendomsoplysninger/")

    ranged = by_id["1087c4ba-9c56-4aae-80c8-b90dcf9edab3"]
    assert (ranged.size_m2_min, ranged.size_m2_max) == (601, 1281)
    assert (ranged.yearly_rent_min, ranged.yearly_rent_max) == (450750, 1402695)
    assert ranged.rent_per_m2 is None  # a range has no single price per m²
    assert ranged.extra["is_new"] is True


def test_savills_follows_next_page_link():
    current = (
        "https://search.savills.com/dk/da/liste/forretningsmaessig/ejendom-til-leje/kontor/danmark"
    )
    assert next_page_url(read("savills_list_sample.html"), current) == current + "?Page=2"


def test_savills_empty_page_gives_no_listings():
    assert parse_list_page("<html><body><div id='root'></div></body></html>", "kontor") == []
