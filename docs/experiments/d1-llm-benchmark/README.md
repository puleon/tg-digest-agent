# D1 — model serving benchmark (CPU-only)

**Question.** Which serving backend and which fast-tier model do we run on the box, and what do
they cost per token and per image? (SPEC §4, §9 D1.)

**Box.** AMD Ryzen 9 9950X (16C/32T, AVX-512 VNNI/BF16), 96 GB DDR5, Ubuntu 24.04, no GPU.
llama.cpp commit `4fea119` (0.4.1-dev, native `-march=native` build) and the official
`ghcr.io/ggml-org/llama.cpp:server-v0.4.1` image (runtime CPU dispatch); Ollama 0.32.15.
Context 16384 split over 2 slots, prompt cache disabled for the measurement, greedy decoding.
The `-tb 16` and Gemma runs overlapped with a 10 MB/s model download and idle Ollama models
resident in RAM — treat ±5 % as noise.

**Harness.** [`scripts/bench_serving.sh`](../../../scripts/bench_serving.sh) starts each
backend/model, [`scripts/bench_llm.py`](../../../scripts/bench_llm.py) measures per target:
cold prefill on a 3.9k-token Russian prompt, generation of 200 tokens, and one 800×600
synthetic meme ([`scripts/make_sample_image.py`](../../../scripts/make_sample_image.py)) with a
caption + OCR JSON prompt (150 tokens max). Medians of 3 runs; raw JSON in [`results/`](results/).

## Results — fast tier (mass operations + VLM/OCR)

| target | quant | threads (gen/batch) | prefill tok/s | gen tok/s | image (s) |
|---|---|---|---|---|---|
| llama-server native · Qwen3.6-35B-A3B | Q4_K_M 20.4 GB | 16 / 32 | 229 | 19.2 | 10.6 |
| llama-server native · Qwen3.6-35B-A3B | Q4_K_M | 16 / **16** | **260** | 19.0 | — |
| llama-server native · Qwen3.6-35B-A3B | Q4_K_M | **32** / 32 | 211 | 15.7 | — |
| llama-server native · Qwen3.6-35B-A3B + MTP draft | Q4_K_M + mtp Q4_0 | 16 / 32 | 209 | 15.9 | — |
| llama-server **docker** · Qwen3.6-35B-A3B | Q4_K_M | 16 / 32 | 223 | 19.0 | 9.4 |
| Ollama · Qwen3.6-35B-A3B (same GGUF imported) | Q4_K_M | auto | 266 ¹ | 18.9 | n/a ² |
| llama-server native · Gemma 4 26B-A4B | UD-Q4_K_M 17.0 GB | 16 / 32 | 184 | 16.9 | 15.7 |
| llama-server native · Gemma 4 26B-A4B, `-fa on` | UD-Q4_K_M | 16 / 16 | 208 | 16.6 | 11.3 |
| llama-server docker · Gemma 4 26B-A4B | UD-Q4_K_M | 16 / 32 | 204 | 16.9 | 14.4 |
| Ollama · gemma4:26b (Ollama's own quant) | Q4_K_M 18.6 GB | auto | 225 ¹ | **26.3** | **5.2** ³ |

¹ Ollama's OpenAI endpoint returns no timings; prefill is wall-clock over `prompt_tokens`
with `max_tokens=1`, so it includes HTTP and scheduling overhead and is slightly *under*-stated.
² The imported GGUF has no vision projector. ³ Gemma 4 in Ollama answered the image request with
empty `content` (thinking mode consumed the budget) — latency only, not a usable answer.

Memory (docker `stats`, model loaded, 2 slots × 8k): Qwen3.6-35B-A3B **23 GiB**.

## OCR probe (one Russian meme, thinking off)

| model | image tokens | latency | OCR of «КОГДА ДЕДЛАЙН БЫЛ ВЧЕРА / 99% / А ТЫ ЕЩЁ ВЫБИРАЕШЬ ШРИФТ» |
|---|---|---|---|
| Gemma 4 26B-A4B | 262 | 10.6 s | verbatim, correct |
| Qwen3.6-35B-A3B, default | 515 | 18.4 s | «Когда делаешь быстра … выбираешь широт» — wrong |
| Qwen3.6-35B-A3B, `--image-min-tokens 1024` | 1076 | 28.1 s | verbatim, correct |

With thinking left on, Gemma spends the whole 200-token budget in `reasoning_content` and
returns empty `content`. Both presets now run with `reasoning = off`.

## Findings

1. **Docker ≈ native.** The official CPU image (runtime dispatch over AVX-512 variants) is within
   3 % of the `-march=native` build on prefill and identical on generation → `make up` runs the
   image; no host build step.
2. **SMT hurts.** 32 generation threads: −18 % gen; 32 batch threads: −12 % prefill vs 16.
   Both set to 16 (physical cores).
3. **MTP speculative decoding does not pay on CPU** for Qwen3.6 (−17 % gen): draft
   verification costs more than it saves. Off.
4. **Gemma 4 26B-A4B is the faster VLM/OCR path**: 262 image tokens vs ≥1024 that Qwen3.6
   needs to read Cyrillic reliably → 10.6 s vs 28 s per image with correct OCR. Extrapolated to
   the ~10k images of the corpus: ~30 h vs ~78 h of background compute.
5. **Ollama runs Gemma 4 55 % faster at generation (26 vs 17 tok/s) and 2× faster per image**
   than llama.cpp with the unsloth quant. Unexplained (different quant, own runner); the Ollama
   blob is not readable by our user, so the quant-vs-engine split is untested. See open questions.
6. llama-server router mode occasionally closes an idle keep-alive connection right after a
   long request; the next request on that socket fails client-side with "server disconnected".
   The OpenAI SDK retries this transparently; the benchmark uses `Connection: close`.

## Decisions

- Serving: **llama-server in Docker, router mode** (`docker/llm/models.ini`), threads 16/16,
  thinking off for the fast tier, `image-min-tokens = 1024` for Qwen3.6.
- Fast-tier default stays **Qwen3.6-35B-A3B** (Apache-2.0, best text throughput); Gemma 4 is the
  favourite for the VLM branch. **D4 decides by numbers**: CER on 50 hand-transcribed images and
  seconds/image on the same 200 posts, both models, same prompt.
- Heavy tier (gpt-oss-120b MXFP4, 63 GB): benchmarked when the download completes (below).

## Open questions

- Why is Ollama's Gemma 4 generation 55 % faster than llama.cpp's? Try the `ggml-org` Q4_0
  GGUF in llama.cpp and, if the gap persists, an `ollama` compose service for the VLM branch.
- Prefill batching for ingest: `-np 2` vs `-np 4` with short prompts (throughput, not latency).

## Reproduce

```bash
make sync && make remote T=bench-serving   # or on the box: scripts/bench_serving.sh --image data/samples/meme.png
make pull-results
```
