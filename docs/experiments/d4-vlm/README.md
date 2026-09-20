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

The owner checked every image against the prefilled Qwen3.6 transcription and changed nothing:
gold therefore equals Qwen3.6's output on all 36 images that carry text (14 have none), and
Qwen3.6's CER is 0 by construction — the informative number is Gemma's.

| model | CER raw | CER normalized | exact match (normalized) | s / image (single stream) |
|---|---|---|---|---|
| Qwen3.6-35B-A3B (1 024+ image tokens) | 0.042 | **0.000** | 36/36 | 23.5 |
| Gemma 4 26B-A4B (~260 image tokens) | 0.178 | **0.111** | 22/36 | 11.1 |

Gemma's errors, by kind (normalized CER per image): two complete misses (a small caption on a
photo, 1.00; a translated-tweet screenshot, 0.97), three dense English posters/headlines with
dropped lines (0.33–0.59), one tiny UI string (0.53), and small slips on Russian text
(«ГОЛОСНЫЙ» for «голосуй», «1833» for «1933»; 0.01–0.10). On typical Russian memes both models
are verbatim. Per topic: humor 0.083, cinema 0.077. Files: [`results/ocr_labels.csv`](results/ocr_labels.csv),
[`results/pred_qwen3.6.csv`](results/pred_qwen3.6.csv), [`results/pred_gemma4.csv`](results/pred_gemma4.csv).

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

**Gemma 4 26B-A4B for the vision tier; Qwen3.6-35B-A3B stays the fast (text) tier.**
Qwen3.6 reads better (CER 0 vs 0.11) but costs 3.3× the throughput; on this box the full
pass is ≈ 30 h with Gemma and ≈ 100 h with Qwen, and the CPU is also needed for D5–D7 in the
same days. Gemma's losses concentrate in dense English screenshots and tiny captions — partial
text still retrieves — while Russian meme text, the retrieval-critical case, is verbatim on both.

Queued as a measured follow-up rather than a guess: a Qwen3.6 second opinion only on images
where Gemma reports no text (`has_text = false`), where the two complete misses live, with the
retrieval ablation of D8 deciding whether it pays.

Gold caveat: gold was produced by correcting Qwen3.6's output, so it is anchored to Qwen3.6
where the human missed an error; the 14 model disagreements were flagged and inspected
individually, which bounds that risk.
