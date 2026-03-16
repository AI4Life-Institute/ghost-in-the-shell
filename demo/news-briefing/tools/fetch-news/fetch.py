#!/usr/bin/env python3
"""Fetch news articles from RSS feeds and output as JSON."""

import json
import os
import sys
from datetime import datetime, timezone
import feedparser

sources = os.environ.get("NEWS_SOURCES", "").split(",")
max_articles = int(os.environ.get("MAX_ARTICLES", "50"))

articles = []
for url in sources:
    url = url.strip()
    if not url:
        continue
    feed = feedparser.parse(url)
    for entry in feed.entries[:max_articles]:
        articles.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "summary": entry.get("summary", ""),
            "published_at": entry.get("published", datetime.now(timezone.utc).isoformat()),
            "source": feed.feed.get("title", url),
        })

json.dump(articles, sys.stdout, ensure_ascii=False, indent=2)
