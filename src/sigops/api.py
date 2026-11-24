"""FastAPI application exposing the ingestion stage."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from sigops.catalog import Catalog
from sigops.config import load_feeds
from sigops.ingestor import Ingestor
from sigops.models import FeedSource, IngestionResult

DEFAULT_FEEDS_PATH = Path(__file__).resolve().parents[2] / "config" / "feeds.toml"


def create_app(feeds_path: Path | None = None, catalog: Catalog | None = None) -> FastAPI:
    """Build the ASGI app.

    `feeds_path` defaults to config/feeds.toml relative to the repo root, so
    tests can point at a fixture file. The file is read per request, so a
    missing or malformed config surfaces as a 500 rather than an import error.
    `catalog` is passed to Ingestor per request; when None the Ingestor builds
    its default. Tests inject a MemoryCatalog so no request touches state/.
    """
    path = feeds_path or DEFAULT_FEEDS_PATH

    app = FastAPI()

    @app.get("/feeds", response_model=list[FeedSource])
    def list_feeds() -> list[FeedSource]:
        """Return the configured feed sources."""
        return load_feeds(path)

    @app.post("/ingest", response_model=IngestionResult)
    def ingest() -> IngestionResult:
        """Run one ingestion pass over the configured feeds."""
        return Ingestor(load_feeds(path), catalog=catalog).run()

    return app


app = create_app()
"""Module-level app for `uvicorn sigops.api:app`."""
