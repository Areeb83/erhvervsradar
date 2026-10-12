"""Erhvervsradar web app (Streamlit).

Upload an Excel sheet of links to Danish commercial lease listings, get the same
sheet back with address, size, rent and status filled in.

Run locally:  streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))  # works without installing the package

from erhvervsradar.check import (  # noqa: E402
    ERROR,
    HOST_DELAYS,
    CheckResult,
    check_url,
    normalize_url,
    quick_check,
)
from erhvervsradar.fetcher import Fetcher  # noqa: E402
from erhvervsradar.spreadsheet import (  # noqa: E402
    MAX_ROWS,
    SheetJob,
    SpreadsheetError,
    example_workbook,
    load_job,
    result_label,
    to_bytes,
    write_results,
)

st.set_page_config(page_title="Erhvervsradar: check property listings in Excel", layout="centered")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REPO = "https://github.com/Areeb83/erhvervsradar"

# -- look and feel: top bar, footer, and the styles in assets/app.css ---------------------

CSS = (Path(__file__).parent / "assets" / "app.css").read_text(encoding="utf-8")
st.html(
    f"""<style>{CSS}</style>
<div class="er-bar">
  <a class="er-logo" href="/" target="_self">Erhvervs<span>radar</span></a>
  <nav>
    <a href="#how-it-works">How it works</a>
    <a href="{REPO}" target="_blank">Source code</a>
  </nav>
</div>
<div class="er-foot">
  © 2026 Erhvervsradar · Built by Areeb Abbas ·
  Listing details belong to the sites they come from
</div>"""
)


def heading(title: str, subtitle: str = "") -> None:
    st.html(
        f'<h1 class="er-title">{title}</h1>'
        + (f'<p class="er-sub">{subtitle}</p>' if subtitle else "")
    )


def note(text: str) -> None:
    st.html(f'<p class="er-note">{text}</p>')


# -- checking links, shared by every visitor -----------------------------------------------


@st.cache_resource
def shared_fetcher() -> Fetcher:
    """One downloader for the whole app, so the pause between requests to a site
    holds across all visitors, not just within one person's session."""
    return Fetcher(host_delays=HOST_DELAYS, max_retries=2, timeout=15)


class _Retryable(Exception):
    """Raised for errors so st.cache_data doesn't remember them."""


@st.cache_data(ttl=3600, max_entries=5000, show_spinner=False)
def _cached_check(url: str) -> CheckResult:
    result = check_url(url, shared_fetcher())
    if result.status == ERROR:
        raise _Retryable(result)
    return result


def check(raw: str) -> CheckResult:
    """Each listing is fetched at most once an hour, however many people check it."""
    if early := quick_check(raw):
        return early
    try:
        return _cached_check(normalize_url(raw))
    except _Retryable as exc:
        return exc.args[0]


# -- results table -----------------------------------------------------------------------


def number(value: float | None) -> str:
    return "" if value is None else f"{value:,.0f}".replace(",", ".")


def span(low, high, unit: str) -> str:
    if low is None:
        return ""
    return f"{number(low)}{unit}" if high in (None, low) else f"{number(low)}–{number(high)}{unit}"


def short_link(url: str) -> str:
    """The part of a link that tells rows apart: the site and the end of the path."""
    bare = url.split("://", 1)[-1].removeprefix("www.").removeprefix("search.").rstrip("/")
    parts = bare.split("/")
    if len(parts) <= 3:
        return bare
    last = parts[-1]
    return (
        f"{parts[0]}/…/{last[:8]}…" if len(last) == 36 else f"{parts[0]}/…/{'/'.join(parts[-2:])}"
    )


def show_table(target, job: SheetJob) -> None:
    rows = []
    for link in job.rows:
        result = job.results.get(link.row)
        listing = result.listing if result else None
        rent = span(listing.yearly_rent_min, listing.yearly_rent_max, " kr.") if listing else ""
        rows.append(
            {
                "Row": link.row,
                "Link": short_link(link.url),
                "Result": result_label(result) if result else "Waiting",
                "Address": f"{listing.address}, {listing.zip_code} {listing.city}"
                if listing
                else "",
                "Size": span(listing.size_m2_min, listing.size_m2_max, " m²") if listing else "",
                "Rent per year": rent or ("On request" if listing else ""),
                "Note": (result.message or "") if result else "",
            }
        )
    target.dataframe(rows, hide_index=True, use_container_width=True)


def summary(job: SheetJob) -> str:
    counts: dict[str, int] = {}
    for result in job.results.values():
        label = result_label(result)
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{n} {label.lower()}" for label, n in counts.items())


def estimate(job: SheetJob) -> str:
    """Each site has its own pause, so the busiest site sets the pace."""
    per_site: dict[str, int] = {}
    for link in job.rows:
        if quick_check(link.url) is None:
            site = normalize_url(link.url).split("/")[2]
            per_site[site] = per_site.get(site, 0) + 1
    if not per_site:
        return ""
    paced = max((n - 1) * HOST_DELAYS[s] for s, n in per_site.items())
    seconds = int(max(paced, sum(per_site.values())) + 3)
    return f"about {seconds} seconds" if seconds < 90 else f"about {round(seconds / 60)} minutes"


# -- screens ---------------------------------------------------------------------------

state = st.session_state
state.setdefault("upload_key", 0)


def start_over() -> None:
    for key in ("job", "phase", "pending", "output", "file_name", "error"):
        state.pop(key, None)
    state.upload_key += 1  # a fresh, empty upload button


def screen_upload() -> None:
    heading(
        "Check property listings in Excel",
        "Upload an Excel sheet with links to EDC Erhverv or Savills listings. "
        "Get it back with address, size, rent and status filled in.",
    )
    uploaded = st.file_uploader(
        "Excel or CSV file with one column of links",
        type=["xlsx", "csv"],
        label_visibility="collapsed",
        key=f"upload_{state.upload_key}",
    )
    if state.get("error"):
        st.error(state.error)
    st.download_button(
        "Don't have a file? Download the sample Excel sheet",
        data=example_workbook(),
        file_name="erhvervsradar-sample.xlsx",
        mime=XLSX,
        key="sample",
    )
    if uploaded is not None:
        state.upload_key += 1
        try:
            state.job = load_job(uploaded.name, uploaded.getvalue())
            state.file_name = uploaded.name
            state.phase = "ready"
            state.pop("error", None)
        except SpreadsheetError as exc:
            state.error = str(exc)
        st.rerun()


def screen_ready(job: SheetJob) -> None:
    count = len(job.rows)
    supported = sum(1 for link in job.rows if quick_check(link.url) is None)
    others = count - supported
    plural = "s" if others != 1 else ""
    detail = f"Found in the “{job.url_header}” column of {state.file_name}. " + (
        f"{supported} is an EDC Erhverv or Savills listing"
        if supported == 1
        else f"{supported} are EDC Erhverv or Savills listings"
    )
    if others:
        detail += f"; {others} other link{plural} will get a note in the Result column"
    detail += "."
    heading(f"Ready to check {count} link{'s' if count != 1 else ''}", detail)
    if st.button("Check links", type="primary", key="start"):
        state.pending = [link.row for link in job.rows]
        state.phase = "running"
        st.rerun()
    if when := estimate(job):
        note(
            f"Takes {when}: the checker waits a few seconds between pages "
            "to be polite to each site."
        )
    if job.skipped:
        note(f"Only the first {MAX_ROWS} rows are checked; {job.skipped} were left out.")
    st.button("Choose another file", key="again", on_click=start_over)


def screen_running(job: SheetJob) -> None:
    pending = state.pop("pending", None)
    if pending is None:
        # The page reloaded during a run because "Stop" was clicked: keep what we have.
        finish(job, stopped=True)
        st.rerun()

    heading(
        "Checking links…", "Keep this page open. Your file will be ready to download in a moment."
    )
    progress = st.progress(0.0)
    st.button("Stop and keep the results so far", key="stop")
    table = st.empty()
    todo = [link for link in job.rows if link.row in set(pending)]
    for link in todo:
        job.results.pop(link.row, None)
    show_table(table, job)
    for link in todo:
        job.results[link.row] = check(link.url)
        done = len(job.results)
        progress.progress(done / len(job.rows), text=f"{done} of {len(job.rows)} checked")
        show_table(table, job)
    finish(job, stopped=False)
    st.rerun()


def finish(job: SheetJob, stopped: bool) -> None:
    write_results(job)
    state.output = to_bytes(job.workbook)
    state.phase = "stopped" if stopped else "done"


def screen_done(job: SheetJob) -> None:
    stopped = state.phase == "stopped"
    heading(
        "Stopped: your partial results are ready" if stopped else "Your Excel file is ready",
        f"{len(job.results)} of {len(job.rows)} links checked: {summary(job)}.",
    )
    st.download_button(
        "Download Excel file",
        data=state.output,
        file_name=f"{Path(state.file_name).stem.removesuffix(' (checked)')} (checked).xlsx",
        mime=XLSX,
        type="primary",
        key="download",
    )
    failed = [
        link.row for link in job.rows if (r := job.results.get(link.row)) and r.status == ERROR
    ]
    if failed and st.button(
        f"Try the {len(failed)} failed link{'s' if len(failed) != 1 else ''} again", key="retry"
    ):
        state.pending = failed
        state.phase = "running"
        st.rerun()
    st.button("Check another file", key="again", on_click=start_over)
    st.write("")
    show_table(st, job)


job: SheetJob | None = state.get("job")
phase = state.get("phase")
if job is None:
    screen_upload()
elif phase == "ready":
    screen_ready(job)
elif phase == "running":
    screen_running(job)
else:
    screen_done(job)

# -- how it works ------------------------------------------------------------------------

st.html('<div id="how-it-works" style="height: 4rem"></div>')
with st.expander("How it works, and which links are supported"):
    st.markdown(
        """
**Supported links**
- EDC Erhverv lease listings, like
  `https://www.edc.dk/erhverv/erhvervslejemaal/kontor/1552-koebenhavn-v/vester-voldgade-104/114058334/`
- Savills Denmark listings, like
  `https://search.savills.com/dk/da/ejendomsoplysninger/dff8a9c0-92ab-4c6e-afce-f1d7e327c393`

Other links stay in your file and are marked in the Result column.

**What you get back:** the same sheet, with your own columns untouched and these added:
Result, Address, Postcode, City, Property type, Size m², Rent per year, Rent per m²,
Source, Checked at, and a Note when something went wrong.

**Your file is not stored.** It is read, filled in and handed back during your visit only.

**Polite to the sites:** robots.txt is followed, there are 2 seconds between EDC pages and
5 seconds between Savills pages (shared across everyone using the app), and each answer is
remembered for an hour so the same listing isn't fetched again and again.
"""
    )
