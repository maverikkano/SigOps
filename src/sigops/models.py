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


class ArticleFailure(BaseModel):
    """An article that could not be recorded in the catalog.

    Failure isolation: one bad row never aborts the run. Mirrors FeedFailure
    but keyed per article rather than per feed.
    """

    feed_id: str
    article_id: str
    reason: str


class Classification(BaseModel):
    """Classification judgment for one cataloged article.

    Stored separately from the article row (classifications table) so history
    can be re-judged when the tag vocabulary changes. `Article` stays a
    normalized feed entry and gains no tag fields.
    """

    feed_id: str
    article_id: str
    tags: list[str] = Field(default_factory=list)
    probabilities: dict[str, float] = Field(default_factory=dict)
    eligible: bool = False
    judge_model: str = ""
    judged_at: datetime | None = None


class IngestionResult(BaseModel):
    """Outcome of one ingestion run.

    `articles` holds every entry from every successful feed.
    `failures` holds one entry per feed that errored.
    `article_failures` holds one entry per article whose catalog write failed.
    """

    articles: list[Article] = Field(default_factory=list)
    failures: list[FeedFailure] = Field(default_factory=list)
    article_failures: list[ArticleFailure] = Field(default_factory=list)
