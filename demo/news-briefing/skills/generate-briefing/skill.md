---
name: generate-briefing
description: Query today's news, run NotebookLM to generate briefing, post to API
steps:
  - run-notebooklm
  - send-briefing
on_error: stop
---

Reads today's articles from data/news.db, calls NotebookLM CLI to
produce a structured briefing markdown file, then calls the distribution
API CLI to publish it.
