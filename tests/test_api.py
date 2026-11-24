"""Tests for the FastAPI surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sigops import api
from sigops.models import Article, FeedSource, IngestionResult


@pytest.fixture
def feeds_file(tmp_path: Path) -> Path:
    p = tmp_path / "feeds.toml"
    p.write_text('[feeds.a]\nurl = "https://e.com/a"\n', encoding="utf-8")
    return p


def test_get_feeds_returns_configured_sources(feeds_file: Path) -> None:
    client = TestClient(api.create_app(feeds_file))
    resp = client.get("/feeds")
    assert resp.status_code == 200
    assert resp.json() == [{"feed_id": "a", "url": "https://e.com/a"}]


def test_ingest_endpoint_returns_result(feeds_file: Path, monkeypatch) -> None:
    class StubIngestor:
        def __init__(self, feeds: list[FeedSource], fetcher=None, catalog=None) -> None:
            self.feeds = feeds

        def run(self) -> IngestionResult:
            return IngestionResult(
                articles=[
                    Article(
                        feed_id="a",
                        article_id="1",
                        title="T",
                        link="https://e.com/1",
                    )
                ]
            )

    monkeypatch.setattr(api, "Ingestor", StubIngestor)
    client = TestClient(api.create_app(feeds_file))
    resp = client.post("/ingest")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["articles"]) == 1
    assert body["articles"][0]["article_id"] == "1"
    assert body["failures"] == []


def test_ingest_reports_failures(feeds_file: Path, monkeypatch) -> None:
    from sigops.models import FeedFailure

    class StubIngestor:
        def __init__(self, feeds: list[FeedSource], fetcher=None, catalog=None) -> None:
            pass

        def run(self) -> IngestionResult:
            return IngestionResult(
                failures=[FeedFailure(feed_id="a", url="https://e.com/a", reason="boom")]
            )

    monkeypatch.setattr(api, "Ingestor", StubIngestor)
    client = TestClient(api.create_app(feeds_file))
    resp = client.post("/ingest")
    assert resp.status_code == 200
    assert resp.json()["failures"][0]["reason"] == "boom"


def test_bad_config_file_returns_500(tmp_path: Path) -> None:
    bad = tmp_path / "feeds.toml"
    bad.write_text("not toml [", encoding="utf-8")
    client = TestClient(api.create_app(bad), raise_server_exceptions=False)
    resp = client.get("/feeds")
    assert resp.status_code == 500
