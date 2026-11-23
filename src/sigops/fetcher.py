"""Fetch raw feed bytes over HTTP."""

from __future__ import annotations

import httpx


class FetchError(RuntimeError):
    """Raised when a feed cannot be retrieved."""
    pass


class FeedFetcher:
    """Retrieves feed documents. Inject an httpx.Client for testing."""

    def __init__(self, client: httpx.Client | None = None, timeout: float = 10.0) -> None:
        """Create a fetcher.

        When `client` is None, build one with `timeout`. The caller owns the
        client lifecycle; the fetcher does not close it.
        """
        self.client = client or httpx.Client(timeout=timeout)

    def fetch(self, url: str) -> bytes:
        """Return the raw body for `url`.

        Raises FetchError on a network error or a non-2xx status. The response
        body is returned undecoded so the parser sees the feed's own encoding.
        """
        try:
            response = self.client.get(url)
            response.raise_for_status()
            if response.status_code != 200:
                raise FetchError(f"Feed {url} returned {response.status_code}")
        except (httpx.HTTPError, httpx.RequestError) as e:
            raise FetchError(f"Failed to fetch {url}: {e}") from e
        return response.content
