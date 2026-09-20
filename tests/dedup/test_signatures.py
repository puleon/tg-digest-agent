from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from tgdigest.dedup.signatures import (
    compute_signature,
    file_sha256,
    hamming,
    phash,
    text_signature,
)


def _meme(
    text: str, bg: tuple[int, int, int] = (30, 60, 110), size: tuple[int, int] = (400, 300)
) -> Image.Image:
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.rectangle((60, 80, 340, 220), fill=(230, 230, 230))
    d.text((80, 100), text, fill=(0, 0, 0))
    d.ellipse((200, 150, 320, 210), fill=(200, 30, 30))
    return img


def _jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def test_text_signature_ignores_case_links_and_short_texts() -> None:
    key, h = text_signature("Когда дедлайн был ВЧЕРА!!! https://t.me/x")
    key2, h2 = text_signature("когда дедлайн был вчера")
    assert key == key2 and h == h2 and h is not None
    assert text_signature("ха-ха") == (None, None)


def test_phash_survives_reencoding_and_resizing_but_separates_images() -> None:
    original = _meme("КОГДА ДЕДЛАЙН БЫЛ ВЧЕРА")
    h0 = phash(original)
    assert hamming(h0, phash(_jpeg(original, 40))) <= 4
    assert hamming(h0, phash(original.resize((800, 600)))) <= 4
    assert hamming(h0, phash(_jpeg(original.resize((300, 225)), 60))) <= 6
    other = _meme("ДРУГОЙ МЕМ", bg=(200, 200, 40), size=(300, 400))
    assert hamming(h0, phash(other)) > 16
    assert 0 <= h0 < 1 << 64


def test_compute_signature_with_and_without_media(tmp_path: Path) -> None:
    path = tmp_path / "a.jpg"
    _meme("x").save(path, "JPEG")
    sig = compute_signature(1, "Перечитал «Солярис» Лема — не о контакте", path)
    assert sig.text_hash and sig.file_sha256 == file_sha256(path) and sig.phash is not None
    plain = compute_signature(2, "", None)
    assert plain.text_hash is None and plain.file_sha256 is None and plain.phash is None
    broken = tmp_path / "b.jpg"
    broken.write_bytes(b"not an image")
    sig_b = compute_signature(3, "", broken)
    assert sig_b.file_sha256 and sig_b.phash is None


def test_blank_frames_carry_no_signature(tmp_path: Path) -> None:
    black = tmp_path / "black.jpg"
    Image.new("RGB", (320, 134), (0, 0, 0)).save(black, "JPEG")
    sig = compute_signature(9, "", black)
    assert sig.phash is None and sig.file_sha256 is None


def test_dark_frames_with_a_small_logo_are_blank_too(tmp_path: Path) -> None:
    """Near-black trailer thumbnails ("18+", a studio logo) hash to noise: no signature."""
    dark = tmp_path / "dark.jpg"
    frame = Image.new("RGB", (320, 180), (2, 2, 2))
    frame.paste(Image.new("RGB", (30, 12), (120, 40, 40)), (145, 84))
    frame.save(dark, "JPEG")
    assert compute_signature(1, "", dark).phash is None
    photo = tmp_path / "photo.jpg"
    Image.linear_gradient("L").resize((320, 180)).convert("RGB").save(photo, "JPEG")
    assert compute_signature(2, "", photo).phash is not None
