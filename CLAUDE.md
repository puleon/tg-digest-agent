# Working agreement for this repository

- `SPEC.md` (Russian) is the source of truth. Read it at the start of every session; if the
  implementation and the spec diverge, say so instead of silently deviating (SPEC §11.1).
- Keep `PROGRESS.md` current: date, what was done, metrics obtained, what failed, what moved.
- Language: English for code, docstrings, commits, README and docs. Conventional Commits,
  1–2 meaningful commits per plan day, no AI attribution trailers.
- Prompts are never hardcoded — they live in `prompts/` (versioned) and in Langfuse.
- Every component ships with a numeric quality measure; "looks fine" is not a result.
- Secrets only via `.env`; the repo carries `.env.example`.
- Local models only (CPU): everything talks to an OpenAI-compatible endpoint (`LLM_BASE_URL`).
- Dev loop: code on the laptop, services on the server — `make remote T=<target>`, `make tunnel`.
