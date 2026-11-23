"""Tests for feed parsing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sigops.parser import ParseError, parse_articles
from tests import fixtures


def test_parses_two_items() -> None:
    articles = parse_articles("feed-a", fixtures.RSS_TWO_ITEMS)
    assert len(articles) == 2
    first, second = articles
    assert first.feed_id == "feed-a"
    assert first.title == "First Post"
    assert first.link == "https://example.com/first"
    assert first.summary == "First summary"
    assert first.article_id == "guid-1"
    assert second.title == "Second Post"


def test_prefers_guid_over_link_for_article_id() -> None:
    articles = parse_articles("f", fixtures.RSS_TWO_ITEMS)
    assert articles[0].article_id == "guid-1"


def test_falls_back_to_link_when_no_guid() -> None:
    articles = parse_articles("f", fixtures.RSS_TWO_ITEMS)
    assert articles[1].article_id == "https://example.com/second"


def test_normalizes_date_to_utc() -> None:
    articles = parse_articles("f", fixtures.RSS_TWO_ITEMS)
    assert articles[0].published_at == datetime(2025, 11, 22, 10, 0, tzinfo=UTC)


def test_skips_entry_without_id_or_link() -> None:
    articles = parse_articles("f", fixtures.RSS_ENTRY_WITHOUT_ID_OR_LINK)
    assert articles == []


def test_missing_date_yields_none() -> None:
    articles = parse_articles("f", fixtures.RSS_NO_DATE)
    assert len(articles) == 1
    assert articles[0].published_at is None


def test_parses_atom_feed() -> None:
    articles = parse_articles("f", fixtures.ATOM_FEED)
    assert len(articles) == 1
    assert articles[0].title == "Atom Post"
    assert articles[0].article_id == "atom-1"
    assert articles[0].published_at == datetime(2025, 11, 22, 10, 0, tzinfo=UTC)


def test_malformed_document_raises() -> None:
    with pytest.raises(ParseError):
        parse_articles("f", fixtures.MALFORMED)


def test_non_feed_document_raises() -> None:
    with pytest.raises(ParseError):
        parse_articles("f", fixtures.NOT_A_FEED)
