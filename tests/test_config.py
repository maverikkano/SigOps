"""Tests for config loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from sigops.config import ConfigError, load_feeds
from sigops.models import FeedSource


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "feeds.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_loads_one_source_per_table(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[feeds.techcrunch]
url = "https://techcrunch.com/feed/"

[feeds.hn]
url = "https://hnrss.org/frontpage"
""",
    )
    sources = load_feeds(path)
    assert [s.feed_id for s in sources] == ["techcrunch", "hn"]
    assert sources[0].url == "https://techcrunch.com/feed/"
    assert all(isinstance(s, FeedSource) for s in sources)


def test_empty_feeds_table_yields_empty_list(tmp_path: Path) -> None:
    path = _write(tmp_path, "[feeds]\n")
    assert load_feeds(path) == []


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_feeds(tmp_path / "nope.toml")


def test_invalid_toml_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "[feeds.x\nurl = ")
    with pytest.raises(ConfigError):
        load_feeds(path)


def test_no_feeds_table_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "[other]\nurl = 'https://e.com'\n")
    with pytest.raises(ConfigError):
        load_feeds(path)


def test_table_missing_url_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "[feeds.x]\nname = 'x'\n")
    with pytest.raises(ConfigError):
        load_feeds(path)


def test_empty_url_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, '[feeds.x]\nurl = ""\n')
    with pytest.raises(ConfigError):
        load_feeds(path)
