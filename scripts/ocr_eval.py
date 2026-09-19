"""D4 — OCR quality of VLM candidates on real posts (SPEC §9 D4: CER on hand-transcribed images).

Three sub-commands, all on the box:

  sample  pick N image posts (stratified over humor/cinema), copy the images to an eval folder
          and write labels.csv with an empty `gold` column for a human to fill in
  run     transcribe every sampled image with one model → predictions CSV (+ latency)
  score   CER of each predictions CSV against the gold column (raw and normalized)

Example:
  uv run python scripts/ocr_eval.py sample --n 50 --out data/eval/ocr
  VLM_MODEL=gemma-4-26b-a4b uv run python scripts/ocr_eval.py run --dir data/eval/ocr --tag gemma
  uv run python scripts/ocr_eval.py score --dir data/eval/ocr
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import random
import re
import shutil
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

IMAGE_TYPES = ("photo", "image")


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return len(a) + len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def normalize(text: str) -> str:
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def cer(gold: str, pred: str) -> float:
    return levenshtein(gold, pred) / max(len(gold), 1)


async def cmd_sample(n: int, out: Path, seed: int) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_session_factory(engine)() as session:
            stmt = (
                select(Post.id, Channel.topic, Post.media_path)
                .join(Channel, Channel.id == Post.channel_id)
                .where(Channel.topic.in_(["humor", "cinema"]), Post.media_type.in_(IMAGE_TYPES))
                .where(Post.media_path.is_not(None))
            )
            rows = (await session.execute(stmt)).all()
    finally:
        await engine.dispose()
    rng = random.Random(seed)
    per_topic = n // 2
    picked = []
    for topic in ("humor", "cinema"):
        pool = [r for r in rows if r.topic == topic]
        rng.shuffle(pool)
        picked += pool[:per_topic]
    out.mkdir(parents=True, exist_ok=True)
    with (out / "labels.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["post_id", "topic", "image", "gold"])
        for r in picked:
            src = settings.media_dir / r.media_path
            dst = out / f"{r.id}{src.suffix}"
            shutil.copyfile(src, dst)
            w.writerow([r.id, r.topic, dst.name, ""])
    print(f"sampled {len(picked)} images into {out} — fill the `gold` column in labels.csv")


async def cmd_run(directory: Path, tag: str, max_tokens: int) -> None:
    settings = get_settings()
    client = LLMClient(settings)
    prompt = load_prompt("vlm_describe", 1)
    rows = list(csv.DictReader((directory / "labels.csv").open(encoding="utf-8")))
    out = directory / f"pred_{tag}.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["post_id", "ocr_text", "caption", "seconds", "prompt_tokens", "error"])
        for i, r in enumerate(rows, 1):
            image = (directory / r["image"]).read_bytes()
            mime = "image/png" if r["image"].endswith(".png") else "image/jpeg"
            content: list[Any] = [{"type": "text", "text": prompt}, image_part(image, mime)]
            messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": content}]
            t = time.perf_counter()
            try:
                result, comp = await client.structured(
                    messages,
                    VisionResult,
                    tier="vlm",
                    max_tokens=max_tokens,
                )
                w.writerow(
                    [
                        r["post_id"],
                        result.ocr_text,
                        result.caption,
                        f"{time.perf_counter() - t:.1f}",
                        comp.usage.prompt_tokens,
                        "",
                    ]
                )
            except (LLMError, LLMOutputError) as exc:
                w.writerow(
                    [r["post_id"], "", "", f"{time.perf_counter() - t:.1f}", "", repr(exc)[:120]]
                )
            fh.flush()
            print(f"[{tag}] {i}/{len(rows)} {time.perf_counter() - t:.1f}s", flush=True)
    print(f"wrote {out} (model={settings.vlm_model})")


def cmd_score(directory: Path) -> None:
    gold = {
        r["post_id"]: r for r in csv.DictReader((directory / "labels.csv").open(encoding="utf-8"))
    }
    labelled = {k: v for k, v in gold.items() if v["gold"].strip()}
    if not labelled:
        print("no gold labels yet")
        return
    print(f"{len(labelled)} labelled images\n")
    print("| model | CER raw | CER normalized | exact (norm) | median s/img | errors |")
    print("|---|---|---|---|---|---|")
    for pred_file in sorted(directory.glob("pred_*.csv")):
        preds = {r["post_id"]: r for r in csv.DictReader(pred_file.open(encoding="utf-8"))}
        raw, norm, exact, secs, errors = [], [], 0, [], 0
        for pid, g in labelled.items():
            p = preds.get(pid)
            if p is None:
                continue
            if p["error"]:
                errors += 1
            raw.append(cer(g["gold"], p["ocr_text"]))
            n_g, n_p = normalize(g["gold"]), normalize(p["ocr_text"])
            norm.append(cer(n_g, n_p))
            exact += n_g == n_p
            if p["seconds"]:
                secs.append(float(p["seconds"]))
        if raw:
            print(
                f"| {pred_file.stem.removeprefix('pred_')} | {statistics.mean(raw):.3f} | "
                f"{statistics.mean(norm):.3f} | {exact}/{len(raw)} | "
                f"{statistics.median(secs):.1f} | {errors} |"
            )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=50)
    s.add_argument("--out", type=Path, default=Path("data/eval/ocr"))
    s.add_argument("--seed", type=int, default=42)
    r = sub.add_parser("run")
    r.add_argument("--dir", type=Path, default=Path("data/eval/ocr"))
    r.add_argument("--tag", required=True)
    r.add_argument("--max-tokens", type=int, default=400)
    c = sub.add_parser("score")
    c.add_argument("--dir", type=Path, default=Path("data/eval/ocr"))
    args = ap.parse_args()
    if args.cmd == "sample":
        asyncio.run(cmd_sample(args.n, args.out, args.seed))
    elif args.cmd == "run":
        asyncio.run(cmd_run(args.dir, args.tag, args.max_tokens))
    else:
        cmd_score(args.dir)


if __name__ == "__main__":
    main()
