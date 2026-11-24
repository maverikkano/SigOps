# SigOps

*Signal Operations*

## Problem statement

- Users subscribe to multiple RSS Feeds
- Each RSS feed article will be saved to a local folder as .txt

```plain text
/
|--- /articles
    |--- /<rss-feed-id>
        !--- <article_title>.txt
        |--- summary_<article_title>.txt    # generated with LLM (based on a criteria)
```

## Status

Only **Ingestion** is implemented. It fetches the configured feeds and normalizes
each entry into an `Article`. Nothing downstream of it writes files yet, so the
`/articles` layout above does not exist on disk.

**Catalog** is the next stage to land. It records every ingested article in a
SQLite index so articles can be found again and so classification can be
recomputed when the tag vocabulary changes. It replaces the current seen-state
JSON file; it does not replace the article files.

## Requirements

- Python 3.12 or newer (3.14 is what the project is developed against)
- [uv](https://docs.astral.sh/uv/) for dependency management

## Install

```bash
uv sync
```

This creates `.venv/` and installs the project plus its dev dependencies.

## Run the tests

```bash
uv run pytest          # full suite
uv run pytest -q       # quieter output
```

Tests run fully offline. Feed responses come from fixtures in
`tests/fixtures.py` and HTTP is stubbed with `httpx.MockTransport`, so no network
access is required.

## Run the API

```bash
uv run uvicorn sigops.api:app --reload
```

Serves on `http://127.0.0.1:8000`. Interactive docs at `/docs`.

| Endpoint | Purpose |
| --- | --- |
| `GET /feeds` | List the feeds configured in `config/feeds.toml` |
| `POST /ingest` | Run one ingestion pass; returns the articles and any per-feed failures |

```bash
# List configured feeds
curl http://127.0.0.1:8000/feeds

# Trigger one ingestion pass
curl -X POST http://127.0.0.1:8000/ingest
```

Both endpoints make real network requests to the configured feeds, so they need
internet access. A feed that is unreachable or returns something that is not a
feed appears in `failures` and does not stop the run.

## Use as a library

```python
from sigops.ingestor import Ingestor
from sigops.config import load_feeds
from pathlib import Path

result = Ingestor(load_feeds(Path("config/feeds.toml"))).run()
for article in result.articles:
    print(article.title)
for failure in result.failures:
    print(f"{failure.feed_id}: {failure.reason}")
```

## Configuration

Feeds are declared in `config/feeds.toml`. The table key becomes the `feed_id`;
each table requires a non-empty `url`.

```toml
[feeds.techcrunch]
url = "https://techcrunch.com/feed/"

[feeds.hn]
url = "https://hnrss.org/frontpage"
```

## State

Ingestion records which articles it has already returned so a second run only
yields new ones. The store is a hand-inspectable JSON file at
`src/sigops/data/seen.json`.

- It is created on the first run that sees a new article.
- Deleting it reprocesses everything.
- It is currently inside the package tree and is not gitignored. Treat it as
  generated state and never commit it.

> The Catalog stage replaces this file with a SQLite index at `state/sigops.db`.
> Until that lands, the JSON store is the only record of what has been seen.

## Project layout

| Path | Contents |
| --- | --- |
| `src/sigops/` | Package source |
| `tests/` | Pytest suite and feed fixtures |
| `config/feeds.toml` | Feed subscriptions |
| `state/` | Generated state, gitignored. Will hold `sigops.db` once Catalog lands |
| `docs/` | Architecture and intent notes; `docs/architecture/` holds the LikeC4 model |

## Troubleshooting

- **`uv: command not found`** — install uv: `brew install uv`.
- **`POST /ingest` returns an empty `articles` list** — the seen-state store
  already holds those article ids. Delete `src/sigops/data/seen.json` to reprocess.
- **`POST /ingest` returns 500** — `config/feeds.toml` is missing or malformed.
  Check the shape against the configuration example above.
- **`state/sigops.db` error** — generated state is disposable. Delete the file and
  a later stage rebuilds it from the feeds.

