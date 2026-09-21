from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
TEXT_ASSETS = (
    ROOT / "jev_ultrafast" / "snapshot.js",
    ROOT / "jev_ultrafast" / "static" / "app.js",
    ROOT / "jev_ultrafast" / "static" / "fixture.html",
    ROOT / "jev_ultrafast" / "static" / "index.html",
    ROOT / "jev_ultrafast" / "static" / "style.css",
)


@pytest.mark.parametrize("path", TEXT_ASSETS, ids=lambda path: path.name)
def test_bundled_text_assets_are_utf8(path):
    """Keep browser and inspector assets readable on every supported platform."""
    path.read_bytes().decode("utf-8")
