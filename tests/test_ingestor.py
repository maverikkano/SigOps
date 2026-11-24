"""Tests for the ingestion orchestrator."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from sigops.fetcher import FeedFetcher
from sigops.ingestor import Ingestor
from sigops.models import FeedSource
from sigops.store import SeenStore
from tests import fixtures


def _fetcher(mapping: dict[str, httpx.Response]) -> FeedFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        return mapping[str(request.url)]

    return FeedFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def store(tmp_path: Path) -> SeenStore:
    """A seen-store confined to this test.

    `Ingestor` defaults to the on-disk store, so every test must inject one or
    it would record fixture ids in the repo and filter them out of later runs.
    """
    return SeenStore(tmp_path / "seen.json")


def test_collects_articles_from_all_feeds(store: SeenStore) -> None:
    feeds = [
        FeedSource(feed_id="a", url="https://e.com/a"),
        FeedSource(feed_id="b", url="https://e.com/b"),
    ]
    fetcher = _fetcher(
        {
            "https://e.com/a": httpx.Response(200, content=fixtures.RSS_TWO_ITEMS),
            "https://e.com/b": httpx.Response(200, content=fixtures.ATOM_FEED),
        }
    )
    result = Ingestor(feeds, fetcher, store).run()
    assert len(result.articles) == 3
    assert {a.feed_id for a in result.articles} == {"a", "b"}
    assert result.failures == []


def test_one_failing_feed_does_not_stop_others(store: SeenStore) -> None:
    feeds = [
        FeedSource(feed_id="bad", url="https://e.com/bad"),
        FeedSource(feed_id="good", url="https://e.com/good"),
    ]
    fetcher = _fetcher(
        {
            "https://e.com/bad": httpx.Response(500, text="err"),
            "https://e.com/good": httpx.Response(200, content=fixtures.RSS_TWO_ITEMS),
        }
    )
    result = Ingestor(feeds, fetcher, store).run()
    assert len(result.articles) == 2
    assert len(result.failures) == 1
    assert result.failures[0].feed_id == "bad"


def test_parse_failure_recorded_per_feed(store: SeenStore) -> None:
    feeds = [FeedSource(feed_id="junk", url="https://e.com/junk")]
    fetcher = _fetcher({"https://e.com/junk": httpx.Response(200, content=fixtures.NOT_A_FEED)})
    result = Ingestor(feeds, fetcher, store).run()
    assert result.articles == []
    assert len(result.failures) == 1
    assert result.failures[0].feed_id == "junk"


def test_empty_feed_list_yields_empty_result(store: SeenStore) -> None:
    result = Ingestor([], _fetcher({}), store).run()
    assert result.articles == []
    assert result.failures == []


def test_second_run_returns_only_new_articles(store: SeenStore) -> None:
    """A repeated run over unchanged feeds yields nothing; a new item still arrives."""
    feeds = [FeedSource(feed_id="a", url="https://e.com/a")]
    fetcher = _fetcher(
        {"https://e.com/a": httpx.Response(200, content=fixtures.RSS_TWO_ITEMS)}
    )

    first = Ingestor(feeds, fetcher, store).run()
    assert len(first.articles) == 2

    second = Ingestor(feeds, fetcher, store).run()
    assert second.articles == []
    assert second.failures == []


def test_new_item_after_first_run_is_returned(store: SeenStore) -> None:
    feeds = [FeedSource(feed_id="a", url="https://e.com/a")]

    Ingestor(
        feeds,
        _fetcher({"https://e.com/a": httpx.Response(200, content=fixtures.RSS_TWO_ITEMS)}),
        store,
    ).run()

    later = _fetcher(
        {"https://e.com/a": httpx.Response(200, content=fixtures.RSS_ONE_NEW_ITEM)}
    )
    result = Ingestor(feeds, later, store).run()
    assert [a.article_id for a in result.articles] == ["guid-3"]
