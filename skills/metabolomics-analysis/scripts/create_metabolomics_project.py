#!/usr/bin/env python3
"""Create a cold-start metabolomics analysis project from the bundled template."""
import argparse
import re
import shutil
from pathlib import Path


def valid_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return key or "metabolomics_analysis"


def replace_tokens(path: Path, title: str, key: str) -> None:
    if path.is_dir():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    text = text.replace("ANALYSIS_TITLE", title)
    text = text.replace("analysis_key", key)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a metabolomics analysis project skeleton")
    parser.add_argument("target_dir", help="Directory to create or populate")
    parser.add_argument("--title", default="Metabolomics analysis", help="Study/report title")
    parser.add_argument("--key", default=None, help="Short lowercase project key")
    parser.add_argument("--force", action="store_true", help="Allow copying into a non-empty directory")
    args = parser.parse_args()

    skill_dir = Path(__file__).resolve().parent.parent
    template_dir = skill_dir / "assets" / "project-template"
    if not template_dir.exists():
        raise SystemExit(f"Template not found: {template_dir}")

    target = Path(args.target_dir).resolve()
    if target.exists() and any(target.iterdir()) and not args.force:
        raise SystemExit(f"Target exists and is not empty: {target}. Use --force to copy into it.")
    target.mkdir(parents=True, exist_ok=True)

    for item in template_dir.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=args.force)
        else:
            shutil.copy2(item, dest)

    key = valid_key(args.key or args.title)
    for path in target.rglob("*"):
        replace_tokens(path, args.title, key)

    for dirname in ("raw", "derived", "results", "report"):
        (target / dirname).mkdir(exist_ok=True)

    print(f"created: {target}")
    print(f"config:  {target / 'config' / 'analysis_plan.R'}")
    print(f"models:  {target / 'config' / 'model_specification.csv'}")
    print(f"next:    fill config paths, then run Rscript code/01_prepare_data.R")


if __name__ == "__main__":
    main()
