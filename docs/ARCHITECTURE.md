# Architecture
```plain text
Ingestion -> Catalog ->  Extraction -> Classification (Jev)  -> Chunk & Index (both text & images) -> OCR (images) -> Summarization -> Templatization (optional) -> Symantic Indexing -> Notify
```

## Known blindspots

- **HTML → Markdown is lossy.** The Extraction stage converts high-resolution
  HTML to lower-resolution Markdown. Exact layout, styling, interactive
  elements, and image binaries are dropped; image/link URLs are kept, binaries
  are never fetched. Chunk & Index, Summarization, and everything downstream
  operate on the Markdown, never the original page. If a stage needs fidelity
  the Markdown cannot carry, it must re-fetch the article URL itself.


| Stage | Purpose |
|-------|---------|
| Ingestion | RSS feeds |
| Catalog | Record every ingested article in a durable index keyed by feed and article id |
| Classification | Classify if an incoming article is of interest based on the title & description into tags |
| Persistence | Save eligible articles to file system/NAS/Blob storage |
| Chunk & Index | Extract the article into chunks and index each chunk (text & images in separate chunks) |
| Extraction | Identify images- graphs, tables, and other entities in the article and|
| Summarization | Summarize the article into a chunk-wise summary |
| Templatization (optional) | Create a summary document from a templete |
| Symantic Indexing | Add key findings to a RAG based on the summary document |
| Notify | Notify the user about the summary document |

## Catalog

The Catalog is an index over articles, not a replacement for the files.
Two artefacts, two jobs:

- **Files are the product.** `articles/<feed-id>/<title>.txt` is what the reader
  opens. Once written, a file is immutable.
- **The database is the index.** `state/sigops.db` records the article metadata
  plus the classification judgment. It is disposable: delete it and it can be
  rebuilt from the feeds.

The reason the Catalog exists as its own stage is that **classification is
derived, not intrinsic.** The article is fixed, but the tag decision depends on
a vocabulary that will change. A file is write-once, so tags stored only in a
file can never be recomputed. Storing the article row first makes re-classifying
history possible: change the vocabulary, re-run the judge, compare the results.

The Catalog also owns deduplication. An article is already known when
`(feed_id, article_id)` exists, so the catalog replaces the earlier seen-state
JSON file rather than sitting beside it. Two sources of truth for "have I seen
this" is a bug waiting to happen.

### Ordering constraint

The Catalog writes an article row **before** Classification runs. If classification
were to persist the article, an article rejected as ineligible would not exist in
the index at all, and could never be reconsidered.

### Storage choices

- **SQLite** via the standard-library `sqlite3` module. No new dependency.
- The file lives at `state/sigops.db`, outside the package tree, so installing
  the package never writes into site-packages.
- Access goes through a `Catalog` protocol with two implementations: a real
  SQLite one, and an in-memory fake for tests. Semantic Indexing is expected to
  want a vector store; the protocol is what makes that swap a single file.

### Schema

```sql
CREATE TABLE articles (
  feed_id      TEXT NOT NULL,
  article_id   TEXT NOT NULL,
  title        TEXT NOT NULL,
  link         TEXT NOT NULL,
  summary      TEXT NOT NULL DEFAULT '',
  published_at TEXT,
  fetched_at   TEXT NOT NULL,
  PRIMARY KEY (feed_id, article_id)
);

CREATE TABLE classifications (
  feed_id       TEXT NOT NULL,
  article_id    TEXT NOT NULL,
  tags          TEXT NOT NULL,  -- JSON array
  probabilities TEXT NOT NULL,  -- JSON object
  eligible      INTEGER NOT NULL,
  judge_model   TEXT NOT NULL,
  judged_at     TEXT NOT NULL,
  PRIMARY KEY (feed_id, article_id),
  FOREIGN KEY (feed_id, article_id) REFERENCES articles(feed_id, article_id)
);
```

The composite primary key mirrors the per-feed id scoping used since Ingestion:
the same id string in two feeds names two different articles. Probabilities are
stored because Classification must be explainable after the fact.


