"""Tests for the ingestion orchestrator."""

from __future__ import annotations

import httpx

from sigops.fetcher import FeedFetcher
from sigops.ingestor import Ingestor
from sigops.models import FeedSource
from tests import fixtures


def _fetcher(mapping: dict[str, httpx.Response]) -> FeedFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        return mapping[str(request.url)]

    return FeedFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_collects_articles_from_all_feeds() -> None:
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
    result = Ingestor(feeds, fetcher).run()
    assert len(result.articles) == 3
    assert {a.feed_id for a in result.articles} == {"a", "b"}
    assert result.failures == []


def test_one_failing_feed_does_not_stop_others() -> None:
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
    result = Ingestor(feeds, fetcher).run()
    assert len(result.articles) == 2
    assert len(result.failures) == 1
    assert result.failures[0].feed_id == "bad"


def test_parse_failure_recorded_per_feed() -> None:
    feeds = [FeedSource(feed_id="junk", url="https://e.com/junk")]
    fetcher = _fetcher({"https://e.com/junk": httpx.Response(200, content=fixtures.NOT_A_FEED)})
    result = Ingestor(feeds, fetcher).run()
    assert result.articles == []
    assert len(result.failures) == 1
    assert result.failures[0].feed_id == "junk"


def test_empty_feed_list_yields_empty_result() -> None:
    result = Ingestor([], _fetcher({})).run()
    assert result.articles == []
    assert result.failures == []
