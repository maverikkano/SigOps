"""FastAPI application exposing the ingestion and classification stages."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

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
from sigops.models import Classification, ClassificationFailure, FeedSource, IngestionResult

DEFAULT_FEEDS_PATH = Path(__file__).resolve().parents[2] / "config" / "feeds.toml"


from pydantic import BaseModel, Field


class ClassifyResult(BaseModel):
    """Outcome of one classification run."""

    judgments: list[Classification] = Field(default_factory=list)
    failures: list[ClassificationFailure] = Field(default_factory=list)


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
        """Run one ingestion pass over the configured feeds."""
        return Ingestor(load_feeds(path), catalog=catalog).run()

    @app.post("/classify", response_model=ClassifyResult)
    def classify() -> ClassifyResult:
        """Judge every unjudged cataloged article and write back."""
        active_catalog = _catalog()
        vocabulary = load_tags(tags_path or DEFAULT_TAGS_PATH)
        active_judge: Judge = judge or JevJudge()
        judgments, failures = classify_unjudged(
            active_catalog, active_catalog.unjudged(), vocabulary, active_judge
        )
        return ClassifyResult(judgments=judgments, failures=failures)

    return app


app = create_app()
"""Module-level app for `uvicorn sigops.api:app`."""
