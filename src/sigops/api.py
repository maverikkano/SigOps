"""FastAPI application exposing the ingestion and classification stages."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

from sigops.catalog import Catalog, CatalogError, SqliteCatalog
from sigops.classifier import (
    DEFAULT_TAGS_PATH,
    JevJudge,
    Judge,
    classify_unjudged,
    load_tags,
)
from sigops.config import load_feeds
from sigops.extraction import ExtractionError, Extractor
from sigops.fetcher import FeedFetcher
from sigops.ingestor import Ingestor
from sigops.models import (
    ArticleFailure,
    Classification,
    ClassificationFailure,
    ExtractionFailure,
    FeedFailure,
    FeedSource,
    IngestionResult,
)

logger = logging.getLogger(__name__)

DEFAULT_FEEDS_PATH = Path(__file__).resolve().parents[2] / "config" / "feeds.toml"


class ClassifyResult(BaseModel):
    """Outcome of one classification run."""

    judgments: list[Classification] = Field(default_factory=list)
    failures: list[ClassificationFailure] = Field(default_factory=list)


class ExtractResult(BaseModel):
    """Outcome of one extraction run."""

    articles: list = Field(default_factory=list)
    failures: list[ExtractionFailure] = Field(default_factory=list)


class PipelineResult(BaseModel):
    """Outcome of one end-to-end pipeline run (`POST /run`).

    The user-facing entry point. Aggregates every stage run so far;
    forthcoming stages append their own fields here.
    """

    articles: list = Field(default_factory=list)
    failures: list[FeedFailure] = Field(default_factory=list)
    article_failures: list[ArticleFailure] = Field(default_factory=list)
    extractions: list = Field(default_factory=list)
    extraction_failures: list[ExtractionFailure] = Field(default_factory=list)
    judgments: list[Classification] = Field(default_factory=list)
    classification_failures: list[ClassificationFailure] = Field(
        default_factory=list
    )


def run_ingest(feeds_path: Path, catalog: Catalog) -> IngestionResult:
    """Fetch, parse, and catalog one pass. Pure stage logic, no HTTP."""
    return Ingestor(load_feeds(feeds_path), catalog=catalog).run()


def run_extract(
    catalog: Catalog, extractor: Extractor | None = None
) -> ExtractResult:
    """Extract text for every unextracted article. Pure stage logic, no HTTP."""
    active = extractor or Extractor()
    enriched: list = []
    failures: list[ExtractionFailure] = []
    for article in catalog.unextracted():
        try:
            done = active.extract(article)
        except ExtractionError as e:
            failures.append(
                ExtractionFailure(
                    feed_id=article.feed_id,
                    article_id=article.article_id,
                    reason=str(e),
                )
            )
            continue
        try:
            catalog.update_extraction(done)
        except CatalogError as e:
            failures.append(
                ExtractionFailure(
                    feed_id=article.feed_id,
                    article_id=article.article_id,
                    reason=str(e),
                )
            )
            continue
        enriched.append(done)
    return ExtractResult(articles=enriched, failures=failures)


def run_classify(
    catalog: Catalog, judge: Judge, tags_path: Path | None
) -> ClassifyResult:
    """Judge every unjudged article and write back. Pure stage logic, no HTTP."""
    vocabulary = load_tags(tags_path or DEFAULT_TAGS_PATH)
    judgments, failures = classify_unjudged(
        catalog, catalog.unjudged(), vocabulary, judge
    )
    return ClassifyResult(judgments=judgments, failures=failures)


def create_app(
    feeds_path: Path | None = None,
    catalog: Catalog | None = None,
    judge: Judge | None = None,
    tags_path: Path | None = None,
    extractor: Extractor | None = None,
    fetcher: FeedFetcher | None = None,
) -> FastAPI:
    """Build the ASGI app.

    `feeds_path` defaults to config/feeds.toml relative to the repo root, so
    tests can point at a fixture file. The file is read per request, so a
    missing or malformed config surfaces as a 500 rather than an import error.
    `catalog` is passed to Ingestor per request; when None the Ingestor builds
    its default. Tests inject a MemoryCatalog so no request touches state/.
    `judge` defaults to JevJudge (live); tests inject a fake.
    `extractor` defaults to a live Extractor; tests inject one backed by a
    MockTransport fetcher.
    """
    path = feeds_path or DEFAULT_FEEDS_PATH

    app = FastAPI()

    def _catalog() -> Catalog:
        return catalog or SqliteCatalog()

    def _extractor() -> Extractor:
        if extractor is not None:
            return extractor
        return Extractor(fetcher) if fetcher is not None else Extractor()

    @app.post("/run", response_model=PipelineResult)
    def run() -> PipelineResult:
        """Run the full pipeline end to end. The user-facing endpoint."""
        run_id = uuid.uuid4().hex[:8]
        log = logging.LoggerAdapter(logger, {"run_id": run_id})
        log.info("run started")
        active_catalog = _catalog()

        started = time.monotonic()
        ingestion = run_ingest(path, active_catalog)
        log.info(
            "ingest done in %.1fs: %d new, %d feed failures, %d article failures",
            time.monotonic() - started,
            len(ingestion.articles),
            len(ingestion.failures),
            len(ingestion.article_failures),
        )

        started = time.monotonic()
        extraction = run_extract(active_catalog, _extractor())
        log.info(
            "extract done in %.1fs: %d extracted, %d extraction failures",
            time.monotonic() - started,
            len(extraction.articles),
            len(extraction.failures),
        )

        started = time.monotonic()
        classification = run_classify(
            active_catalog, judge or JevJudge(), tags_path
        )
        log.info(
            "classify done in %.1fs: %d judged, %d classification failures",
            time.monotonic() - started,
            len(classification.judgments),
            len(classification.failures),
        )
        log.info("run finished")
        return PipelineResult(
            articles=ingestion.articles,
            failures=ingestion.failures,
            article_failures=ingestion.article_failures,
            extractions=extraction.articles,
            extraction_failures=extraction.failures,
            judgments=classification.judgments,
            classification_failures=classification.failures,
        )

    @app.get("/feeds", response_model=list[FeedSource])
    def list_feeds() -> list[FeedSource]:
        """Return the configured feed sources."""
        return load_feeds(path)

    @app.post("/ingest", response_model=IngestionResult)
    def ingest() -> IngestionResult:
        """Run one ingestion pass. Standalone stage endpoint (testing)."""
        return run_ingest(path, _catalog())

    @app.post("/extract", response_model=ExtractResult)
    def extract() -> ExtractResult:
        """Extract text for every unextracted article. Standalone endpoint."""
        return run_extract(_catalog(), _extractor())

    @app.post("/classify", response_model=ClassifyResult)
    def classify() -> ClassifyResult:
        """Judge every unjudged cataloged article. Standalone stage endpoint."""
        return run_classify(_catalog(), judge or JevJudge(), tags_path)

    return app


app = create_app()
"""Module-level app for `uvicorn sigops.api:app`."""
