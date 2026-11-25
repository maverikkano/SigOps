"""Tests for the Chunk & Index stage (issue #8).

Contract: split an extracted article (Markdown) into ordered, addressable
chunks -- text chunks plus separate image chunks -- and persist the set
so stages can re-read without re-chunking.

RED STATE: every test fails with NotImplementedError until the owner
implements chunking.py / catalog.py / models.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sigops import api as api_module
from sigops import chunking
from sigops.catalog import (
    Catalog,
    CatalogError,
    MemoryCatalog,
    SqliteCatalog,
)
from sigops.chunking import chunk_article
from sigops.models import Article, Chunk, ChunkKind, Classification


MULTI_PARAGRAPH_MD = """# New LLM breakthrough

A new model for code generation was released today. It scores highly on
benchmarks for Python, TypeScript, and Rust.

## What changed

Faster inference. Longer context. Researchers say the gains come from
better data curation and a new training objective.
"""

WITH_IMAGES_MD = """# Launch photos

First paragraph of the story.

![Rocket on pad](https://e.com/img1.jpg)

Second paragraph after the photo.

![Crew portrait](https://e.com/img2.jpg)
"""

LONG_PARAGRAPH_MD = " ".join(["word"] * 1200)

EMPTY_MD = ""

SINGLE_SHORT_MD = "One short paragraph, nothing more."


def _article(extracted_text: str = MULTI_PARAGRAPH_MD, **kw) -> Article:
    article_id = kw.pop("article_id", "1")
    link = kw.pop("link", f"https://e.com/{article_id}")
    base = {
        "feed_id": "a",
        "article_id": article_id,
        "title": "T",
        "link": link,
        "summary": "s",
        "content_type": "text/html",
    }
    base.update(kw)
    return Article(extracted_text=extracted_text, **base)  # type: ignore[arg-type]


@pytest.fixture(params=["memory", "sqlite"])
def catalog(request, tmp_path: Path) -> Catalog:
    """One Catalog per backend so every contract test runs twice."""
    if request.param == "memory":
        return MemoryCatalog()
    return SqliteCatalog(tmp_path / "sigops.db")


# --- splitter: structure -------------------------------------------------------


def test_multi_paragraph_gives_ordered_text_chunks() -> None:
    chunks = chunk_article(_article(MULTI_PARAGRAPH_MD))
    assert len(chunks) >= 2
    assert all(c.kind == ChunkKind.TEXT for c in chunks)
    assert [c.pos for c in chunks] == list(range(len(chunks)))


def test_images_become_separate_chunks_not_text() -> None:
    chunks = chunk_article(_article(WITH_IMAGES_MD))
    images = [c for c in chunks if c.kind == ChunkKind.IMAGE]
    texts = [c for c in chunks if c.kind == ChunkKind.TEXT]
    assert len(images) == 2
    assert len(texts) >= 1
    assert "https://e.com/img1.jpg" in images[0].content
    assert "https://e.com/img2.jpg" in images[1].content
    assert all("https://e.com/img" not in t.content for t in texts)


def test_very_long_single_paragraph_splits_with_overlap() -> None:
    chunks = chunk_article(_article(LONG_PARAGRAPH_MD))
    assert len(chunks) >= 2
    assert all(c.kind == ChunkKind.TEXT for c in chunks)
    assert [c.pos for c in chunks] == list(range(len(chunks)))


def test_empty_body_gives_no_chunks() -> None:
    assert chunk_article(_article(EMPTY_MD)) == []


def test_single_short_paragraph_gives_one_chunk() -> None:
    chunks = chunk_article(_article(SINGLE_SHORT_MD))
    assert len(chunks) == 1
    assert chunks[0].kind == ChunkKind.TEXT
    assert chunks[0].pos == 0


def test_no_images_gives_zero_image_chunks() -> None:
    chunks = chunk_article(_article(MULTI_PARAGRAPH_MD))
    assert all(c.kind == ChunkKind.TEXT for c in chunks)


def test_chunking_is_deterministic() -> None:
    first = chunk_article(_article(WITH_IMAGES_MD))
    second = chunk_article(_article(WITH_IMAGES_MD))
    assert first == second


def test_each_chunk_records_article_position_and_source() -> None:
    chunks = chunk_article(_article(WITH_IMAGES_MD))
    for pos, chunk in enumerate(chunks):
        assert chunk.feed_id == "a"
        assert chunk.article_id == "1"
        assert chunk.pos == pos
        assert chunk.source_ref != ""
        assert chunk.content != ""


# --- persistence: write once, re-read ------------------------------------------


def test_chunks_roundtrip_without_rechunking(catalog: Catalog) -> None:
    catalog.put(_article(WITH_IMAGES_MD))
    chunks = chunk_article(_article(WITH_IMAGES_MD))
    catalog.put_chunks(chunks)
    reread = catalog.get_chunks("a", "1")
    assert reread == chunks
    assert [c.pos for c in reread] == sorted(c.pos for c in reread)


def test_get_chunks_empty_when_unchunked(catalog: Catalog) -> None:
    catalog.put(_article())
    assert catalog.get_chunks("a", "1") == []


def test_put_chunks_requires_article_row(catalog: Catalog) -> None:
    with pytest.raises(CatalogError):
        catalog.put_chunks(chunk_article(_article()))


def test_unchunked_returns_only_missing(catalog: Catalog) -> None:
    catalog.put(_article(article_id="1"))
    catalog.put(_article(article_id="2"))
    catalog.put_chunks(chunk_article(_article(article_id="1")))
    assert {a.article_id for a in catalog.unchunked()} == {"2"}


def test_unchunked_empty_when_all_chunked(catalog: Catalog) -> None:
    catalog.put(_article())
    catalog.put_chunks(chunk_article(_article()))
    assert catalog.unchunked() == []


def test_unchunked_ignores_unextracted(catalog: Catalog) -> None:
    catalog.put(_article(EMPTY_MD))
    assert catalog.unchunked() == []


# --- model shape -----------------------------------------------------------------


def test_article_has_no_chunk_fields() -> None:
    assert "chunks" not in Article.model_fields
    assert "pos" not in Article.model_fields


def test_chunk_kinds_are_text_and_image() -> None:
    assert ChunkKind.TEXT == "text"
    assert ChunkKind.IMAGE == "image"
    assert set(ChunkKind) == {ChunkKind.TEXT, ChunkKind.IMAGE}


def test_chunk_model_shape() -> None:
    chunk = Chunk(
        feed_id="a",
        article_id="1",
        pos=0,
        kind=ChunkKind.TEXT,
        content="hello",
        source_ref="# Title @ 0",
        content_hash="abc",
    )
    assert chunk.pos == 0
    assert chunk.kind == ChunkKind.TEXT


# --- stage runner: run_chunk_index ---------------------------------------------------


def test_run_chunk_index_chunks_unchunked(monkeypatch) -> None:
    catalog = MemoryCatalog()
    catalog.put(_article(WITH_IMAGES_MD))
    fake_chunks = [
        Chunk(
            feed_id="a", article_id="1", pos=0,
            kind=ChunkKind.TEXT, content="First",
            source_ref="# Launch @ 0", content_hash="h",
        ),
        Chunk(
            feed_id="a", article_id="1", pos=1,
            kind=ChunkKind.IMAGE, content="https://e.com/img1.jpg",
            source_ref="# Launch @ 100", content_hash="h",
        ),
    ]
    monkeypatch.setattr(chunking, "chunk_article", lambda a: fake_chunks)
    result = api_module.run_chunk_index(catalog)
    assert result.chunks == fake_chunks
    assert result.failures == []
    assert catalog.get_chunks("a", "1") == fake_chunks


def test_run_chunk_index_skips_chunked() -> None:
    catalog = MemoryCatalog()
    catalog.put(_article(WITH_IMAGES_MD))
    catalog.put_chunks(
        [
            Chunk(
                feed_id="a", article_id="1", pos=0,
                kind=ChunkKind.TEXT, content="done",
                source_ref="# T @ 0", content_hash="h",
            )
        ]
    )
    result = api_module.run_chunk_index(catalog)
    assert result.chunks == []
    assert result.failures == []


def test_run_chunk_index_failure_isolated_per_article(monkeypatch) -> None:
    catalog = MemoryCatalog()
    catalog.put(_article(MULTI_PARAGRAPH_MD, article_id="1"))
    catalog.put(_article(MULTI_PARAGRAPH_MD, article_id="2"))

    def boom(article: Article) -> list[Chunk]:
        if article.article_id == "1":
            raise CatalogError("disk full")
        return [
            Chunk(
                feed_id="a", article_id="2", pos=0,
                kind=ChunkKind.TEXT, content="ok",
                source_ref="# T @ 0", content_hash="h",
            )
        ]

    monkeypatch.setattr(chunking, "chunk_article", boom)
    result = api_module.run_chunk_index(catalog)
    assert len(result.chunks) == 1
    assert len(result.failures) == 1
    assert result.failures[0].article_id == "1"


def test_run_chunk_index_empty_body_records_no_failure() -> None:
    catalog = MemoryCatalog()
    catalog.put(_article(EMPTY_MD))
    result = api_module.run_chunk_index(catalog)
    assert result.chunks == []
    assert result.failures == []


# --- endpoint: POST /chunk_index + POST /run cascade -----------------------------------


def test_chunk_index_endpoint_indexes_unchunked(tmp_path: Path) -> None:
    """POST /chunk_index chunks extracted articles; stub the splitter."""
    catalog = MemoryCatalog()
    catalog.put(_article(WITH_IMAGES_MD))
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text('[feeds.a]\nurl = "https://e.com/a"\n', encoding="utf-8")
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps({"tags": ["AI-LLM"]}), encoding="utf-8")

    fake_chunks = [
        Chunk(
            feed_id="a", article_id="1", pos=0,
            kind=ChunkKind.TEXT, content="First",
            source_ref="# Launch @ 0", content_hash="h",
        ),
    ]

    client = TestClient(
        api_module.create_app(feeds_file, catalog=catalog, tags_path=tags_file)
    )
    orig = chunking.chunk_article
    chunking.chunk_article = lambda a: fake_chunks  # type: ignore[assignment]
    try:
        resp = client.post("/chunk_index")
    finally:
        chunking.chunk_article = orig
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["chunks"]) == 1
    assert body["chunks"][0]["kind"] == "text"
    assert body["failures"] == []
    assert catalog.get_chunks("a", "1") == fake_chunks


def test_run_endpoint_cascades_chunk_index(tmp_path: Path) -> None:
    """POST /run chunks after classify; body carries chunks + chunk_failures."""
    catalog = MemoryCatalog()
    catalog.put(_article(WITH_IMAGES_MD))
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text('[feeds.a]\nurl = "https://e.com/a"\n', encoding="utf-8")
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps({"tags": ["AI-LLM"]}), encoding="utf-8")

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

    fake_chunks = [
        Chunk(
            feed_id="a", article_id="1", pos=0,
            kind=ChunkKind.TEXT, content="First",
            source_ref="# Launch @ 0", content_hash="h",
        ),
    ]
    orig = chunking.chunk_article
    chunking.chunk_article = lambda a: fake_chunks  # type: ignore[assignment]
    try:
        client = TestClient(
            api_module.create_app(
                feeds_file, catalog=catalog, judge=FakeJudge(),
                tags_path=tags_file,
            )
        )
        resp = client.post("/run")
    finally:
        chunking.chunk_article = orig
    assert resp.status_code == 200
    body = resp.json()
    assert body["chunk_index"] == {"succeeded": 1, "failed": 0}
    assert catalog.get_chunks("a", "1") == fake_chunks
