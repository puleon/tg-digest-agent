# D4 — VLM branch on real posts: which model reads memes, and how fast

**Question.** Which multimodal model serves the vision branch (caption + OCR, SPEC §6.2 step 2)
for the ~23k images of the corpus, and what does the full pass cost? (SPEC §9 D4.)

**Setup.** llama-server 0.4.1 in Docker, router mode, `--models-max 2` (both candidates stay
resident: 29 GiB together), 16 threads. Prompt `prompts/vlm_describe.v1.md`, JSON schema
`{ocr_text, caption, has_text, is_readable}` (grammar-constrained), thinking off.
Qwen3.6-35B-A3B runs with `image-min-tokens = 1024` (below that it misreads Cyrillic, D1);
Gemma 4 26B-A4B uses its fixed ~260 image tokens.

## OCR quality — 50 hand-transcribed images (25 humor, 25 cinema)

`scripts/ocr_eval.py sample|run|score`: stratified random sample (seed 42), each model
transcribes every image once, CER = Levenshtein / len(gold); "normalized" lowercases, folds
ё→е and drops punctuation. Gold labels are written by a human from the image; the labelling
sheet shows both models' outputs as hints, so a mild anchoring bias toward whichever hint was
right is possible — the two models agree verbatim on 36/50 images, the metric is decided by the
other 14.

*Pending the gold column — filled by the owner; the table is produced by `score`.*

## Throughput — real images, cache-free (`scripts/vlm_bench.py`, 16 images per cell, different images per run)

| model | slots · concurrency | max_tokens | s / image (median latency) | images / min |
|---|---|---|---|---|
| Qwen3.6-35B-A3B | 2 · 1 | 400 | 23.4 | 2.6 |
| Qwen3.6-35B-A3B | 2 · 2 | 400 | 25.9 | 2.5 |
| Gemma 4 26B-A4B | 2 · 1 | 400 | 10.5 | 4.1 |
| Gemma 4 26B-A4B | 2 · 2 | 400 | 14.9 | 5.7 |
| Gemma 4 26B-A4B | 4 · 4 | 400 | 23.6 | 5.0 |
| Gemma 4 26B-A4B | 2 · 2 | 250 | 16.7 | 6.7 |
| Gemma 4 26B-A4B | **4 · 4** | **250** | 22.1 | **8.5** |

Cells use 16 different images each, so ±20 % is sample noise (dense text screenshots cost 2–3×
a plain meme). Raw rows: [`results/throughput.jsonl`](results/throughput.jsonl).

**A measurement trap worth recording.** The first pass of this benchmark reported Qwen at
*19.3 images/min* with two slots — a 7× jump. It reused the same 16 images as the previous run,
and llama-server's RAM prompt cache (`--cache-ram`, on by default) restored their KV state, so
only decoding ran. Every cell above uses a fresh image set; measurements that need cold prefill
must either vary the input or send `cache_prompt: false` (the D1 harness does).

## Findings

1. **Qwen3.6 does not scale with slots** (2.6 → 2.5 images/min): with 1 200 image tokens its
   cost is the vision encoder + prefill, both compute-bound, and batching cannot hide them.
2. **Gemma 4 is 2.3× faster per image and scales**: 4.1 → 5.7 (2 slots) → 8.5 images/min
   (4 slots, 250-token budget). Decoding dominates its cost, and decoding on CPU is
   memory-bandwidth-bound, so concurrent slots share the weight reads.
3. **Output budget matters more than slots**: 400 → 250 tokens is +20–70 %. The ingest graph
   now asks for 300 and the client retries a truncated answer with 600 once.
4. Both tiers can stay resident (`--models-max 2`, 29 GiB) — no model swapping per post, which
   D3 had shown costs +20 s. The heavy model is only needed offline; when it is (D13+), the
   router config switches back to one resident model.

## Full-pass cost (posts that need vision: every humor/cinema image, scifi images with short text)

≈ 14 000 images (+ 3 100 animation thumbnails): Gemma 4 at 8.5 images/min ≈ **28–34 h** of
background compute; Qwen3.6 at 2.6 images/min ≈ 90–110 h.

## Decision

*Pending CER.* Speed alone says Gemma 4 (4 slots, 250–300 tokens). If its CER is within noise
of Qwen3.6's, the vision tier is Gemma 4 and the full pass starts immediately; if Qwen3.6 reads
Cyrillic materially better, the extra ~70 h buy accuracy on the retrieval-critical field.
