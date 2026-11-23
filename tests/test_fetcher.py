"""Tests for HTTP feed fetching."""

from __future__ import annotations

import httpx
import pytest

from sigops.fetcher import FeedFetcher, FetchError
from tests import fixtures


def _fetcher(handler) -> FeedFetcher:
    return FeedFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_returns_raw_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=fixtures.RSS_TWO_ITEMS)

    assert _fetcher(handler).fetch("https://e.com/feed") == fixtures.RSS_TWO_ITEMS


def test_non_2xx_raises_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="gone")

    with pytest.raises(FetchError):
        _fetcher(handler).fetch("https://e.com/missing")


def test_network_error_raises_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(FetchError):
        _fetcher(handler).fetch("https://e.com/feed")


def test_default_client_is_created() -> None:
    fetcher = FeedFetcher()
    assert isinstance(fetcher, FeedFetcher)
