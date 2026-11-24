"""FastAPI application exposing the ingestion and classification stages."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

from sigops.catalog import Catalog, SqliteCatalog
from sigops.classifier import (
    DEFAULT_TAGS_PATH,
    JevJudge,
    Judge,
    classify_unjudged,
    load_tags,
)
from sigops.config import load_feeds
from sigops.ingestor import Ingestor
from sigops.models import (
    ArticleFailure,
    Classification,
    ClassificationFailure,
    FeedFailure,
    FeedSource,
    IngestionResult,
)

DEFAULT_FEEDS_PATH = Path(__file__).resolve().parents[2] / "config" / "feeds.toml"


class ClassifyResult(BaseModel):
    """Outcome of one classification run."""

    judgments: list[Classification] = Field(default_factory=list)
    failures: list[ClassificationFailure] = Field(default_factory=list)


class PipelineResult(BaseModel):
    """Outcome of one end-to-end pipeline run (`POST /run`).

    The user-facing entry point. Aggregates every stage run so far;
    forthcoming stages append their own fields here.
    """

    articles: list = Field(default_factory=list)
    failures: list[FeedFailure] = Field(default_factory=list)
    article_failures: list[ArticleFailure] = Field(default_factory=list)
    judgments: list[Classification] = Field(default_factory=list)
    classification_failures: list[ClassificationFailure] = Field(
        default_factory=list
    )


def run_ingest(feeds_path: Path, catalog: Catalog) -> IngestionResult:
    """Fetch, parse, and catalog one pass. Pure stage logic, no HTTP."""
    return Ingestor(load_feeds(feeds_path), catalog=catalog).run()


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
) -> FastAPI:
    """Build the ASGI app.

    `feeds_path` defaults to config/feeds.toml relative to the repo root, so
    tests can point at a fixture file. The file is read per request, so a
    missing or malformed config surfaces as a 500 rather than an import error.
    `catalog` is passed to Ingestor per request; when None the Ingestor builds
    its default. Tests inject a MemoryCatalog so no request touches state/.
    `judge` defaults to JevJudge (live); tests inject a fake.
    """
    path = feeds_path or DEFAULT_FEEDS_PATH

    app = FastAPI()

    def _catalog() -> Catalog:
        return catalog or SqliteCatalog()

    @app.get("/feeds", response_model=list[FeedSource])
    def list_feeds() -> list[FeedSource]:
        """Return the configured feed sources."""
        return load_feeds(path)

    @app.post("/ingest", response_model=IngestionResult)
    def ingest() -> IngestionResult:
        """Run one ingestion pass. Standalone stage endpoint (testing)."""
        return run_ingest(path, _catalog())

    @app.post("/classify", response_model=ClassifyResult)
    def classify() -> ClassifyResult:
        """Judge every unjudged cataloged article. Standalone stage endpoint."""
        return run_classify(_catalog(), judge or JevJudge(), tags_path)

    @app.post("/run", response_model=PipelineResult)
    def run() -> PipelineResult:
        """Run the full pipeline end to end. The user-facing endpoint."""
        active_catalog = _catalog()
        ingestion = run_ingest(path, active_catalog)
        classification = run_classify(
            active_catalog, judge or JevJudge(), tags_path
        )
        return PipelineResult(
            articles=ingestion.articles,
            failures=ingestion.failures,
            article_failures=ingestion.article_failures,
            judgments=classification.judgments,
            classification_failures=classification.failures,
        )

    return app


app = create_app()
"""Module-level app for `uvicorn sigops.api:app`."""
