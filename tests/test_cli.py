"""End-to-end: the real CLI against a fake internet built from the fixtures."""

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from erhvervsradar import fetcher as fetcher_module
from erhvervsradar.cli import app
from erhvervsradar.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def savills_site(site_up: dict) -> httpx.MockTransport:
    page = (FIXTURES / "savills_list_sample.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        if not site_up["value"]:
            return httpx.Response(404)
        if request.url.path.endswith("/kontor/danmark") and not request.url.query:
            return httpx.Response(200, text=page)
        return httpx.Response(200, text="<html><body></body></html>")

    return httpx.MockTransport(handler)


@pytest.fixture
def fake_internet(monkeypatch):
    site_up = {"value": True}
    original_init = fetcher_module.Fetcher.__init__

    def init(self, *args, **kwargs):
        kwargs.update(transport=savills_site(site_up), min_delay=0)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(fetcher_module.Fetcher, "__init__", init)
    monkeypatch.setattr(fetcher_module.time, "sleep", lambda _: None)
    return site_up


def test_crawl_report_and_export(fake_internet, tmp_path):
    db = tmp_path / "listings.db"
    result = runner.invoke(app, ["crawl", "--source", "savills", "--db", str(db)])
    assert result.exit_code == 0, result.output

    report = runner.invoke(app, ["report", "--db", str(db)])
    assert "| savills | 3 | 3 |" in report.output

    csv_path = tmp_path / "out.csv"
    runner.invoke(app, ["export", str(csv_path), "--db", str(db)])
    assert len(csv_path.read_text(encoding="utf-8").splitlines()) == 4  # header + 3


def test_site_outage_fails_loudly_and_keeps_data(fake_internet, tmp_path):
    db = tmp_path / "listings.db"
    runner.invoke(app, ["crawl", "--source", "savills", "--db", str(db)])

    fake_internet["value"] = False  # the site goes down
    result = runner.invoke(app, ["crawl", "--source", "savills", "--db", str(db)])

    assert result.exit_code == 1  # a scheduled run would turn red
    storage = Storage(db)
    assert len(storage.active_listings()) == 3  # nothing wrongly marked as removed
    storage.close()


def test_unknown_source_is_rejected(tmp_path):
    result = runner.invoke(app, ["crawl", "--source", "nope", "--db", str(tmp_path / "x.db")])
    assert result.exit_code != 0
