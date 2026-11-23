"""Durable record of which articles have already been ingested."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from sigops.models import Article, IngestionResult

PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_PATH = PROJECT_ROOT / ".sigops" / "seen.json"

class StoreError(RuntimeError):
    """Raised when the seen-state file exists but cannot be read."""

class SeenStore:
    """Article ids already ingested, scoped by feed and persisted as JSON.

    Storage: a JSON object mapping `feed_id` to a sorted list of
    `article_id`s at `state/seen.json` by default, so it survives process
    restarts and stays hand-inspectable. Ids are scoped per feed because
    the same id string in two feeds names two different articles.

    A missing file means nothing has been seen yet. A file that exists but
    is not a valid mapping raises StoreError rather than silently
    reprocessing.

    Expected wiring (left to the developer): `Ingestor` accepts an optional
    store, `run(force=False)` returns only `store.filter_new(articles)` then
    calls `store.mark_seen(returned)`; `force=True` skips filtering.
    """

    def __init__(self, path: Path = LOCAL_PATH) -> None:
        """Load the store at `path`, creating nothing until the first write."""
        self.path = path
        self._seen = self._load()

    def _load(self) -> dict[str, set[str]]:
        """Read the JSON file into `{feed_id: {article_id}}`; empty if missing."""
        if not self.path.exists():
            return {}
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StoreError(f"{self.path} cannot be read: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StoreError(f"{self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise StoreError(f"{self.path} must contain a JSON object")
        seen: dict[str, set[str]] = {}
        for feed_id, ids in data.items():
            if not isinstance(feed_id, str) or not isinstance(ids, list):
                raise StoreError(f"{self.path} has a malformed entry for {feed_id!r}")
            id_set: set[str] = set()
            for item in ids:
                if not isinstance(item, str):
                    raise StoreError(
                        f"{self.path} has a non-string article id for {feed_id!r}"
                    )
                id_set.add(item)
            seen[feed_id] = id_set
        return seen

    def _save(self) -> None:
        """Write the whole store atomically so a crash leaves no partial file."""
        payload = {feed_id: sorted(ids) for feed_id, ids in self._seen.items()}
        parent = self.path.parent
        if str(parent) and parent != Path("."):
            parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def filter_new(self, articles: list[Article]) -> list[Article]:
        """Return the articles whose id is not already recorded for their feed."""
        return [
            article
            for article in articles
            if article.article_id not in self._seen.get(article.feed_id, set())
        ]

    def mark_seen(self, articles: list[Article]) -> None:
        """Record `articles` as processed and persist, if anything is new."""
        changed = False
        for article in articles:
            ids = self._seen.setdefault(article.feed_id, set())
            if article.article_id not in ids:
                ids.add(article.article_id)
                changed = True
        if changed:
            self._save()

    def dedupe(self, result: IngestionResult, *, force: bool = False) -> IngestionResult:
        """Return `result` with only unseen articles, recording them as seen.

        `failures` pass through untouched. `force=True` skips filtering but
        still records the articles as seen.
        """
        if force:
            self.mark_seen(result.articles)
            return result
        fresh = self.filter_new(result.articles)
        self.mark_seen(fresh)
        return IngestionResult(articles=fresh, failures=list(result.failures))
