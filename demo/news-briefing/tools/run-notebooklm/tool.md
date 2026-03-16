---
name: run-notebooklm
description: Call NotebookLM CLI to generate a briefing from today's collected articles
command: python run.py
environment:
  INPUT_DB: data/news.db
  OUTPUT_FILE: data/briefing.md
  NOTEBOOKLM_API_KEY: ${NOTEBOOKLM_API_KEY}
---

Queries data/news.db for today's articles, sends them to NotebookLM CLI,
writes the generated briefing markdown to data/briefing.md.
