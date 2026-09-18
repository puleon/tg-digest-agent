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

**Metrics** — model-serving benchmark pending download completion (numbers go to
`docs/experiments/d1-llm-benchmark.md`).

**Open** — `make up` on the server with real models; benchmark; pick fast/heavy tiers.
