"""Tests for the Extraction stage (issue #2).

Contract: fetch each article's link, convert HTML to Markdown prose, and
carry the text on the article model so later stages never touch HTTP or
markup. Markdown (not plain text): headings, lists, and tables survive
for the Summarization stage.

Failure isolation mirrors the existing per-feed handling: paywalls,
redirects, non-HTML content types, and near-empty pages become a
recorded ExtractionFailure per article, never an exception that ends
the run.

Tests run fully offline via httpx.MockTransport.
"""

from __future__ import annotations

import httpx
import pytest

from sigops.extraction import ExtractionError, Extractor, extract_text
from sigops.fetcher import FeedFetcher
from sigops.models import Article, ExtractionFailure
from tests import fixtures


def _article(link: str = "https://example.com/first") -> Article:
    return Article(
        feed_id="a", article_id="1", title="T", link=link, summary="s"
    )


def _fetcher(mapping: dict[str, httpx.Response]) -> FeedFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        return mapping[str(request.url)]

    return FeedFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _html_response(body: bytes) -> httpx.Response:
    return httpx.Response(
        200, content=body, headers={"content-type": "text/html; charset=utf-8"}
    )


# --- extract_text: HTML -> Markdown -------------------------------------------


def test_extract_text_contains_prose_as_markdown() -> None:
    md = extract_text(fixtures.ARTICLE_HTML)
    assert "New LLM breakthrough" in md
    assert "code generation" in md
    assert "Faster inference" in md


def test_extract_text_keeps_headings() -> None:
    md = extract_text(fixtures.ARTICLE_HTML)
    assert "# " in md  # Markdown heading survives; plain text would lose it


def test_extract_text_strips_chrome() -> None:
    md = extract_text(fixtures.ARTICLE_HTML)
    assert "var x = 1" not in md  # script
    assert "color: red" not in md  # style
    assert "Buy now!" not in md  # ad div
    assert "All rights reserved" not in md  # footer


def test_extract_text_near_empty_raises() -> None:
    with pytest.raises(ExtractionError):
        extract_text(fixtures.NEAR_EMPTY_HTML)


def test_extract_text_empty_document_raises() -> None:
    with pytest.raises(ExtractionError):
        extract_text(b"<html><body></body></html>")


# --- Extractor.extract ----------------------------------------------------------


def test_extract_attaches_markdown_and_content_type() -> None:
    extractor = Extractor(_fetcher({"https://example.com/first": _html_response(fixtures.ARTICLE_HTML)}))
    out = extractor.extract(_article())
    assert "code generation" in out.extracted_text
    assert "# " in out.extracted_text
    assert out.content_type.startswith("text/html")


def test_extract_does_not_mutate_input() -> None:
    extractor = Extractor(_fetcher({"https://example.com/first": _html_response(fixtures.ARTICLE_HTML)}))
    original = _article()
    extractor.extract(original)
    assert original.extracted_text == ""


def test_extract_non_html_raises() -> None:
    extractor = Extractor(
        _fetcher(
            {
                "https://example.com/first": httpx.Response(
                    200,
                    content=fixtures.PDF_BYTES,
                    headers={"content-type": "application/pdf"},
                )
            }
        )
    )
    with pytest.raises(ExtractionError):
        extractor.extract(_article())


def test_extract_http_error_raises() -> None:
    extractor = Extractor(
        _fetcher({"https://example.com/first": httpx.Response(403, text="denied")})
    )
    with pytest.raises(ExtractionError):
        extractor.extract(_article())


def test_extract_paywall_shell_raises() -> None:
    extractor = Extractor(
        _fetcher({"https://example.com/first": _html_response(fixtures.NEAR_EMPTY_HTML)})
    )
    with pytest.raises(ExtractionError):
        extractor.extract(_article())


# --- Extractor.extract_many: failure isolation ----------------------------------


def test_extract_many_failure_isolated_per_article() -> None:
    extractor = Extractor(
        _fetcher(
            {
                "https://example.com/good": _html_response(fixtures.ARTICLE_HTML),
                "https://example.com/bad": httpx.Response(500, text="err"),
            }
        )
    )
    articles = [
        _article("https://example.com/good"),
        Article(feed_id="a", article_id="2", title="B", link="https://example.com/bad"),
    ]
    enriched, failures = extractor.extract_many(articles)
    assert len(enriched) == 1
    assert "code generation" in enriched[0].extracted_text
    assert len(failures) == 1
    assert isinstance(failures[0], ExtractionFailure)
    assert failures[0].article_id == "2"


def test_extract_many_all_fail_gives_empty_enriched() -> None:
    extractor = Extractor(
        _fetcher({"https://example.com/first": httpx.Response(404, text="no")})
    )
    enriched, failures = extractor.extract_many([_article()])
    assert enriched == []
    assert len(failures) == 1


def test_extract_many_empty_input() -> None:
    enriched, failures = Extractor(_fetcher({})).extract_many([])
    assert enriched == []
    assert failures == []


# --- model shape -----------------------------------------------------------------


def test_article_carries_extracted_text_and_content_type() -> None:
    a = Article(
        feed_id="a", article_id="1", title="T", link="https://e.com/1",
        extracted_text="# Hello", content_type="text/html",
    )
    assert a.extracted_text == "# Hello"
    assert a.content_type == "text/html"


def test_article_extracted_text_defaults_empty() -> None:
    assert _article().extracted_text == ""
    assert _article().content_type == ""
