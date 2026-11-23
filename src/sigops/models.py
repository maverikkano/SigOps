"""Domain models for the ingestion stage."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FeedSource(BaseModel):
    """A single subscribed feed as declared in config/feeds.toml."""

    feed_id: str = Field(min_length=1)
    url: str = Field(min_length=1)


class Article(BaseModel):
    """One normalized RSS entry.

    `article_id` is stable across fetches: prefer the feed's guid/id, else link.
    `published_at` is None when the feed omits a parsable date.
    """

    feed_id: str
    article_id: str
    title: str
    link: str
    summary: str = ""
    published_at: datetime | None = None


class FeedFailure(BaseModel):
    """A feed that could not be fetched or parsed. One failure must not abort the run."""

    feed_id: str
    url: str
    reason: str


class IngestionResult(BaseModel):
    """Outcome of one ingestion run.

    `articles` holds every entry from every successful feed.
    `failures` holds one entry per feed that errored.
    """

    articles: list[Article] = Field(default_factory=list)
    failures: list[FeedFailure] = Field(default_factory=list)
