"""Smoke test: the Streamlit page renders without errors."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).parent.parent / "app.py")


def test_start_screen_renders():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert len(at.get("file_uploader")) == 1
    assert len(at.get("download_button")) == 1  # the sample sheet
    assert any("How it works" in e.label for e in at.expander)
