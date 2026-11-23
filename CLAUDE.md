# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: SigOps (Signal Operations)

- RSS feed article ingestion → LLM summarization → local file output.
- Users subscribe to multiple RSS feeds; each article saved as `.txt`.
- Output layout (per README.md):
  - `/articles/<rss-feed-id>/<article_title>.txt`
  - `/articles/<rss-feed-id>/summary_<article_title>.txt` (LLM-generated, criteria-based)

## Architecture (per ARCHITECTURE.md)

Pipeline:

```plain
Ingestion -> Classification (Jev) -> Persistence -> Chunk & Index (text & images) -> Extraction -> Summarization -> Templatization (optional) -> Semantic Indexing -> Notify
```

- Ingestion: RSS feeds.
- Classification: Jev-based; tag articles by title + description; only eligible articles continue.
- Persistence: filesystem / NAS / blob storage.
- Chunk & Index: chunk article; index text and image chunks separately.
- Extraction: images, graphs, tables, entities.
- Summarization: chunk-wise summary.
- Templatization (optional): template-based summary doc.
- Semantic Indexing: key findings into RAG from summary doc.
- Notify: notify user of summary doc.

## Current State

- Docs only. No source code, no build/lint/test commands, no dependencies.
- Files: `README.md`, `ARCHITECTURE.md`, `INTENT.md`.
- No `.cursor/rules`, `.cursorrules`, `.github/copilot-instructions.md`, `.codex/`, `.gemini/`, `GEMINI.md` found.
- Do not invent build/test commands until source exists.

## Working Conventions (per INTENT.md)

- Avoid over-engineering. Keep implementations minimal.
- Explain design principles/patterns used; owner is closing a gap there, not in language constructs.
- Learning-context stack (not yet implemented): RSS, Kafka + RabbitMQ, JWT auth, certs/SSL, LangChain LLMOps, LLM-as-Judge evals, RAG, Langfuse observability.
