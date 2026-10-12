from erhvervsradar.models import REMOVED, Listing
from erhvervsradar.report import build_report, possible_duplicates
from erhvervsradar.storage import RunResult, Storage


def make(source_id="1", rent=100_000, source="edc", address="Vester Voldgade 104", zip_code="1552"):
    return Listing(
        source=source,
        source_id=source_id,
        url=f"https://example.dk/{source_id}",
        address=address,
        zip_code=zip_code,
        city="København V",
        property_type="office",
        size_m2_min=100,
        size_m2_max=100,
        yearly_rent_min=rent,
        yearly_rent_max=rent,
    )


def run(storage, source, listings, started):
    run_id, _ = storage.start_run(source)
    result = RunResult()
    outcomes = [storage.save(x, result, seen_at=started) for x in listings]
    storage.mark_removed(source, started, result)
    storage.finish_run(run_id, result, pages=len(listings), errors=0, complete=True)
    return result, outcomes


def test_tracks_new_changed_unchanged_and_removed(tmp_path):
    storage = Storage(tmp_path / "test.db")

    first, outcomes = run(storage, "edc", [make("1"), make("2")], "2026-10-01T05:00:00+00:00")
    assert outcomes == ["new", "new"]
    assert (first.new, first.changed, first.removed) == (2, 0, 0)

    # Day 2: listing 1 got cheaper, listing 2 disappeared, listing 3 is new.
    second, outcomes = run(
        storage, "edc", [make("1", rent=90_000), make("3")], "2026-10-02T05:00:00+00:00"
    )
    assert outcomes == ["changed", "new"]
    assert (second.new, second.changed, second.removed) == (1, 1, 1)

    rows = {r["source_id"]: r for r in storage.all_listings()}
    assert rows["1"]["yearly_rent_min"] == 90_000
    assert rows["1"]["first_seen"] == "2026-10-01T05:00:00+00:00"
    assert rows["2"]["status"] == REMOVED

    # Day 3: nothing changed.
    third, outcomes = run(
        storage, "edc", [make("1", rent=90_000), make("3")], "2026-10-03T05:00:00+00:00"
    )
    assert outcomes == ["unchanged", "unchanged"]
    assert (third.new, third.changed, third.removed) == (0, 0, 0)


def test_removing_only_affects_the_crawled_source(tmp_path):
    storage = Storage(tmp_path / "test.db")
    run(storage, "edc", [make("1")], "2026-10-01T05:00:00+00:00")
    run(storage, "savills", [make("a", source="savills")], "2026-10-02T05:00:00+00:00")
    statuses = {r["source"]: r["status"] for r in storage.all_listings()}
    assert statuses == {"edc": "available", "savills": "available"}


def test_cross_site_duplicates_and_report(tmp_path):
    storage = Storage(tmp_path / "test.db")
    day = "2026-10-01T05:00:00+00:00"
    edc = make("1", address="Vester Voldgade 104 2 TV")
    savills = make("a", source="savills", address="Vester Voldgade 104, 2. tv.")
    run(storage, "edc", [edc], day)
    run(storage, "savills", [savills], day)

    groups = possible_duplicates(storage.active_listings())
    assert len(groups) == 1 and {r["source"] for r in groups[0]} == {"edc", "savills"}

    report = build_report(storage)
    assert "Possible cross-site duplicates:** 1" in report
    assert "| office | 2 | 1,000 |" in report
