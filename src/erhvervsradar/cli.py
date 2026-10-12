"""Command line entry point.

erhvervsradar crawl --source all
erhvervsradar report
erhvervsradar export data/listings.csv
erhvervsradar save-fixture <url> tests/fixtures/edc_listing.html
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from .fetcher import Fetcher
from .report import build_report, export_csv
from .sources import SOURCES
from .storage import RunResult, Storage

app = typer.Typer(add_completion=False, help="Crawl Danish commercial lease listings.")
log = logging.getLogger("erhvervsradar")

DEFAULT_DB = Path("data/listings.db")


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.command()
def crawl(
    source: str = typer.Option("all", help=f"One of: all, {', '.join(SOURCES)}"),
    db: Path = typer.Option(DEFAULT_DB, help="SQLite database file"),
    limit: int | None = typer.Option(None, help="Stop after N listings per source (for testing)"),
    browser: bool = typer.Option(False, help="Allow headless-browser fallback for JS sites"),
    max_error_rate: float = typer.Option(0.2, help="Fail the run if more pages than this fail"),
) -> None:
    """Crawl one or all sources and update the database."""
    names = list(SOURCES) if source == "all" else [source]
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        raise typer.BadParameter(f"Unknown source(s): {unknown}. Choose from {list(SOURCES)}")

    storage = Storage(db)
    failed = False
    try:
        for name in names:
            source_cls = SOURCES[name]
            fetcher = Fetcher(min_delay=source_cls.min_delay)
            adapter = source_cls(fetcher, limit=limit, use_browser=browser)
            run_id, started = storage.start_run(name)
            result = RunResult()
            log.info("== %s: crawling", name)
            try:
                for listing in adapter.crawl():
                    storage.save(listing, result, seen_at=started)
            except Exception as exc:  # e.g. sitemap down: record it, move on to the next site
                log.exception("== %s: crawl aborted: %s", name, exc)
                adapter.stats.errors.append(f"aborted: {exc}")
                adapter.stats.complete = False
            finally:
                storage.commit()
                fetcher.close()

            stats = adapter.stats
            error_rate = len(stats.errors) / max(stats.pages_fetched + len(stats.errors), 1)
            # Only a complete, mostly successful run can tell us what has left the site.
            # Otherwise a network outage would mark every listing as removed.
            trustworthy = stats.complete and result.found > 0 and error_rate <= max_error_rate
            if trustworthy:
                storage.mark_removed(name, started, result)
            storage.finish_run(
                run_id, result, stats.pages_fetched, len(stats.errors), stats.complete
            )

            log.info(
                "== %s: %d found, %d new, %d changed, %d removed, %d errors, %d requests",
                name,
                result.found,
                result.new,
                result.changed,
                result.removed,
                len(stats.errors),
                fetcher.requests_made,
            )
            if result.found == 0 or error_rate > max_error_rate:
                log.error(
                    "== %s looks broken (found=%d, error rate=%.0f%%). Check the site and adapter.",
                    name,
                    result.found,
                    100 * error_rate,
                )
                failed = True
    finally:
        storage.close()

    if failed:
        raise typer.Exit(code=1)  # makes the scheduled GitHub Action go red


@app.command()
def report(
    db: Path = typer.Option(DEFAULT_DB),
    out: Path | None = typer.Option(None, help="Write Markdown here instead of printing"),
) -> None:
    """Print a data-quality and market report."""
    storage = Storage(db)
    text = build_report(storage)
    storage.close()
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"Report written to {out}")
    else:
        typer.echo(text)


@app.command()
def export(
    path: Path = typer.Argument(Path("data/listings.csv")),
    db: Path = typer.Option(DEFAULT_DB),
    all_statuses: bool = typer.Option(False, "--all", help="Include rented/removed listings"),
) -> None:
    """Export listings to CSV."""
    storage = Storage(db)
    count = export_csv(storage, path, include_inactive=all_statuses)
    storage.close()
    typer.echo(f"Exported {count} listings to {path}")


@app.command("save-fixture")
def save_fixture(url: str, path: Path, browser: bool = False) -> None:
    """Save a real page as a test fixture, so parser changes can be tested offline."""
    fetcher = Fetcher()
    try:
        html = fetcher.get_rendered(url) if browser else fetcher.get_text(url)
    finally:
        fetcher.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    typer.echo(f"Saved {len(html):,} characters to {path}")


if __name__ == "__main__":
    app()
