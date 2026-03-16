---
name: collect-news
description: Fetch latest news articles from configured sources and save to data/news.db
steps:
  - fetch-news
  - save-articles
on_error: continue
---

Fetches articles from RSS feeds and web scrapers.
Deduplicates by URL before saving to avoid repeated entries.
