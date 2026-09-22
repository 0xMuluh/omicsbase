#!/usr/bin/env python3
"""Build and index documentation and vignettes from installed R packages into knowledge.db.

Extracts documentation statically using R's built-in tools; never executes user code.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.knowledge.indexer import index_package_docs

DEFAULT_PACKAGES = [
    "mia",
    "miaViz",
    "TreeSummarizedExperiment",
    "SingleCellExperiment",
    "SummarizedExperiment",
    "scater",
    "scran",
    "DESeq2",
]


def extract_packages(packages: list[str], output_json: Path) -> Path:
    """Run extract_package_docs.R to extract Rd topics and vignettes."""
    extractor_script = ROOT / "engine" / "knowledge" / "extract_package_docs.R"
    if not extractor_script.exists():
        raise FileNotFoundError(f"Extractor script not found: {extractor_script}")

    cmd = ["Rscript", str(extractor_script), "--output", str(output_json)] + packages
    print(f"Running R extraction for {len(packages)} packages...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Extraction failed with code {res.returncode}:\n{res.stderr}")
        raise RuntimeError(f"R extraction script failed: {res.stderr}")
    print(res.stdout.strip())
    return output_json


def main():
    parser = argparse.ArgumentParser(description="Extract and index R package documentation.")
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "engine" / "knowledge" / "knowledge.db",
        help="Path to SQLite knowledge database",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=ROOT / "engine" / "knowledge" / "package_docs.json",
        help="Path to extracted JSON intermediary",
    )
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip R extraction step and re-index existing JSON",
    )
    parser.add_argument(
        "packages",
        nargs="*",
        default=DEFAULT_PACKAGES,
        help="Optional package names to extract (defaults to core 8)",
    )

    args = parser.parse_args()

    if not args.skip_extract or not args.output_json.exists():
        extract_packages(args.packages, args.output_json)

    print(f"Indexing documentation into {args.db}...")
    stats = index_package_docs(args.output_json, args.db)
    print("\nSummary of indexed packages:")
    for pkg, count in stats.items():
        print(f"  {pkg:<28}: {count:>4} chunks")
    print("\nDone.")


if __name__ == "__main__":
    main()
