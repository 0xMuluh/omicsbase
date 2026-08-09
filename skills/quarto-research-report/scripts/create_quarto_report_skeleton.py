#!/usr/bin/env python3
"""Create a scientific Quarto report skeleton from a bundled template."""
import argparse
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Create a Quarto research report skeleton")
    parser.add_argument("target_dir", help="Directory to create or populate")
    parser.add_argument("--title", default="Analysis report", help="Website/report title")
    parser.add_argument(
        "--template",
        choices=("minimal", "metabolomics-full"),
        default="minimal",
        help="Template to copy: minimal planning report or full metabolomics result-site report",
    )
    parser.add_argument("--force", action="store_true", help="Allow copying into a non-empty directory")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    template_name = "metabolomics-report-template" if args.template == "metabolomics-full" else "quarto-site-template"
    template_dir = skill_dir / "assets" / template_name
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

    quarto_yml = target / "code" / "_quarto.yml"
    if quarto_yml.exists():
        text = quarto_yml.read_text(encoding="utf-8")
        text = text.replace('title: "Analysis report"', f'title: "{args.title}"')
        quarto_yml.write_text(text, encoding="utf-8")

    print(f"created:  {target}")
    print(f"template: {args.template}")
    print(f"source:   {target / 'code'}")
    print(f"render:   cd {target / 'code'} && quarto render")


if __name__ == "__main__":
    main()
