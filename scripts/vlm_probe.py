"""Send one image to an OpenAI-compatible VLM endpoint and print the raw answer fields.

Usage: uv run python scripts/vlm_probe.py IMAGE [--model M] [--base-url URL] [--max-tokens N]
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

import httpx

PROMPT = (
    "Return JSON with keys caption (one sentence) and ocr_text (all readable text, verbatim, "
    "empty string if none)."
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--model", default="qwen3.6-35b-a3b")
    ap.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--think", action="store_true", help="leave the model's thinking mode on")
    args = ap.parse_args()

    b64 = base64.b64encode(args.image.read_bytes()).decode()
    mime = "image/png" if args.image.suffix.lower() == ".png" else "image/jpeg"
    body: dict[str, object] = {
        "model": args.model,
        "max_tokens": args.max_tokens,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ],
    }
    if not args.think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
        body["reasoning_effort"] = "none"
    t = time.perf_counter()
    resp = httpx.post(f"{args.base_url}/chat/completions", json=body, timeout=600)
    elapsed = time.perf_counter() - t
    resp.raise_for_status()
    data = resp.json()
    choice = data["choices"][0]
    msg = choice["message"]
    print(f"{elapsed:.1f}s finish={choice['finish_reason']} usage={json.dumps(data.get('usage'))}")
    if data.get("timings"):
        t_ = data["timings"]
        print(f"timings: prompt_n={t_['prompt_n']} predicted_n={t_['predicted_n']}")
    print("content:", msg.get("content"))
    for key in ("reasoning_content", "reasoning"):
        if msg.get(key):
            print(f"{key}: {str(msg[key])[:500]}")


if __name__ == "__main__":
    main()
