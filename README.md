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

