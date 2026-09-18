"""D1 benchmark: throughput of candidate models on candidate serving backends (SPEC §4, §9 D1).

Measures, per target, prompt-processing (prefill) tok/s, generation tok/s, model load time and
optionally the latency of one image request. Servers are expected to expose an OpenAI-compatible
``/v1/chat/completions``; llama-server's ``timings`` block is used when present, otherwise
wall-clock over ``usage``.

Example::

    python scripts/bench_llm.py \\
        --target native-qwen=http://127.0.0.1:8081/v1,qwen3.6-35b-a3b \\
        --target ollama-gemma=http://127.0.0.1:11434/v1,gemma4:26b \\
        --image data/samples/meme.jpg --runs 3 --out docs/experiments/d1-bench.json
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

# A ~1.5k-token Russian prompt: prefill cost is what dominates batch ingest (SPEC §6.2).
_PARAGRAPH = (
    "Станислав Лем в «Солярисе» описывает океан, который невозможно понять, но который "
    "отражает людей самим себе; это не история контакта, а история о пределах познания. "
    "Тарковский снял по роману фильм, сместив акцент с эпистемологии на совесть и память, "
    "и с тех пор спор о том, чья версия честнее, не утихает в читательских каналах. "
)
LONG_PROMPT = (
    "Ниже приведён текст. Ответь одним словом: о какой книге идёт речь?\n\n" + _PARAGRAPH * 40
)
SHORT_PROMPT = "Напиши короткую рецензию (150 слов) на фильм «Чужой» 1979 года без спойлеров."
IMAGE_PROMPT = (
    "Return JSON with keys caption (one sentence) and ocr_text (all readable text, "
    "verbatim, empty string if none)."
)


@dataclass
class Target:
    name: str
    base_url: str
    model: str


@dataclass
class Result:
    target: str
    model: str
    load_s: float | None = None
    prefill_tps: list[float] = field(default_factory=list)
    gen_tps: list[float] = field(default_factory=list)
    prompt_tokens: int | None = None
    image_s: list[float] = field(default_factory=list)
    image_reply: str | None = None
    rss_mb: int | None = None
    error: str | None = None

    def row(self) -> dict[str, Any]:
        def med(xs: list[float]) -> float | None:
            return round(statistics.median(xs), 1) if xs else None

        return {
            "target": self.target,
            "model": self.model,
            "load_s": None if self.load_s is None else round(self.load_s, 1),
            "prefill_tps": med(self.prefill_tps),
            "gen_tps": med(self.gen_tps),
            "prompt_tokens": self.prompt_tokens,
            "image_s": med(self.image_s),
            "rss_mb": self.rss_mb,
            "error": self.error,
        }


def _chat(
    client: httpx.Client, t: Target, messages: list[dict[str, Any]], max_tokens: int
) -> tuple[dict[str, Any], float]:
    body = {
        "model": t.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }
    start = time.perf_counter()
    resp = client.post(f"{t.base_url}/chat/completions", json=body)
    elapsed = time.perf_counter() - start
    resp.raise_for_status()
    return resp.json(), elapsed


def _rates(data: dict[str, Any], elapsed: float) -> tuple[float | None, float | None, int]:
    """(prefill tok/s, generation tok/s, prompt tokens) from server timings or wall-clock."""
    usage = data.get("usage") or {}
    p_tok = int(usage.get("prompt_tokens") or 0)
    c_tok = int(usage.get("completion_tokens") or 0)
    timings = data.get("timings")
    if timings:  # llama-server
        return (
            float(timings["prompt_per_second"]),
            float(timings["predicted_per_second"]),
            int(timings["prompt_n"]),
        )
    # Wall-clock fallback (Ollama et al.): attribute everything to the dominant phase.
    if c_tok <= 1:
        return (p_tok / elapsed if elapsed else None, None, p_tok)
    return (None, c_tok / elapsed if elapsed else None, p_tok)


def _rss_mb(pattern: str) -> int | None:
    out = subprocess.run(["ps", "-eo", "rss,args"], capture_output=True, text=True, check=False)
    total = sum(
        int(line.split(None, 1)[0])
        for line in out.stdout.splitlines()[1:]
        if pattern in line and "bench_llm" not in line
    )
    return total // 1024 if total else None


def bench(t: Target, runs: int, image: Path | None, rss_pattern: str | None) -> Result:
    r = Result(target=t.name, model=t.model)
    client = httpx.Client(timeout=1800)
    try:
        # 1. load (first request may pull the model into RAM)
        _, r.load_s = _chat(client, t, [{"role": "user", "content": "Hi"}], 1)
        # 2. prefill
        for _ in range(runs):
            data, el = _chat(client, t, [{"role": "user", "content": LONG_PROMPT}], 1)
            pp, _, r.prompt_tokens = _rates(data, el)
            if pp:
                r.prefill_tps.append(pp)
        # 3. generation
        for _ in range(runs):
            data, el = _chat(client, t, [{"role": "user", "content": SHORT_PROMPT}], 200)
            _, tg, _ = _rates(data, el)
            if tg:
                r.gen_tps.append(tg)
        # 4. one image (caption + OCR), optional
        if image is not None:
            b64 = base64.b64encode(image.read_bytes()).decode()
            mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
            content = [
                {"type": "text", "text": IMAGE_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
            for _ in range(runs):
                data, el = _chat(client, t, [{"role": "user", "content": content}], 150)
                r.image_s.append(el)
            r.image_reply = data["choices"][0]["message"]["content"][:300]
        if rss_pattern:
            r.rss_mb = _rss_mb(rss_pattern)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        r.error = repr(exc)[:300]
    finally:
        client.close()
    return r


def _parse_target(spec: str) -> Target:
    try:
        name, rest = spec.split("=", 1)
        base_url, model = rest.rsplit(",", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected NAME=BASE_URL,MODEL got {spec!r}") from exc
    return Target(name.strip(), base_url.strip().rstrip("/"), model.strip())


def _markdown(rows: list[dict[str, Any]]) -> str:
    cols = [
        "target",
        "model",
        "load_s",
        "prefill_tps",
        "gen_tps",
        "prompt_tokens",
        "image_s",
        "rss_mb",
    ]
    head = "| " + " | ".join(cols) + " |\n|" + "|".join("---" for _ in cols) + "|\n"
    body = "".join("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n" for r in rows)
    errors = "".join(f"\n- {r['target']}: {r['error']}" for r in rows if r.get("error"))
    return head + body + (f"\nErrors:{errors}\n" if errors else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--target", action="append", type=_parse_target, required=True, help="NAME=BASE_URL,MODEL"
    )
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--image", type=Path, default=None, help="image for the VLM latency probe")
    ap.add_argument(
        "--rss-pattern", default=None, help="ps args substring to sum RSS over (host only)"
    )
    ap.add_argument("--out", type=Path, default=None, help="write JSON results here")
    args = ap.parse_args(argv)

    rows = []
    for t in args.target:
        print(f"== {t.name} ({t.model}) @ {t.base_url}", file=sys.stderr)
        res = bench(t, args.runs, args.image, args.rss_pattern)
        rows.append(res.row() | {"image_reply": res.image_reply})
        print(json.dumps(rows[-1], ensure_ascii=False), file=sys.stderr)
    print(_markdown(rows))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {"targets": [asdict(t) for t in args.target], "runs": args.runs, "results": rows},
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
