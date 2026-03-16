---
name: send-briefing
description: POST the generated briefing file to the distribution API
command: python send.py
environment:
  INPUT_FILE: data/briefing.md
  API_ENDPOINT: ${BRIEFING_API_ENDPOINT}
  API_KEY: ${BRIEFING_API_KEY}
---

Reads the briefing from data/briefing.md and POSTs it to the
distribution API endpoint. Exits non-zero on HTTP error.
