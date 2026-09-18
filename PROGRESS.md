# Progress log

One entry per plan day (SPEC §9). Format: what was done · metrics · what failed · what moved.

## D1 — 2026-09-18 — Foundation

**Done**
- Repository scaffold: `src/tgdigest` package (settings via pydantic-settings, structlog,
  health checks, Typer CLI), `pyproject.toml` (uv, ruff, mypy strict, pytest), Makefile,
  pre-commit, GitHub Actions (lint + types + unit tests + gitleaks), `.env.example`, MIT license.
- `docker-compose.yml`: Postgres 16 (shared instance, separate `langfuse` DB), Qdrant 1.19,
  llama-server 0.4.1 in router mode (`docker/llm/models.ini`, on-demand load/unload,
  `--models-max 1`, idle sleep), Langfuse 4.38 with ClickHouse / Redis / MinIO and headless
  bootstrap of org/project/keys. Everything bound to 127.0.0.1; UIs over `make tunnel`.
- Server inventory (Ubuntu 24.04, 9950X 16C/32T with AVX-512 VNNI/BF16, 96 GB, 1.5 TB NVMe
  free, Docker 29, Ollama 0.32 already running). Native llama.cpp build (commit 4fea119,
  AVX-512 + VNNI). Candidate GGUFs downloading: Qwen3.6-35B-A3B Q4_K_M (+mmproj, +MTP draft),
  Gemma 4 26B-A4B UD-Q4_K_M (+mmproj), gpt-oss-120b MXFP4.
- Benchmark harness `scripts/bench_llm.py` (prefill tok/s, generation tok/s, load time,
  image latency; llama-server timings or wall-clock).

**Decisions / deviations from SPEC**
- SPEC §4 was written against vLLM + Qwen3-8B + Qwen2.5-VL-7B + an API model; it now says
  llama.cpp + local-only (agreed on 2026-09-18, see §2.8). Newer multimodal MoE models
  (Qwen3.6-35B-A3B, Gemma 4 26B-A4B) can serve both the mass-ops and the VLM tier with one
  weight set — the D1 benchmark decides.
- The collector CLI is `python -m tgdigest.collector …` (one package) rather than the
  spec's literal `python -m collector …`.
- The server is a workstation with a GNOME session: realistic RAM budget for us is ~80–85 GB.
- Server NIC negotiated 100 Mbit/s → model downloads run at ~10 MB/s (~3 h for ~105 GB).

**Metrics** — serving benchmark, full table and method in
[`docs/experiments/d1-llm-benchmark/`](docs/experiments/d1-llm-benchmark/README.md):

| fast tier (CPU, 16 threads) | prefill tok/s | gen tok/s | s / image (caption+OCR) |
|---|---|---|---|
| Qwen3.6-35B-A3B Q4_K_M, llama-server docker | 223 (260 native, `-tb 16`) | 19.0 | 9.4 (28 with OCR-grade `image-min-tokens 1024`) |
| Gemma 4 26B-A4B UD-Q4_K_M, llama-server docker | 204 | 16.9 | 14.4 (10.6 with thinking off) |
| Gemma 4 26B-A4B, Ollama 0.32 | 225 | 26.3 | 5.2 (empty answer: thinking) |

- Docker image ≈ native build (≤ 3 %) → services stay fully in compose.
- SMT threads hurt (−12…−18 %); MTP speculative decoding hurts (−17 %) — both off.
- OCR probe: Gemma reads Cyrillic verbatim at 262 image tokens; Qwen needs ≥ 1024 (2.6× slower).
- Infra is up on the box: Postgres 16.15, Qdrant 1.19.1, Langfuse 4.38 (headless-bootstrapped
  project + keys), `tgdigest health` green for all but the LLM until models are mounted.

**What did not work / surprises**
- Langfuse's reference compose pulls MinIO from `cgr.dev/chainguard`, which now returns 403 →
  switched to `quay.io/minio/minio` (last community release, 2025-09-07).
- llama-server router mode drops an idle keep-alive socket after long requests → client-side
  "server disconnected" on the next call (SDK retries cover it; benchmark uses `Connection: close`).
- `rsync --delete` from the laptop wiped server-side benchmark outputs once → experiment outputs
  now live in `docs/experiments/*/results/`, excluded from the push and pulled with `make pull-results`.
- Ollama runs Gemma 4 55 % faster at generation than llama.cpp with the unsloth quant — open question.

**Open** — heavy tier (gpt-oss-120b, 63 GB) benchmark once the download completes; channel
list and Telegram credentials from the owner before D2.

## D2 — 2026-09-18 (in progress) — Collector

**Done**
- Schema for all of SPEC §5 as SQLAlchemy 2 models (`src/tgdigest/db/models.py`) + Alembic
  (async env, migration `0001`) applied on the box; `alembic check` reports no drift. One row per
  Telegram message, albums linked by `grouped_id`; `(channel_id, tg_message_id)` unique — the
  idempotency guarantee lives in the database, not in the code path. Collector cursor
  (`last_message_id`, `last_synced_at`) on `channels`.
- Collector (`tgdigest collector …` / `python -m tgdigest.collector …`): YAML corpus → channels
  upsert; sequential incremental sync (`min_id` after the first pass, `offset_date` for the
  first 183 days); commits per batch so a crash or FloodWait resumes from the cursor;
  exponential FloodWait backoff on top of Telethon's own sleeps; photos and image documents
  downloaded, video/animation thumbnails only, deduplicated by Telegram media id across
  channels; `forward_from_*` kept as free dedup ground truth (SPEC §6.1).
- Tests: 25 unit (fake Telegram source + SQLite: idempotent re-runs, incremental cursor,
  FloodWait resume without duplicates, media dedup across channels, thumbnails, failure
  isolation, albums/forwards, YAML normalization, Telethon → row mapping on real TL objects)
  + 1 Postgres integration test (`make test-int`).

**Blocked on the owner** — `config/channels.yaml` (8–14 channels per topic) and Telegram
`api_id`/`api_hash` + one interactive `tgdigest collector login` on the box. "Done when":
3 channels collected and a re-run inserts nothing.
