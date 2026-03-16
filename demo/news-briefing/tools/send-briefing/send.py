#!/usr/bin/env python3
"""POST the generated briefing to the distribution API."""

import os
import sys
from datetime import date
import urllib.request
import urllib.error
import json

input_file = os.environ.get("INPUT_FILE", "data/briefing.md")
endpoint = os.environ.get("API_ENDPOINT", "")
api_key = os.environ.get("API_KEY", "")

if not endpoint:
    print("API_ENDPOINT not set", file=sys.stderr)
    sys.exit(1)

with open(input_file) as f:
    content = f.read()

payload = json.dumps({
    "date": date.today().isoformat(),
    "content": content,
    "format": "markdown",
}).encode()

req = urllib.request.Request(
    endpoint,
    data=payload,
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    method="POST",
)

try:
    with urllib.request.urlopen(req) as resp:
        print(f"Published: HTTP {resp.status}")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.reason}", file=sys.stderr)
    sys.exit(1)
