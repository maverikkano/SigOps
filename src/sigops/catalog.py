"""Durable article index (Catalog stage).

Sits between Ingestion and Classification. Records every ingested article
keyed by (feed_id, article_id), replacing the JSON seen-state store.
Deduplication is a consequence of the index: an article is already known
when its key exists.

Two artefacts, two jobs:
- Files are the product (Persistence owns them). Immutable once written.
- This database is the index. Disposable: delete state/sigops.db and it
  rebuilds from the feeds.

Decisions (from issue #15, confirmed by owner):
- Writes use INSERT OR IGNORE. A re-run is cheap and never rewrites history.
- fetched_at records the first sighting only, never updated on re-fetch.
- A failed catalog write is recorded as a per-article failure
  (ArticleFailure); it never aborts the run (failure isolation).
- The default path (state/sigops.db) is overridable by injecting a
  Catalog object; SqliteCatalog defaults to DEFAULT_DB_PATH.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from sigops.models import Article, Classification

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "state" / "sigops.db"
"""Default SQLite file. Outside the package tree; gitignored, disposable."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  feed_id      TEXT NOT NULL,
  article_id   TEXT NOT NULL,
  title        TEXT NOT NULL,
  link         TEXT NOT NULL,
  summary      TEXT NOT NULL DEFAULT '',
  published_at TEXT,
  fetched_at   TEXT NOT NULL,
  PRIMARY KEY (feed_id, article_id)
);

CREATE TABLE IF NOT EXISTS classifications (
  feed_id       TEXT NOT NULL,
  article_id    TEXT NOT NULL,
  tags          TEXT NOT NULL,
  probabilities TEXT NOT NULL,
  eligible      INTEGER NOT NULL,
  judge_model   TEXT NOT NULL,
  judged_at     TEXT NOT NULL,
  PRIMARY KEY (feed_id, article_id),
  FOREIGN KEY (feed_id, article_id) REFERENCES articles(feed_id, article_id)
);
"""
"""DDL for state/sigops.db. Composite PK mirrors per-feed id scoping."""


class CatalogError(RuntimeError):
    """Raised when the catalog cannot be read, written, or a stored row is malformed."""
    pass


@runtime_checkable
class Catalog(Protocol):
    """Article index. SQLite is the real store; the in-memory fake is for tests.

    Implementations must share these semantics:
    - Keys are (feed_id, article_id). Same id string in two feeds = two articles.
    - Article rows are write-once (INSERT OR IGNORE semantics).
    - Classification rows are upserted (re-judge overwrites).
    - Malformed stored rows raise CatalogError on read, never silent None.
    """

    def put(self, article: Article) -> bool:
        """Insert `article`, or ignore it when the key already exists.

        Sets fetched_at to now (UTC) on first insert only; never updates it.
        Returns True when the row was newly inserted, False when it existed.
        Raises CatalogError on a storage failure. Must not raise on duplicates.
        """

    def get(self, feed_id: str, article_id: str) -> Article | None:
        """Return the article for `(feed_id, article_id)`, or None on a miss.

        Raises CatalogError when the stored row cannot be decoded
        (bad datetime, wrong types, missing non-null column).
        """
        ...

    def put_classification(self, classification: Classification) -> None:
        """Record `classification`, overwriting any prior judgment for its key.

        Requires the article row to exist; raises CatalogError otherwise.
        Re-judge must overwrite (upsert), never duplicate.
        """
        ...

    def get_classification(
        self, feed_id: str, article_id: str
    ) -> Classification | None:
        """Return the judgment for `(feed_id, article_id)`, or None if unjudged.

        Raises CatalogError when the stored row has malformed JSON
        (tags/probabilities), a bad datetime, or wrong types.
        """
        ...

    def find_by_tag(self, tag: str) -> list[Article]:
        """Return judged articles whose tags contain exactly `tag`.

        Empty list when nothing matches. Requires a join over classifications;
        unjudged articles never match.
        """
        ...

    def find_by_eligibility(self, eligible: bool) -> list[Article]:
        """Return judged articles with eligibility == `eligible`.

        Empty list when nothing matches. Unjudged articles never match.
        """
        ...

    def unjudged(self) -> list[Article]:
        """Return articles with no classification judgment yet.

        Empty list when everything is judged. The classification stage
        consumes this; re-judge is explicit, never default.
        """
        ...


def _decode_article_row(row: tuple) -> Article:
    try:
        feed_id, article_id, title, link, summary, published_at, fetched_at = row
        if not all(
            isinstance(v, str)
            for v in (feed_id, article_id, title, link, summary)
        ):
            raise CatalogError(f"malformed article row: wrong types: {row!r}")
        published = (
            datetime.fromisoformat(published_at) if published_at is not None else None
        )
        if published_at is not None and not isinstance(published, datetime):
            raise CatalogError(f"malformed published_at: {published_at!r}")
        datetime.fromisoformat(fetched_at)
        return Article(
            feed_id=feed_id,
            article_id=article_id,
            title=title,
            link=link,
            summary=summary,
            published_at=published,
        )
    except CatalogError:
        raise
    except Exception as e:
        raise CatalogError(f"malformed article row: {row!r}: {e}") from e


def _decode_classification_row(row: tuple) -> Classification:
    try:
        feed_id, article_id, tags_json, prob_json, eligible, judge_model, judged_at = row
        tags = json.loads(tags_json)
        probabilities = json.loads(prob_json)
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise CatalogError(f"malformed tags: {tags_json!r}")
        if not isinstance(probabilities, dict):
            raise CatalogError(f"malformed probabilities: {prob_json!r}")
        judged = datetime.fromisoformat(judged_at)
        return Classification(
            feed_id=feed_id,
            article_id=article_id,
            tags=tags,
            probabilities=probabilities,
            eligible=bool(eligible),
            judge_model=judge_model,
            judged_at=judged,
        )
    except CatalogError:
        raise
    except Exception as e:
        raise CatalogError(f"malformed classification row: {row!r}: {e}") from e


class SqliteCatalog:
    """Catalog backed by SQLite via stdlib sqlite3. No new dependency.

    Each method opens a short-lived connection; no close() needed.
    Creates parent dirs and tables on first write. Instantiation alone
    must not create files or directories.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        """Point at `path`, defaulting to DEFAULT_DB_PATH (state/sigops.db).

        `path` may be ":memory:" or a tmp file in tests; never the repo's
        real state/ dir in tests. Must not create files until the first write.
        """
        if path is None:
            self._path: Path | str = DEFAULT_DB_PATH
        else:
            self._path = path
        self._mem_conn: sqlite3.Connection | None = None

    def _is_memory(self) -> bool:
        return self._path == ":memory:"

    def _open(self) -> sqlite3.Connection:
        if self._is_memory():
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:")
                self._mem_conn.executescript(SCHEMA)
                self._mem_conn.commit()
            return self._mem_conn
        return sqlite3.connect(self._path)

    def _close(self, conn: sqlite3.Connection) -> None:
        if not self._is_memory():
            conn.close()

    def _db_exists(self) -> bool:
        if self._is_memory():
            return self._mem_conn is not None
        return Path(self._path).exists()

    def put(self, article: Article) -> bool:
        """INSERT OR IGNORE one article row; True if new, False if existed."""
        try:
            if not self._is_memory():
                Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            conn = self._open()
            try:
                conn.executescript(SCHEMA)
                fetched_at = datetime.now(timezone.utc).isoformat()
                published = (
                    article.published_at.isoformat()
                    if article.published_at is not None
                    else None
                )
                cur = conn.execute(
                    "INSERT OR IGNORE INTO articles"
                    " (feed_id, article_id, title, link, summary,"
                    " published_at, fetched_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        article.feed_id,
                        article.article_id,
                        article.title,
                        article.link,
                        article.summary,
                        published,
                        fetched_at,
                    ),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                self._close(conn)
        except (sqlite3.Error, OSError) as e:
            raise CatalogError(f"catalog write failed: {e}") from e

    def get(self, feed_id: str, article_id: str) -> Article | None:
        """Read one article row; None on miss, CatalogError on malformed row."""
        try:
            if not self._db_exists():
                return None
            conn = self._open()
            try:
                try:
                    cur = conn.execute(
                        "SELECT feed_id, article_id, title, link, summary,"
                        " published_at, fetched_at FROM articles"
                        " WHERE feed_id = ? AND article_id = ?",
                        (feed_id, article_id),
                    )
                except sqlite3.OperationalError:
                    return None
                row = cur.fetchone()
                if row is None:
                    return None
                return _decode_article_row(row)
            finally:
                self._close(conn)
        except CatalogError:
            raise
        except (sqlite3.Error, OSError) as e:
            raise CatalogError(f"catalog read failed: {e}") from e

    def put_classification(self, classification: Classification) -> None:
        """Upsert one classification row; CatalogError when article is missing."""
        try:
            if not self._is_memory():
                Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            conn = self._open()
            try:
                conn.executescript(SCHEMA)
                exists = conn.execute(
                    "SELECT 1 FROM articles WHERE feed_id = ? AND article_id = ?",
                    (classification.feed_id, classification.article_id),
                ).fetchone()
                if exists is None:
                    raise CatalogError(
                        f"no article row for"
                        f" ({classification.feed_id!r},"
                        f" {classification.article_id!r})"
                    )
                judged_at = (
                    classification.judged_at.isoformat()
                    if classification.judged_at is not None
                    else datetime.now(timezone.utc).isoformat()
                )
                conn.execute(
                    "INSERT INTO classifications"
                    " (feed_id, article_id, tags, probabilities,"
                    " eligible, judge_model, judged_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(feed_id, article_id) DO UPDATE SET"
                    " tags = excluded.tags,"
                    " probabilities = excluded.probabilities,"
                    " eligible = excluded.eligible,"
                    " judge_model = excluded.judge_model,"
                    " judged_at = excluded.judged_at",
                    (
                        classification.feed_id,
                        classification.article_id,
                        json.dumps(classification.tags),
                        json.dumps(classification.probabilities),
                        1 if classification.eligible else 0,
                        classification.judge_model,
                        judged_at,
                    ),
                )
                conn.commit()
            finally:
                self._close(conn)
        except CatalogError:
            raise
        except (sqlite3.Error, OSError) as e:
            raise CatalogError(f"catalog write failed: {e}") from e

    def get_classification(
        self, feed_id: str, article_id: str
    ) -> Classification | None:
        """Read one classification row; None when unjudged."""
        try:
            if not self._db_exists():
                return None
            conn = self._open()
            try:
                try:
                    cur = conn.execute(
                        "SELECT feed_id, article_id, tags, probabilities,"
                        " eligible, judge_model, judged_at"
                        " FROM classifications"
                        " WHERE feed_id = ? AND article_id = ?",
                        (feed_id, article_id),
                    )
                except sqlite3.OperationalError:
                    return None
                row = cur.fetchone()
                if row is None:
                    return None
                return _decode_classification_row(row)
            finally:
                self._close(conn)
        except CatalogError:
            raise
        except (sqlite3.Error, OSError) as e:
            raise CatalogError(f"catalog read failed: {e}") from e

    def find_by_tag(self, tag: str) -> list[Article]:
        """Join articles to classifications where tags contain `tag`."""
        try:
            if not self._db_exists():
                return []
            conn = self._open()
            try:
                try:
                    rows = conn.execute(
                        "SELECT a.feed_id, a.article_id, a.title, a.link,"
                        " a.summary, a.published_at, a.fetched_at, c.tags"
                        " FROM articles a"
                        " JOIN classifications c"
                        " ON a.feed_id = c.feed_id"
                        " AND a.article_id = c.article_id"
                    ).fetchall()
                except sqlite3.OperationalError:
                    return []
                out: list[Article] = []
                for row in rows:
                    *article_part, tags_json = row
                    try:
                        tags = json.loads(tags_json)
                    except Exception as e:
                        raise CatalogError(
                            f"malformed tags: {tags_json!r}: {e}"
                        ) from e
                    if isinstance(tags, list) and tag in tags:
                        out.append(_decode_article_row(tuple(article_part)))
                return out
            finally:
                self._close(conn)
        except CatalogError:
            raise
        except (sqlite3.Error, OSError) as e:
            raise CatalogError(f"catalog read failed: {e}") from e

    def find_by_eligibility(self, eligible: bool) -> list[Article]:
        """Join articles to classifications where eligible matches."""
        try:
            if not self._db_exists():
                return []
            conn = self._open()
            try:
                try:
                    rows = conn.execute(
                        "SELECT a.feed_id, a.article_id, a.title, a.link,"
                        " a.summary, a.published_at, a.fetched_at"
                        " FROM articles a"
                        " JOIN classifications c"
                        " ON a.feed_id = c.feed_id"
                        " AND a.article_id = c.article_id"
                        " WHERE c.eligible = ?",
                        (1 if eligible else 0,),
                    ).fetchall()
                except sqlite3.OperationalError:
                    return []
                return [_decode_article_row(tuple(row)) for row in rows]
            finally:
                self._close(conn)
        except CatalogError:
            raise
        except (sqlite3.Error, OSError) as e:
            raise CatalogError(f"catalog read failed: {e}") from e

    def unjudged(self) -> list[Article]:
        """Return articles with no classification row yet."""
        try:
            if not self._db_exists():
                return []
            conn = self._open()
            try:
                try:
                    rows = conn.execute(
                        "SELECT a.feed_id, a.article_id, a.title, a.link,"
                        " a.summary, a.published_at, a.fetched_at"
                        " FROM articles a"
                        " LEFT JOIN classifications c"
                        " ON a.feed_id = c.feed_id"
                        " AND a.article_id = c.article_id"
                        " WHERE c.feed_id IS NULL"
                    ).fetchall()
                except sqlite3.OperationalError:
                    return []
                return [_decode_article_row(tuple(row)) for row in rows]
            finally:
                self._close(conn)
        except CatalogError:
            raise
        except (sqlite3.Error, OSError) as e:
            raise CatalogError(f"catalog read failed: {e}") from e


class MemoryCatalog:
    """In-memory Catalog fake for tests. Same Protocol semantics, no I/O."""

    def __init__(self) -> None:
        """Start empty. No path, no filesystem, no shared state."""
        self._articles: dict[tuple[str, str], Article] = {}
        self._judgments: dict[tuple[str, str], Classification] = {}

    def put(self, article: Article) -> bool:
        """Store a copy; True if new, False if key existed (keep the first)."""
        key = (article.feed_id, article.article_id)
        if key in self._articles:
            return False
        self._articles[key] = article.model_copy(deep=True)
        return True

    def get(self, feed_id: str, article_id: str) -> Article | None:
        """Return a copy of the stored article, or None on a miss."""
        found = self._articles.get((feed_id, article_id))
        return found.model_copy(deep=True) if found is not None else None

    def put_classification(self, classification: Classification) -> None:
        """Store a copy, overwriting; CatalogError when article is missing."""
        key = (classification.feed_id, classification.article_id)
        if key not in self._articles:
            raise CatalogError(f"no article row for {key!r}")
        self._judgments[key] = classification.model_copy(deep=True)

    def get_classification(
        self, feed_id: str, article_id: str
    ) -> Classification | None:
        """Return a copy of the judgment, or None when unjudged."""
        found = self._judgments.get((feed_id, article_id))
        return found.model_copy(deep=True) if found is not None else None

    def find_by_tag(self, tag: str) -> list[Article]:
        """Exact-match tag filter over stored judgments."""
        out: list[Article] = []
        for key, judgment in self._judgments.items():
            if tag in judgment.tags:
                article = self._articles.get(key)
                if article is not None:
                    out.append(article.model_copy(deep=True))
        return out

    def find_by_eligibility(self, eligible: bool) -> list[Article]:
        """Eligibility filter over stored judgments."""
        out: list[Article] = []
        for key, judgment in self._judgments.items():
            if judgment.eligible == eligible:
                article = self._articles.get(key)
                if article is not None:
                    out.append(article.model_copy(deep=True))
        return out

    def unjudged(self) -> list[Article]:
        """Stored articles with no judgment yet."""
        return [
            article.model_copy(deep=True)
            for key, article in self._articles.items()
            if key not in self._judgments
        ]
