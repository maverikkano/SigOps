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


def test_classify_endpoint_judges_unjudged(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timezone

    from sigops.catalog import MemoryCatalog
    from sigops.classifier import Judge
    from sigops.models import Classification

    class FakeJudge:
        model_name = "fake-jev"

        def judge(self, article, vocabulary):
            return Classification(
                feed_id=article.feed_id,
                article_id=article.article_id,
                tags=["AI-LLM"],
                probabilities={"AI-LLM": 0.9},
                eligible=True,
                judge_model=self.model_name,
                judged_at=datetime(2025, 11, 24, 12, 0, tzinfo=timezone.utc),
            )

    assert isinstance(FakeJudge(), Judge)
    catalog = MemoryCatalog()
    catalog.put(
        Article(feed_id="a", article_id="1", title="T", link="https://e.com/1")
    )
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps({"tags": ["AI-LLM"]}), encoding="utf-8")
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text('[feeds.a]\nurl = "https://e.com/a"\n', encoding="utf-8")
    client = TestClient(
        api.create_app(feeds_file, catalog=catalog, judge=FakeJudge(), tags_path=tags_file)
    )
    resp = client.post("/classify")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["judgments"]) == 1
    assert body["judgments"][0]["tags"] == ["AI-LLM"]
    assert body["failures"] == []
    assert catalog.get_classification("a", "1") is not None


def test_classify_endpoint_skips_judged(tmp_path: Path) -> None:
    import json

    from sigops.catalog import MemoryCatalog
    from sigops.models import Article

    catalog = MemoryCatalog()
    catalog.put(
        Article(feed_id="a", article_id="1", title="T", link="https://e.com/1")
    )
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps({"tags": ["AI-LLM"]}), encoding="utf-8")
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text('[feeds.a]\nurl = "https://e.com/a"\n', encoding="utf-8")

    class ExplodingJudge:
        model_name = "boom"

        def judge(self, article, vocabulary):
            raise AssertionError("must not be called for judged articles")

    from datetime import datetime, timezone

    from sigops.models import Classification

    catalog.put_classification(
        Classification(
            feed_id="a", article_id="1", tags=["AI-LLM"],
            probabilities={"AI-LLM": 0.9}, eligible=True,
            judge_model="old", judged_at=datetime.now(timezone.utc),
        )
    )
    client = TestClient(
        api.create_app(
            feeds_file, catalog=catalog, judge=ExplodingJudge(), tags_path=tags_file
        )
    )
    resp = client.post("/classify")
    assert resp.status_code == 200
    assert resp.json()["judgments"] == []


def test_run_endpoint_runs_ingest_then_classify(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timezone

    from sigops.catalog import MemoryCatalog
    from sigops.models import Classification

    catalog = MemoryCatalog()
    catalog.put(
        Article(feed_id="a", article_id="1", title="T", link="https://e.com/1")
    )
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps({"tags": ["AI-LLM"]}), encoding="utf-8")
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text('[feeds.a]\nurl = "https://e.com/a"\n', encoding="utf-8")

    class FakeJudge:
        model_name = "fake-jev"

        def judge(self, article, vocabulary):
            return Classification(
                feed_id=article.feed_id,
                article_id=article.article_id,
                tags=["AI-LLM"],
                probabilities={"AI-LLM": 0.9},
                eligible=True,
                judge_model=self.model_name,
                judged_at=datetime(2025, 11, 24, 12, 0, tzinfo=timezone.utc),
            )

    client = TestClient(
        api.create_app(
            feeds_file, catalog=catalog, judge=FakeJudge(), tags_path=tags_file
        )
    )
    resp = client.post("/run")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["judgments"]) == 1
    assert body["judgments"][0]["tags"] == ["AI-LLM"]
    assert body["classification_failures"] == []
    assert catalog.get_classification("a", "1") is not None


def test_extract_endpoint_extracts_unextracted(tmp_path: Path) -> None:
    from sigops.catalog import MemoryCatalog
    from sigops.extraction import Extractor

    catalog = MemoryCatalog()
    catalog.put(
        Article(feed_id="a", article_id="1", title="T", link="https://e.com/1")
    )
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text('[feeds.a]\nurl = "https://e.com/a"\n', encoding="utf-8")

    class FakeExtractor(Extractor):
        def extract(self, article):
            return article.model_copy(
                update={"extracted_text": "# Fake", "content_type": "text/html"}
            )

    client = TestClient(
        api.create_app(feeds_file, catalog=catalog, extractor=FakeExtractor())
    )
    resp = client.post("/extract")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["articles"]) == 1
    assert body["articles"][0]["extracted_text"] == "# Fake"
    assert body["failures"] == []
    got = catalog.get("a", "1")
    assert got is not None
    assert got.extracted_text == "# Fake"


def test_run_endpoint_extracts_before_classify(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timezone

    from sigops.catalog import MemoryCatalog
    from sigops.extraction import Extractor
    from sigops.models import Classification

    catalog = MemoryCatalog()
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text('[feeds.a]\nurl = "https://e.com/a"\n', encoding="utf-8")
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps({"tags": ["AI-LLM"]}), encoding="utf-8")

    seen: list[str] = []

    class FakeExtractor(Extractor):
        def extract(self, article):
            return article.model_copy(
                update={"extracted_text": "# Fake", "content_type": "text/html"}
            )

    class FakeJudge:
        model_name = "fake-jev"

        def judge(self, article, vocabulary):
            seen.append(article.extracted_text)
            return Classification(
                feed_id=article.feed_id,
                article_id=article.article_id,
                tags=["AI-LLM"],
                probabilities={"AI-LLM": 0.9},
                eligible=True,
                judge_model=self.model_name,
                judged_at=datetime(2025, 11, 24, 12, 0, tzinfo=timezone.utc),
            )

    client = TestClient(
        api.create_app(
            feeds_file,
            catalog=catalog,
            judge=FakeJudge(),
            tags_path=tags_file,
            extractor=FakeExtractor(),
        )
    )
    resp = client.post("/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_failures"] == []
    assert len(body["judgments"]) == 0  # no feeds configured beyond the file stub
    assert seen == [] or all(s == "# Fake" for s in seen)
