"""SQLite storage that remembers listings between runs.

Each run compares what it found with what is already stored, so we know
which listings are new, which changed (price, size, status), and which have
disappeared from the site since last time.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import AVAILABLE, REMOVED, Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT,
    address         TEXT,
    zip_code        TEXT,
    city            TEXT,
    property_type   TEXT,
    size_m2_min     INTEGER,
    size_m2_max     INTEGER,
    yearly_rent_min INTEGER,
    yearly_rent_max INTEGER,
    rent_per_m2     REAL,
    building_key    TEXT,
    status          TEXT NOT NULL,
    extra           TEXT,
    content_hash    TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    last_changed    TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_building ON listings(building_key);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    pages_fetched   INTEGER,
    found           INTEGER,
    new             INTEGER,
    changed         INTEGER,
    removed         INTEGER,
    errors          INTEGER,
    complete        INTEGER
);
"""

COLUMNS = [
    "source",
    "source_id",
    "url",
    "title",
    "address",
    "zip_code",
    "city",
    "property_type",
    "size_m2_min",
    "size_m2_max",
    "yearly_rent_min",
    "yearly_rent_max",
    "rent_per_m2",
    "building_key",
    "status",
    "extra",
]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class RunResult:
    found: int = 0
    new: int = 0
    changed: int = 0
    removed: int = 0


class Storage:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def start_run(self, source: str) -> tuple[int, str]:
        started = now()
        cur = self.db.execute(
            "INSERT INTO runs (source, started_at) VALUES (?, ?)", (source, started)
        )
        self.db.commit()
        return cur.lastrowid, started

    def save(self, listing: Listing, result: RunResult, seen_at: str) -> str:
        """Insert or update one listing. Returns 'new', 'changed' or 'unchanged'."""
        result.found += 1
        data = listing.to_dict()
        values = [
            data[c] if c != "extra" else json.dumps(data["extra"], ensure_ascii=False)
            for c in COLUMNS
        ]
        new_hash = listing.content_hash()

        row = self.db.execute(
            "SELECT content_hash FROM listings WHERE source = ? AND source_id = ?",
            (listing.source, listing.source_id),
        ).fetchone()

        if row is None:
            self.db.execute(
                f"INSERT INTO listings ({', '.join(COLUMNS)}, content_hash, first_seen, "
                f"last_seen, last_changed) VALUES ({', '.join('?' * len(COLUMNS))}, ?, ?, ?, ?)",
                [*values, new_hash, seen_at, seen_at, seen_at],
            )
            result.new += 1
            return "new"

        if row["content_hash"] != new_hash:
            assignments = ", ".join(f"{c} = ?" for c in COLUMNS)
            self.db.execute(
                f"UPDATE listings SET {assignments}, content_hash = ?, last_seen = ?, "
                "last_changed = ? WHERE source = ? AND source_id = ?",
                [*values, new_hash, seen_at, seen_at, listing.source, listing.source_id],
            )
            result.changed += 1
            return "changed"

        self.db.execute(
            "UPDATE listings SET last_seen = ? WHERE source = ? AND source_id = ?",
            (seen_at, listing.source, listing.source_id),
        )
        return "unchanged"

    def mark_removed(self, source: str, run_started: str, result: RunResult) -> None:
        """Listings we did not see in this (complete) run have left the site."""
        cur = self.db.execute(
            "UPDATE listings SET status = ?, last_changed = ? "
            "WHERE source = ? AND last_seen < ? AND status != ?",
            (REMOVED, now(), source, run_started, REMOVED),
        )
        result.removed = cur.rowcount

    def finish_run(
        self, run_id: int, result: RunResult, pages: int, errors: int, complete: bool
    ) -> None:
        self.db.execute(
            "UPDATE runs SET finished_at = ?, pages_fetched = ?, found = ?, new = ?, "
            "changed = ?, removed = ?, errors = ?, complete = ? WHERE id = ?",
            (
                now(),
                pages,
                result.found,
                result.new,
                result.changed,
                result.removed,
                errors,
                int(complete),
                run_id,
            ),
        )
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()

    # -- reading -------------------------------------------------------------

    def active_listings(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM listings WHERE status = ? ORDER BY source, city, address",
            (AVAILABLE,),
        ).fetchall()

    def all_listings(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM listings").fetchall()

    def latest_runs(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM runs WHERE id IN (SELECT MAX(id) FROM runs GROUP BY source) "
            "ORDER BY source"
        ).fetchall()

    def close(self) -> None:
        self.db.close()
