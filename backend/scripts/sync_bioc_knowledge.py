"""Synchronise the curated QMD Bioconductor knowledge catalog.

Examples:
    python scripts/sync_bioc_knowledge.py
    python scripts/sync_bioc_knowledge.py --book osca --stable-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
import json

from app.config import settings
from app.database import SessionLocal
from app.services.bioc_knowledge import sync_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", dest="book", help="Synchronise only one catalog slug")
    parser.add_argument("--include-preview", action="store_true", help="Also sync the devel/preview channel")
    parser.add_argument("--stable-only", action="store_true", help="Explicitly select only the stable channel")
    args = parser.parse_args()
    channels = ("stable", "preview") if args.include_preview and not args.stable_only else ("stable",)
    db = SessionLocal()
    try:
        result = sync_catalog(
            db,
            settings.bioc_knowledge_catalog_path,
            storage_root=settings.bioc_knowledge_storage_dir,
            channels=channels,
            only_slug=args.book,
        )
    finally:
        db.close()
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
