"""Tests for the Catalog stage (issue #15).

Contract: Catalog protocol with SqliteCatalog (real) and MemoryCatalog
(fake). All tests run against both unless marked sqlite-only. Tests never
touch the repo's real state/ directory: sqlite uses tmp_path, api/ingestor
tests inject MemoryCatalog.

RED STATE: every test fails with NotImplementedError until the owner
implements catalog.py / ingestor.py / api.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from sigops import api
from sigops.catalog import (
    DEFAULT_DB_PATH,
    Catalog,
    CatalogError,
    MemoryCatalog,
    SqliteCatalog,
)
from sigops.fetcher import FeedFetcher
from sigops.ingestor import Ingestor
from sigops.models import Article, ArticleFailure, Classification, FeedSource
from tests import fixtures


def _article(
    feed_id: str = "a", article_id: str = "1", **kw
) -> Article:
    base = {
        "feed_id": feed_id,
        "article_id": article_id,
        "title": "T",
        "link": f"https://e.com/{article_id}",
        "summary": "s",
        "published_at": datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return Article(**base)


def _classification(feed_id: str = "a", article_id: str = "1", **kw) -> Classification:
    base = {
        "feed_id": feed_id,
        "article_id": article_id,
        "tags": ["tech"],
        "probabilities": {"tech": 0.9},
        "eligible": True,
        "judge_model": "jev-0",
        "judged_at": datetime(2025, 11, 24, 12, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return Classification(**base)


@pytest.fixture(params=["memory", "sqlite"])
def catalog(request, tmp_path: Path) -> Catalog:
    """One Catalog per backend so every contract test runs twice."""
    if request.param == "memory":
        return MemoryCatalog()
    return SqliteCatalog(tmp_path / "sigops.db")


@pytest.fixture
def memory_catalog() -> MemoryCatalog:
    return MemoryCatalog()


# --- first write / idempotent re-write ------------------------------------


def test_first_write_returns_true_and_is_readable(catalog: Catalog) -> None:
    assert catalog.put(_article()) is True
    got = catalog.get("a", "1")
    assert got is not None
    assert got.feed_id == "a"
    assert got.article_id == "1"
    assert got.title == "T"
    assert got.link == "https://e.com/1"
    assert got.summary == "s"
    assert got.published_at == datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc)


def test_rewrite_returns_false_and_keeps_first_row(catalog: Catalog) -> None:
    assert catalog.put(_article(title="Original")) is True
    assert catalog.put(_article(title="Changed")) is False
    got = catalog.get("a", "1")
    assert got is not None
    assert got.title == "Original"


def test_ids_are_scoped_per_feed(catalog: Catalog) -> None:
    assert catalog.put(_article("a", "1")) is True
    assert catalog.put(_article("b", "1")) is True
    assert catalog.get("a", "1") is not None
    assert catalog.get("b", "1") is not None


def test_article_with_no_publication_date_roundtrips(catalog: Catalog) -> None:
    assert catalog.put(_article(published_at=None)) is True
    got = catalog.get("a", "1")
    assert got is not None
    assert got.published_at is None


# --- lookup hit and miss ---------------------------------------------------


def test_lookup_miss_returns_none(catalog: Catalog) -> None:
    assert catalog.get("nope", "missing") is None


def test_lookup_hit_after_write(catalog: Catalog) -> None:
    catalog.put(_article("a", "1"))
    catalog.put(_article("a", "2"))
    assert catalog.get("a", "2") is not None
    assert catalog.get("a", "2").article_id == "2"


# --- classifications --------------------------------------------------------


def test_put_classification_requires_article_row(catalog: Catalog) -> None:
    with pytest.raises(CatalogError):
        catalog.put_classification(_classification())


def test_classification_roundtrip(catalog: Catalog) -> None:
    catalog.put(_article())
    catalog.put_classification(_classification())
    got = catalog.get_classification("a", "1")
    assert got is not None
    assert got.tags == ["tech"]
    assert got.probabilities == {"tech": 0.9}
    assert got.eligible is True
    assert got.judge_model == "jev-0"


def test_unjudged_article_returns_none_classification(catalog: Catalog) -> None:
    catalog.put(_article())
    assert catalog.get_classification("a", "1") is None


def test_rejudge_overwrites(catalog: Catalog) -> None:
    catalog.put(_article())
    catalog.put_classification(_classification(tags=["tech"], eligible=True))
    catalog.put_classification(
        _classification(tags=["other"], probabilities={"other": 0.4}, eligible=False)
    )
    got = catalog.get_classification("a", "1")
    assert got is not None
    assert got.tags == ["other"]
    assert got.eligible is False


def test_find_by_tag(catalog: Catalog) -> None:
    catalog.put(_article("a", "1"))
    catalog.put(_article("a", "2"))
    catalog.put(_article("a", "3"))
    catalog.put_classification(_classification("a", "1", tags=["tech", "ai"]))
    catalog.put_classification(_classification("a", "2", tags=["sports"]))
    found = catalog.find_by_tag("tech")
    assert {a.article_id for a in found} == {"1"}
    assert catalog.find_by_tag("nope") == []


def test_find_by_tag_exact_match_only(catalog: Catalog) -> None:
    catalog.put(_article("a", "1"))
    catalog.put_classification(_classification("a", "1", tags=["technology"]))
    assert catalog.find_by_tag("tech") == []


def test_find_by_eligibility(catalog: Catalog) -> None:
    catalog.put(_article("a", "1"))
    catalog.put(_article("a", "2"))
    catalog.put(_article("a", "3"))  # unjudged: must never match
    catalog.put_classification(_classification("a", "1", eligible=True))
    catalog.put_classification(_classification("a", "2", eligible=False))
    assert {a.article_id for a in catalog.find_by_eligibility(True)} == {"1"}
    assert {a.article_id for a in catalog.find_by_eligibility(False)} == {"2"}


def test_unjudged_articles_never_match_queries(catalog: Catalog) -> None:
    catalog.put(_article("a", "1"))
    assert catalog.find_by_tag("tech") == []
    assert catalog.find_by_eligibility(True) == []
    assert catalog.find_by_eligibility(False) == []


# --- Article stays tag-free -------------------------------------------------


def test_article_has_no_tag_fields() -> None:
    assert "tags" not in Article.model_fields
    assert "eligible" not in Article.model_fields
    assert "probabilities" not in Article.model_fields


# --- sqlite-only: malformed rows, paths -------------------------------------


def test_sqlite_malformed_article_row_raises(tmp_path: Path) -> None:
    import sqlite3

    from sigops.catalog import SCHEMA

    db = tmp_path / "bad.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("a", "1", "T", "https://e.com/1", "s", "not-a-datetime", "also-bad"),
    )
    conn.commit()
    conn.close()
    with pytest.raises(CatalogError):
        SqliteCatalog(db).get("a", "1")


def test_sqlite_malformed_classification_row_raises(tmp_path: Path) -> None:
    import sqlite3

    from sigops.catalog import SCHEMA

    db = tmp_path / "bad.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("a", "1", "T", "https://e.com/1", "s", None, "2025-11-24T12:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO classifications VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("a", "1", "not-json{{{", "also-not-json", 1, "jev-0", "2025-11-24T12:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    with pytest.raises(CatalogError):
        SqliteCatalog(db).get_classification("a", "1")


def test_sqlite_instantiation_creates_nothing(tmp_path: Path) -> None:
    db = tmp_path / "sub" / "sigops.db"
    SqliteCatalog(db)
    assert not db.exists()
    assert not (tmp_path / "sub").exists()


def test_sqlite_first_write_creates_parent_dirs(tmp_path: Path) -> None:
    db = tmp_path / "sub" / "sigops.db"
    SqliteCatalog(db).put(_article())
    assert db.exists()


def test_default_path_is_gitignored_state_db() -> None:
    assert DEFAULT_DB_PATH.name == "sigops.db"
    assert DEFAULT_DB_PATH.parent.name == "state"
    assert "src/sigops" not in str(DEFAULT_DB_PATH)
    assert "site-packages" not in str(DEFAULT_DB_PATH)


# --- ingestor + catalog integration ------------------------------------------


def _fetcher(mapping: dict[str, httpx.Response]) -> FeedFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        return mapping[str(request.url)]

    return FeedFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ingestor_writes_rows_before_classification(memory_catalog: Catalog) -> None:
    """Every ingested article is stored, even ones a later stage may reject."""
    feeds = [FeedSource(feed_id="a", url="https://e.com/a")]
    fetcher = _fetcher({"https://e.com/a": httpx.Response(200, content=fixtures.RSS_TWO_ITEMS)})
    result = Ingestor(feeds, fetcher, memory_catalog).run()
    assert len(result.articles) == 2
    assert result.failures == []
    assert result.article_failures == []
    assert memory_catalog.get("a", "guid-1") is not None


def test_ingestor_second_run_returns_nothing_new(memory_catalog: Catalog) -> None:
    feeds = [FeedSource(feed_id="a", url="https://e.com/a")]
    fetcher = _fetcher({"https://e.com/a": httpx.Response(200, content=fixtures.RSS_TWO_ITEMS)})
    first = Ingestor(feeds, fetcher, memory_catalog).run()
    assert len(first.articles) == 2
    second = Ingestor(feeds, fetcher, memory_catalog).run()
    assert second.articles == []
    assert second.failures == []
    assert second.article_failures == []


def test_ingestor_new_item_after_first_run(memory_catalog: Catalog) -> None:
    feeds = [FeedSource(feed_id="a", url="https://e.com/a")]
    Ingestor(
        feeds,
        _fetcher({"https://e.com/a": httpx.Response(200, content=fixtures.RSS_TWO_ITEMS)}),
        memory_catalog,
    ).run()
    later = _fetcher({"https://e.com/a": httpx.Response(200, content=fixtures.RSS_ONE_NEW_ITEM)})
    result = Ingestor(feeds, later, memory_catalog).run()
    assert [a.article_id for a in result.articles] == ["guid-3"]


def test_ingestor_catalog_error_becomes_article_failure() -> None:
    """A failed catalog write never aborts the run (failure isolation)."""

    class ExplodingCatalog(MemoryCatalog):
        def put(self, article: Article) -> bool:
            raise CatalogError("disk full")

    feeds = [FeedSource(feed_id="a", url="https://e.com/a")]
    fetcher = _fetcher({"https://e.com/a": httpx.Response(200, content=fixtures.RSS_TWO_ITEMS)})
    result = Ingestor(feeds, fetcher, ExplodingCatalog()).run()
    assert result.articles == []
    assert result.failures == []
    assert len(result.article_failures) == 2
    assert all(isinstance(f, ArticleFailure) for f in result.article_failures)


def test_ingest_endpoint_injects_catalog(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text('[feeds.a]\nurl = "https://e.com/a"\n', encoding="utf-8")
    injected = MemoryCatalog()
    client = TestClient(api.create_app(feeds_file, catalog=injected))
    resp = client.get("/feeds")
    assert resp.status_code == 200
