# Architecture
```plain text
Ingestion -> Classification (Jev) -> Persistence -> Chunk & Index (both text & images) -> Extraction -> Summarization -> Templatization (optional) -> Symantic Indexing -> Notify
```


| Stage | Purpose |
|-------|---------|
| Ingestion | RSS feeds |
| Classification | Classify if an incoming article is of interest based on the title & description into tags |
| Persistence | Save eligible articles to file system/NAS/Blob storage |
| Chunk & Index | Extract the article into chunks and index each chunk (text & images in separate chunks) |
| Extraction | Identify images- graphs, tables, and other entities in the article and|
| Summarization | Summarize the article into a chunk-wise summary |
| Templatization (optional) | Create a summary document from a templete |
| Symantic Indexing | Add key findings to a RAG based on the summary document |
| Notify | Notify the user about the summary document |


