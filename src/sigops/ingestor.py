"""Orchestrate ingestion across all configured feeds."""

from __future__ import annotations

from sigops.catalog import Catalog, CatalogError, SqliteCatalog
from sigops.fetcher import FeedFetcher, FetchError
from sigops.models import ArticleFailure, FeedSource, IngestionResult, FeedFailure
from sigops.parser import ParseError, parse_articles

class Ingestor:
    """Runs ingestion over a fixed set of feeds."""

    def __init__(self, feeds: list[FeedSource], fetcher: FeedFetcher | None = None, catalog: Catalog | None = None) -> None:
        """Store feeds, fetcher, and catalog.

        Build a default FeedFetcher and a default SqliteCatalog (at
        state/sigops.db) when None. `catalog` is injectable so tests pass a
        MemoryCatalog or tmp-path SqliteCatalog and never touch real state/.
        """
        self.feeds = feeds
        self.fetcher = fetcher or FeedFetcher()
        self.catalog: Catalog = catalog or SqliteCatalog()

    def run(self) -> IngestionResult:
        """Fetch, parse, and catalog every feed.

        Per feed: FetchError/ParseError is recorded in `failures` and does
        not stop the others. Per article: catalog.put() returning False
        means already known, so the article is NOT in `articles`;
        CatalogError is recorded in `article_failures` and does not stop
        the run. Writes the article row before Classification ever runs, so
        rejected articles are still stored (rejection is recorded later,
        never a reason to omit the row).
        """
        result = IngestionResult()
        for feed in self.feeds:
            try:
                raw = self.fetcher.fetch(feed.url)
                articles = parse_articles(feed.feed_id, raw)
            except (FetchError, ParseError) as e:
                result.failures.append(
                    FeedFailure(feed_id=feed.feed_id, url=feed.url, reason=str(e))
                )
                continue
            for article in articles:
                try:
                    if self.catalog.put(article):
                        result.articles.append(article)
                except CatalogError as e:
                    result.article_failures.append(
                        ArticleFailure(
                            feed_id=article.feed_id,
                            article_id=article.article_id,
                            reason=str(e),
                        )
                    )
        return result
