#!/usr/bin/env python3
"""Query today's news from DB and generate briefing via NotebookLM CLI."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import json
from datetime import date

db_path = os.environ.get("INPUT_DB", "data/news.db")
output_file = os.environ.get("OUTPUT_FILE", "data/briefing.md")
today = date.today().isoformat()

# Pull today's articles
conn = sqlite3.connect(db_path)
rows = conn.execute(
    "SELECT title, url, summary FROM articles WHERE date(published_at) = ?", (today,)
).fetchall()
conn.close()

if not rows:
    print(f"No articles for {today}, skipping.", file=sys.stderr)
    sys.exit(0)

articles = [{"title": r[0], "url": r[1], "summary": r[2]} for r in rows]

# Write temp input file and call notebooklm CLI
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(articles, f)
    tmp = f.name

result = subprocess.run(
    ["notebooklm", "generate", "--input", tmp, "--output", output_file, "--format", "markdown"],
    capture_output=True, text=True
)

if result.returncode != 0:
    print(result.stderr, file=sys.stderr)
    sys.exit(result.returncode)

print(f"Briefing written to {output_file}")
