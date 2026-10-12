import httpx
import pytest

from erhvervsradar import fetcher as fetcher_module
from erhvervsradar.fetcher import USER_AGENT, BlockedByRobots, Fetcher

ROBOTS = """
User-agent: *
Disallow: /private/
Crawl-delay: 7
"""


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """Record sleeps instead of waiting, so tests run instantly."""
    slept: list[float] = []
    monkeypatch.setattr(fetcher_module.time, "sleep", slept.append)
    return slept


def fake_site(responses: dict[str, list[httpx.Response]], seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        queue = responses.get(request.url.path)
        return queue.pop(0) if queue else httpx.Response(404)

    return httpx.MockTransport(handler)


def test_respects_robots_txt():
    seen: list[httpx.Request] = []
    f = Fetcher(min_delay=0, transport=fake_site({}, seen))
    with pytest.raises(BlockedByRobots):
        f.get("https://example.dk/private/page")
    assert [r.url.path for r in seen] == ["/robots.txt"]  # the page itself was never requested


def test_sends_honest_user_agent_and_uses_crawl_delay(no_sleeping):
    seen: list[httpx.Request] = []
    pages = {"/a": [httpx.Response(200, text="A")], "/b": [httpx.Response(200, text="B")]}
    f = Fetcher(min_delay=1, transport=fake_site(pages, seen))

    assert f.get_text("https://example.dk/a") == "A"
    assert f.get_text("https://example.dk/b") == "B"

    assert seen[-1].headers["user-agent"] == USER_AGENT
    # robots.txt says 7 seconds, which beats our own minimum of 1.
    assert any(6 < s <= 7 for s in no_sleeping)


def test_retries_on_server_errors_then_succeeds():
    seen: list[httpx.Request] = []
    pages = {"/flaky": [httpx.Response(503), httpx.Response(429), httpx.Response(200, text="ok")]}
    f = Fetcher(min_delay=0, transport=fake_site(pages, seen))
    assert f.get_text("https://example.dk/flaky") == "ok"
    assert f.requests_made == 3


def test_does_not_retry_not_found():
    seen: list[httpx.Request] = []
    f = Fetcher(min_delay=0, transport=fake_site({}, seen))
    with pytest.raises(httpx.HTTPStatusError):
        f.get("https://example.dk/missing")
    assert f.requests_made == 1


@pytest.mark.parametrize(("status", "allowed"), [(404, True), (503, False)])
def test_robots_txt_errors_follow_rfc_9309(status, allowed):
    """No robots.txt (404) allows everything; a server error (503) allows nothing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(status)
        return httpx.Response(200, text="page")

    f = Fetcher(min_delay=0, transport=httpx.MockTransport(handler))
    assert f.allowed("https://example.dk/page") is allowed
