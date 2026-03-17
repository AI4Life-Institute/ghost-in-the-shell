---
name: briefing-generator
description: Generate a daily news briefing using NotebookLM CLI, then post to distribution API
trigger:
  type: loop
  schedule: "0 8 * * *"
skills:
  - generate-briefing
on_failure: retry:2
guard:
  session: ghost-ops
---

Reads today's collected articles from data/news.db, runs NotebookLM CLI
to generate a structured briefing, then pushes it to the distribution API.
