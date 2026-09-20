"""Cheap per-message signatures for the dedup cascade (SPEC §6.3 stages 1–3)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from tgdigest.ingest.normalize import dedup_key

MIN_TEXT_KEY_CHARS = 20  # shorter texts ("ха-ха", a bare emoji line) are not evidence of a repost
_DCT_SIZE = 32
_HASH_SIZE = 8


def text_signature(text: str) -> tuple[str | None, str | None]:
    """(normalized key, sha1 of it) or (None, None) when the text is too short to mean anything."""
    key = dedup_key(text)
    if len(key) < MIN_TEXT_KEY_CHARS:
        return None, None
    return key, hashlib.sha1(key.encode()).hexdigest()  # noqa: S324 - not a security hash


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m: np.ndarray = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2 / n)
    m[0, :] /= np.sqrt(2)
    return m


_DCT = _dct_matrix(_DCT_SIZE)


def phash(image: Image.Image) -> int:
    """64-bit perceptual hash (DCT of a 32×32 grayscale, low 8×8 block, median threshold).

    Survives re-encoding, resizing and mild crops — the way memes travel between channels.
    """
    gray = (
        ImageOps.exif_transpose(image)
        .convert("L")
        .resize((_DCT_SIZE, _DCT_SIZE), Image.Resampling.LANCZOS)
    )
    pixels = np.asarray(gray, dtype=np.float64)
    dct = _DCT @ pixels @ _DCT.T
    low = dct[:_HASH_SIZE, :_HASH_SIZE].flatten()
    median = np.median(low[1:])  # skip the DC term
    bits = (low > median).astype(np.uint8)
    bits[0] = 0
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    return value


def is_blank(image: Image.Image) -> bool:
    """A flat frame (black video thumbnail, solid placeholder) carries no visual evidence."""
    gray = ImageOps.exif_transpose(image).convert("L").resize((32, 32))
    return float(np.asarray(gray, dtype=np.float64).std()) < 2.0


def phash_file(path: Path) -> int | None:
    """None for blank images: identical placeholders must not link unrelated posts."""
    with Image.open(path) as img:
        if is_blank(img):
            return None
        return phash(img)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


@dataclass(frozen=True)
class Signature:
    post_id: int
    text_key: str | None
    text_hash: str | None
    file_sha256: str | None
    phash: int | None


def compute_signature(post_id: int, text: str, media_path: Path | None) -> Signature:
    key, thash = text_signature(text)
    fhash = None
    ph = None
    if media_path is not None and media_path.exists() and media_path.stat().st_size > 0:
        try:
            ph = phash_file(media_path)
            blank = ph is None
        except (OSError, ValueError):  # not an image the decoder understands
            ph, blank = None, False
        fhash = None if blank else file_sha256(media_path)
    return Signature(post_id, key, thash, fhash, ph)
