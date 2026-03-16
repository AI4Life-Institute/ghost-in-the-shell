---
name: fetch-news
description: Scrape news articles from RSS feeds and web sources, write JSON to stdout
command: python fetch.py
environment:
  NEWS_SOURCES: "https://feeds.reuters.com/reuters/technologyNews,https://hnrss.org/frontpage"
  MAX_ARTICLES: "50"
---

Fetches from RSS feeds defined in NEWS_SOURCES.
Outputs a JSON array of { title, url, summary, published_at } to stdout.
