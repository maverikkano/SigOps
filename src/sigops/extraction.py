"""Extraction stage.

Fetches each article's link, strips HTML down to readable Markdown, and
carries the result on the article model so later stages never touch HTTP
or markup.

Decisions:
- Markdown, not plain text. Headings, lists, and tables survive; the
  Summarization stage needs that structure.
- Extraction is `trafilatura.extract(html, output_format="markdown")`:
  readability-style boilerplate removal plus Markdown in one call, one
  dependency.
- `extracted_text` is intrinsic to the article (unlike classification,
  which is derived and re-judgeable), so it lives as columns on the
  `articles` table, not a new table. Empty text means not yet extracted.
- Failure isolation: paywalls, redirects, non-HTML content types, and
  near-empty pages become a recorded ExtractionFailure per article; the
  run continues.

Stage ordering: ingest -> extraction -> classify. Classification's input
signal upgrades from title+summary to title+summary+extracted_text.
"""

from __future__ import annotations

import httpx
import trafilatura

from sigops.fetcher import FeedFetcher
from sigops.models import Article, ExtractionFailure

MIN_TEXT_CHARS = 200
"""Minimum extracted characters to count as meaningful text.

Below this the page is treated as near-empty (paywall shell, JS app
root, stub) and recorded as an ExtractionFailure rather than stored.
"""


class ExtractionError(RuntimeError):
    """Raised when an article cannot be fetched or extracted.

    Covers non-HTML content types, network failures, and pages that
    yield almost no text. Callers record it as an ExtractionFailure; it
    must never abort a run.
    """


def extract_text(html: str | bytes) -> str:
    """Convert an HTML document to readable Markdown prose.

    Strips nav, footer, scripts, and styles; keeps semantic structure
    (headings, lists, tables, image links, hyperlinks) as Markdown.
    Image binaries are never fetched; `![alt](src)` links preserve the
    source URL for the later chunk/index stages. Input is the raw
    response body; encoding is detected from the markup.

    Returns the Markdown string. Raises ExtractionError when the document
    yields no meaningful text (below MIN_TEXT_CHARS after stripping).
    Pure function: no I/O, deterministic for a fixed input.
    """
    try:
        md = trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_images=True,
            include_links=True,
        )
    except Exception as e:
        raise ExtractionError(f"extraction failed: {e}") from e
    if not md or len(md.strip()) < MIN_TEXT_CHARS:
        raise ExtractionError(
            f"page yielded no meaningful text"
            f" ({len(md.strip()) if md else 0} chars)"
        )
    return md.strip()


class Extractor:
    """Fetches article links and attaches extracted text.

    Owns HTTP for this stage; FeedFetcher stays feed-shaped (raw bytes,
    no content-type gating). Inject an httpx-backed FeedFetcher in
    tests via MockTransport; no network is ever needed.
    """

    def __init__(
        self,
        fetcher: FeedFetcher | None = None,
        min_chars: int = MIN_TEXT_CHARS,
    ) -> None:
        """Store the fetcher and minimum-text gate.

        Builds a default FeedFetcher when None. `min_chars` overrides
        MIN_TEXT_CHARS per instance, mainly for tests.
        """
        self.fetcher = fetcher or FeedFetcher()
        self.min_chars = min_chars

    def extract(self, article: Article) -> Article:
        """Return a copy of `article` with `extracted_text` set.

        GETs `article.link`. Non-HTML content types, HTTP errors, and
        near-empty extractions raise ExtractionError. `content_type` is
        recorded on the returned copy. Never mutates the input.
        """
        try:
            response = self.fetcher.client.get(article.link)
            response.raise_for_status()
        except (httpx.HTTPError, httpx.RequestError) as e:
            raise ExtractionError(f"fetch failed for {article.link}: {e}") from e
        content_type = response.headers.get("content-type", "")
        if content_type and "html" not in content_type.lower():
            raise ExtractionError(f"non-HTML content type: {content_type}")
        try:
            md = trafilatura.extract(
                response.content,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                include_images=True,
                include_links=True,
            )
        except Exception as e:
            raise ExtractionError(f"extraction failed: {e}") from e
        if not md or len(md.strip()) < self.min_chars:
            raise ExtractionError(
                f"page yielded no meaningful text"
                f" ({len(md.strip()) if md else 0} chars)"
            )
        return article.model_copy(
            update={"extracted_text": md.strip(), "content_type": content_type}
        )

    def extract_many(
        self, articles: list[Article]
    ) -> tuple[list[Article], list[ExtractionFailure]]:
        """Enrich every article; record per-article failures, never abort.

        Returns (enriched, failures): one ExtractionFailure per article
        whose fetch or extraction failed. Succeeding articles are
        unaffected by their siblings' failures.
        """
        enriched: list[Article] = []
        failures: list[ExtractionFailure] = []
        for article in articles:
            try:
                enriched.append(self.extract(article))
            except ExtractionError as e:
                failures.append(
                    ExtractionFailure(
                        feed_id=article.feed_id,
                        article_id=article.article_id,
                        reason=str(e),
                    )
                )
        return enriched, failures
