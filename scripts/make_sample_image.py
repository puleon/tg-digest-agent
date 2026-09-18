"""Generate a synthetic meme-like image (Russian caption over a drawing) for VLM/OCR probes.

Run with: uv run --with pillow python scripts/make_sample_image.py data/samples/meme.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]
TOP = "КОГДА ДЕДЛАЙН БЫЛ ВЧЕРА"
BOTTOM = "А ТЫ ЕЩЁ ВЫБИРАЕШЬ ШРИФТ"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def main(out: Path) -> None:
    w, h = 800, 600
    img = Image.new("RGB", (w, h), (30, 60, 110))
    d = ImageDraw.Draw(img)
    # a "monitor" with a red progress bar — something a caption model can describe
    d.rectangle((150, 150, 650, 450), fill=(230, 230, 230), outline=(0, 0, 0), width=6)
    d.rectangle((200, 280, 600, 320), fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    d.rectangle((200, 280, 560, 320), fill=(200, 30, 30))
    d.text((300, 340), "99%", font=_font(40), fill=(0, 0, 0))
    font = _font(44)
    for text, y in ((TOP, 40), (BOTTOM, 500)):
        x = (w - d.textlength(text, font=font)) / 2
        d.text((x, y), text, font=font, fill="white", stroke_width=3, stroke_fill="black")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"wrote {out} ({w}x{h})")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/samples/meme.png"))
