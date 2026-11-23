"""Parse raw feed bytes into normalized Article models."""

from __future__ import annotations

from datetime import datetime, timezone

import feedparser

from sigops.models import Article


class ParseError(RuntimeError):
    """Raised when feed bytes are not a usable RSS/Atom document."""
    pass


def parse_articles(feed_id: str, raw: bytes) -> list[Article]:
    """Parse `raw` into Articles for `feed_id`.

    Prefer the entry's guid/id for `article_id`, else its link. Skip entries
    that have neither an id nor a link. Normalize dates to UTC datetime; leave
    `published_at` None when the date is absent or unparsable.

    Raises ParseError when the document yields no entries, which covers both
    malformed XML and well-formed documents that are not feeds.
    """
    feed = feedparser.parse(raw)
    if not feed.entries:
        raise ParseError("no entries in feed")

    articles = []
    for entry in feed.entries:
        article_id = entry.get("id") or entry.get("link")
        if not article_id:
            continue
        published_at = _to_datetime(entry.get("published_parsed"))
        if published_at is None:
            published_at = _to_datetime(entry.get("updated_parsed"))
        articles.append(
            Article(
                feed_id=feed_id,
                article_id=article_id,
                title=entry.get("title") or "",
                link=entry.get("link") or "",
                summary=entry.get("summary") or "",
                published_at=published_at,
            )
        )
    return articles


def _to_datetime(struct_time: object) -> datetime | None:
    """Convert a feedparser `*_parsed` struct_time to a UTC datetime, or None."""
    if struct_time is None:
        return None
    try:
        fields = tuple(struct_time)[:6]  # type: ignore[call-overload]
        return datetime(*fields, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
