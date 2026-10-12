from pathlib import Path

import httpx
import pytest

from erhvervsradar import check as check_module
from erhvervsradar import fetcher as fetcher_module
from erhvervsradar.check import (
    BLOCKED,
    ERROR,
    INVALID_URL,
    NOT_A_LISTING,
    NOT_FOUND,
    OK,
    UNSUPPORTED_SITE,
    check_url,
    normalize_url,
)
from erhvervsradar.fetcher import USER_AGENT, Fetcher
from erhvervsradar.sources.savills import parse_listing_page

FIXTURES = Path(__file__).parent / "fixtures"
EDC = "https://www.edc.dk/erhverv/erhvervslejemaal/kontor/1552-koebenhavn-v/vester-voldgade-104/114058334/"
SAVILLS = (
    "https://search.savills.com/dk/da/ejendomsoplysninger/dff8a9c0-92ab-4c6e-afce-f1d7e327c393"
)
ROBOTS = "User-agent: *\nAllow: /\nDisallow: /umbraco\n"


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr(fetcher_module.time, "sleep", lambda _: None)


def fetcher_for(pages: dict[str, httpx.Response | Exception], seen: list | None = None) -> Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path_url = str(request.url)
        if request.url.path == "/robots.txt" and path_url not in pages:
            return httpx.Response(200, text=ROBOTS)
        answer = pages.get(path_url, httpx.Response(404))
        if isinstance(answer, Exception):
            raise answer
        return answer

    return Fetcher(min_delay=0, max_retries=1, transport=httpx.MockTransport(handler))


def page(name: str) -> httpx.Response:
    return httpx.Response(200, text=(FIXTURES / name).read_text(encoding="utf-8"))


# -- Savills single listing page --------------------------------------------------


def test_savills_listing_page_ignores_menu_and_footer():
    html = (FIXTURES / "savills_detail_sample.html").read_text(encoding="utf-8")
    listing = parse_listing_page(html, SAVILLS)
    assert listing.address == "Rygårds Allé 133"
    assert (listing.zip_code, listing.city) == ("2900", "Hellerup")
    assert listing.property_type == "office"  # not "warehouse" from the site menu
    assert (listing.size_m2_min, listing.size_m2_max) == (696, 696)  # not 5.000 m² from the menu
    assert listing.yearly_rent_min == 1_009_200  # not "Udbudspris 99 kr." from the footer
    assert listing.rent_per_m2 == 1450.0


# -- check_url --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("www.edc.dk/erhverv/?utm_source=x", "https://www.edc.dk/erhverv/"),
        ("  http://WWW.EDC.DK/a#top ", "https://www.edc.dk/a"),
        ("Vester Voldgade 104", None),
        ("", None),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_reads_edc_and_savills_listings():
    f = fetcher_for(
        {EDC: page("edc_listing_sample.html"), SAVILLS: page("savills_detail_sample.html")}
    )
    edc = check_url(EDC + "?utm_source=newsletter", f)
    assert edc.status == OK and edc.listing.yearly_rent_min == 382_700
    savills = check_url(SAVILLS, f)
    assert savills.status == OK and savills.listing.address == "Rygårds Allé 133"


def test_answers_without_downloading_when_possible():
    seen: list[httpx.Request] = []
    f = fetcher_for({}, seen)
    assert check_url("not a link", f).status == INVALID_URL
    assert check_url("https://www.boligsiden.dk/adresse/x", f).status == UNSUPPORTED_SITE
    assert check_url("https://www.edc.dk/erhverv/", f).status == NOT_A_LISTING
    assert seen == []


def test_sends_honest_user_agent():
    seen: list[httpx.Request] = []
    check_url(EDC, fetcher_for({EDC: page("edc_listing_sample.html")}, seen))
    assert seen[-1].headers["user-agent"] == USER_AGENT


def test_robots_txt_is_respected():
    f = fetcher_for(
        {
            "https://www.edc.dk/robots.txt": httpx.Response(
                200, text="User-agent: *\nDisallow: /erhverv/"
            )
        }
    )
    assert check_url(EDC, f).status == BLOCKED


def test_gone_listings():
    assert check_url(EDC, fetcher_for({})).status == NOT_FOUND  # 404
    redirected = fetcher_for(
        {
            EDC: httpx.Response(301, headers={"Location": "https://www.edc.dk/erhverv/"}),
            "https://www.edc.dk/erhverv/": httpx.Response(200, text="<html>search</html>"),
        }
    )
    assert check_url(EDC, redirected).status == NOT_FOUND


def test_errors_never_raise():
    busy = check_url(EDC, fetcher_for({EDC: httpx.Response(503)}))
    assert busy.status == ERROR and "503" in busy.message
    down = check_url(EDC, fetcher_for({EDC: httpx.ConnectError("refused")}))
    assert down.status == ERROR and down.message == "Could not reach the site."
    slow = check_url(EDC, fetcher_for({EDC: httpx.ReadTimeout("slow")}))
    assert slow.message == "The site took too long to answer."


def test_only_known_sites_are_in_the_allowlist():
    assert set(check_module.HOST_DELAYS) == {"www.edc.dk", "edc.dk", "search.savills.com"}
    assert check_module.HOST_DELAYS["search.savills.com"] >= 5
