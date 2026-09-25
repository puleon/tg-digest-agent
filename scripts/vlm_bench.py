"""D4 — VLM throughput on real posts: seconds per image and images/minute at a given concurrency.

Runs the ingest vision prompt (same schema, same max_tokens) over N sampled image posts.
The server must expose at least `--concurrency` slots (llm-stack/models.ini: np).

  VLM_MODEL=gemma-4-26b-a4b uv run python scripts/vlm_bench.py --n 40 --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletionMessageParam
from sqlalchemy import select

from tgdigest.config import get_settings
from tgdigest.db.base import make_engine, make_session_factory
from tgdigest.db.models import Channel, Post
from tgdigest.ingest.schemas import VisionResult
from tgdigest.llm.client import LLMClient, LLMError, LLMOutputError, image_part
from tgdigest.prompts import load_prompt


async def sample_paths(n: int, seed: int) -> list[Path]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_session_factory(engine)() as session:
            stmt = (
                select(Post.media_path)
                .join(Channel, Channel.id == Post.channel_id)
                .where(Channel.topic.in_(["humor", "cinema"]), Post.media_type == "photo")
                .where(Post.media_path.is_not(None))
            )
            paths = [p for p in (await session.execute(stmt)).scalars() if p]
    finally:
        await engine.dispose()
    random.Random(seed).shuffle(paths)
    return [settings.media_dir / p for p in paths[:n]]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    settings = get_settings()
    client = LLMClient(settings)
    prompt = load_prompt("vlm_describe", 1)
    paths = await sample_paths(args.n, args.seed)
    sem = asyncio.Semaphore(args.concurrency)
    latencies: list[float] = []
    prompt_tokens: list[int] = []
    errors = 0
    ocr_nonempty = 0

    async def one(path: Path) -> None:
        nonlocal errors, ocr_nonempty
        image = path.read_bytes()
        mime = "image/png" if path.suffix == ".png" else "image/jpeg"
        content: list[Any] = [{"type": "text", "text": prompt}, image_part(image, mime)]
        messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": content}]
        async with sem:
            t = time.perf_counter()
            try:
                result, comp = await client.structured(
                    messages, VisionResult, tier="vlm", max_tokens=args.max_tokens
                )
                prompt_tokens.append(comp.usage.prompt_tokens)
                ocr_nonempty += bool(result.ocr_text.strip())
            except (LLMError, LLMOutputError):
                errors += 1
            latencies.append(time.perf_counter() - t)

    # warm-up: make sure the model is loaded before the clock starts
    await client.complete([{"role": "user", "content": "hi"}], tier="vlm", max_tokens=1)
    t0 = time.perf_counter()
    await asyncio.gather(*(one(p) for p in paths))
    wall = time.perf_counter() - t0
    report = {
        "model": settings.vlm_model,
        "n": len(paths),
        "concurrency": args.concurrency,
        "max_tokens": args.max_tokens,
        "wall_s": round(wall, 1),
        "images_per_min": round(60 * len(paths) / wall, 1),
        "latency_median_s": round(statistics.median(latencies), 1),
        "prompt_tokens_median": statistics.median(prompt_tokens) if prompt_tokens else None,
        "ocr_nonempty": ocr_nonempty,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
