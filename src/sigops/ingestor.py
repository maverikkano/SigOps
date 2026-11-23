"""Orchestrate ingestion across all configured feeds."""

from __future__ import annotations

from sigops.fetcher import FeedFetcher, FetchError
from sigops.parser import ParseError, parse_articles
from sigops.models import FeedSource, IngestionResult, FeedFailure
from sigops.store import SeenStore

class Ingestor:
    """Runs ingestion over a fixed set of feeds."""

    def __init__(self, feeds: list[FeedSource], fetcher: FeedFetcher | None = None, seen_store: SeenStore | None = None) -> None:
        """Store feeds and fetcher. Build a default FeedFetcher when None."""
        self.feeds = feeds
        self.fetcher = fetcher or FeedFetcher()
        self._seen_store = seen_store or SeenStore()

    def run(self) -> IngestionResult:
        """Fetch and parse every feed.

        One feed's FetchError or ParseError is recorded in `failures` and does
        not stop the others. Returns articles from all successful feeds.
        """
        result = IngestionResult()
        for feed in self.feeds:
            try:
                raw = self.fetcher.fetch(feed.url)
                result.articles.extend(parse_articles(feed.feed_id, raw))

            except (FetchError, ParseError) as e:
                result.failures.append(
                    FeedFailure(feed_id=feed.feed_id, url=feed.url, reason=str(e))
                )
        result = self._seen_store.dedupe(result)
        return result