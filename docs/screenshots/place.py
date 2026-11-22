"""Put each screenshot under its marker in the docs pages.

A page marks a picture with <!-- screenshot: <name> — <description> -->. When
images/screens/<name>.webp exists, the line after the marker becomes the image, with the
description (up to "; highlight") as its alt text for screen readers. Running it again changes nothing.
"""
import re
from pathlib import Path

docs = Path(__file__).resolve().parent.parent
marker = re.compile(r"^(?P<indent>\s*)<!-- screenshot: (?P<name>[a-z0-9-]+) — (?P<desc>.*?) -->\s*$")
placed = missing = 0
for page in sorted(docs.glob("**/*.qmd")):
    if "archive" in page.parts:
        continue
    lines = page.read_text().split("\n")
    out, i, changed = [], 0, False
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = marker.match(line)
        if m:
            image = docs / "images" / "screens" / f"{m['name']}.webp"
            rel = Path("../" * (len(page.relative_to(docs).parts) - 1)) / "images" / "screens" / image.name
            alt = m["desc"].split("; highlight")[0].strip().replace('"', "'")
            alt = alt[0].upper() + alt[1:]
            wanted = f'{m["indent"]}![]({rel.as_posix()}){{.screenshot fig-alt="{alt}."}}'
            has_image = i + 1 < len(lines) and lines[i + 1].lstrip().startswith("![") and "images/screens/" in lines[i + 1]
            if has_image:
                i += 1  # the old image line is replaced or dropped
            if image.exists():
                placed += 1
                out.append(wanted)
                changed |= not has_image or lines[i] != wanted
            else:
                missing += 1
                changed |= has_image
        i += 1
    if changed:
        page.write_text("\n".join(out))
print(f"screenshots placed: {placed}, markers without an image yet: {missing}")
