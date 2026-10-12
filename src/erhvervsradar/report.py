"""Reports built from the database: a Markdown data-quality report and CSV export."""

from __future__ import annotations

import csv
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from .models import AVAILABLE
from .storage import Storage, now

CSV_COLUMNS = [
    "source",
    "source_id",
    "url",
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
    "first_seen",
    "last_seen",
    "last_changed",
]

QUALITY_FIELDS = ["address", "zip_code", "city", "size_m2_min", "yearly_rent_min"]


def possible_duplicates(rows: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    """Groups of active listings in the same building on *different* sites."""
    by_building: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if row["building_key"]:
            by_building[row["building_key"]].append(row)
    return [group for group in by_building.values() if len({r["source"] for r in group}) > 1]


def summarize(storage: Storage) -> dict:
    """Everything the Markdown report shows, as plain data."""
    rows = storage.all_listings()
    active = [r for r in rows if r["status"] == AVAILABLE]

    by_source: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in active:
        by_source[row["source"]].append(row)
    quality = {
        source: {
            "listings": len(items),
            "fields": {
                f: round(sum(1 for r in items if r[f] not in (None, "")) / len(items), 3)
                for f in QUALITY_FIELDS
            },
        }
        for source, items in sorted(by_source.items())
    }

    per_type: dict[str, list[float]] = defaultdict(list)
    for row in active:
        if row["rent_per_m2"]:
            per_type[row["property_type"]].append(row["rent_per_m2"])
    market = [
        {"property_type": kind, "listings": len(values), "median_rent_per_m2": median(values)}
        for kind, values in sorted(per_type.items(), key=lambda kv: -len(kv[1]))
    ]

    return {
        "generated_at": now(),
        "available": len(active),
        "tracked": len(rows),
        "runs": [dict(run) for run in storage.latest_runs()],
        "quality": quality,
        "possible_duplicates": len(possible_duplicates(active)),
        "market": market,
        "cities": Counter(r["city"] for r in active if r["city"]).most_common(10),
    }


def build_report(storage: Storage) -> str:
    s = summarize(storage)

    def pct(share: float) -> str:
        return f"{100 * share:.0f}%"

    out = [
        "# Erhvervsradar report",
        "",
        f"Generated {s['generated_at']}: {s['available']} available listings "
        f"({s['tracked']} tracked in total)",
        "",
        "## Latest run per source",
        "",
        "| Source | Found | New | Changed | Removed | Errors | Complete |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for run in s["runs"]:
        out.append(
            f"| {run['source']} | {run['found'] or 0} | {run['new'] or 0} | "
            f"{run['changed'] or 0} | {run['removed'] or 0} | {run['errors'] or 0} | "
            f"{'yes' if run['complete'] else 'no (limited)'} |"
        )

    out += [
        "",
        "## Data quality (available listings)",
        "",
        "Share of listings where each field could be extracted:",
        "",
        "| Source | Listings | " + " | ".join(QUALITY_FIELDS) + " |",
        "|---|---:|" + "---:|" * len(QUALITY_FIELDS),
    ]
    for source, q in s["quality"].items():
        cells = [pct(q["fields"][f]) for f in QUALITY_FIELDS]
        out.append(f"| {source} | {q['listings']} | " + " | ".join(cells) + " |")

    out += [
        "",
        f"**Possible cross-site duplicates:** {s['possible_duplicates']} buildings "
        "listed on more than one site.",
        "",
        "## Market snapshot",
        "",
        "Median yearly rent per m² (DKK), listings with a single size and price:",
        "",
        "| Property type | Listings | Median DKK/m²/year |",
        "|---|---:|---:|",
    ]
    for m in s["market"]:
        out.append(f"| {m['property_type']} | {m['listings']} | {m['median_rent_per_m2']:,.0f} |")

    out += ["", "Most listings by city:", ""]
    out += [f"- {city}: {count}" for city, count in s["cities"]]
    return "\n".join(out) + "\n"


def export_csv(storage: Storage, path: str | Path, include_inactive: bool = False) -> int:
    rows = storage.all_listings() if include_inactive else storage.active_listings()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow([row[c] for c in CSV_COLUMNS])
    return len(rows)
