#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    print("Usage: validate_reports_json.py <reports.json>", file=sys.stderr)
    sys.exit(2)
path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"reports_json_failed: cannot parse JSON: {exc}", file=sys.stderr)
    sys.exit(1)
if not isinstance(data, list):
    print("reports_json_failed: root must be a list", file=sys.stderr)
    sys.exit(1)
required = {"key", "title", "summary", "visibility", "updated", "area", "authors", "tags", "reportUrl"}
key_re = re.compile(r"^[a-z0-9-]+$")
date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
seen = set()
errors = []
for i, item in enumerate(data):
    label = f"entry[{i}]"
    if not isinstance(item, dict):
        errors.append(f"{label}: must be object")
        continue
    missing = sorted(required - set(item))
    if missing:
        errors.append(f"{label}: missing {', '.join(missing)}")
    key = item.get("key")
    if not isinstance(key, str) or not key_re.match(key):
        errors.append(f"{label}: invalid key {key!r}")
    elif key in seen:
        errors.append(f"{label}: duplicate key {key}")
    else:
        seen.add(key)
    if item.get("visibility") not in {"Public", "Protected"}:
        errors.append(f"{label}: visibility must be Public or Protected")
    if not isinstance(item.get("updated"), str) or not date_re.match(item.get("updated", "")):
        errors.append(f"{label}: updated must be YYYY-MM-DD")
    if not isinstance(item.get("authors"), list):
        errors.append(f"{label}: authors must be list")
    if not isinstance(item.get("tags"), list):
        errors.append(f"{label}: tags must be list")
    if "draft" in item and not isinstance(item["draft"], bool):
        errors.append(f"{label}: draft must be boolean when present")
    manuscript = item.get("manuscript")
    if manuscript is not None and (not isinstance(manuscript, dict) or "label" not in manuscript):
        errors.append(f"{label}: manuscript must be null or object with label")
if errors:
    print("reports_json_failed")
    for err in errors:
        print(err)
    sys.exit(1)
print(f"reports_json_ok: {len(data)} entries")
