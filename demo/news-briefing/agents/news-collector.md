---
name: news-collector
description: Collect AI and tech news from the web every hour and store in data/news.db
trigger:
  type: loop
  schedule: "0 * * * *"
skills:
  - collect-news
on_failure: retry:3
guard:
  session: ghost-ops
---

Monitors and collects AI/tech news from configured sources every hour.
Saves deduplicated articles to data/news.db for downstream processing.
