"""Save a screenshot as WebP, at most 1600 pixels wide: to_webp.py in.png out.webp"""
import sys
from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
img = Image.open(src).convert("RGB")
if img.width > 1600:
    img = img.resize((1600, round(img.height * 1600 / img.width)), Image.LANCZOS)
img.save(dst, "WEBP", quality=82, method=6)
