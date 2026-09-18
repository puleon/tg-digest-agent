#!/usr/bin/env bash
# D1: model-serving benchmark matrix on the CPU box (SPEC §9 D1).
# Starts each backend/model combination, runs scripts/bench_llm.py against it, stops it.
# Usage: scripts/bench_serving.sh [--image path] [--runs N] [--only native|docker|ollama]
# Results: docs/experiments/d1-llm-benchmark/results/<backend>-<model>.json (+ combined table on stdout).
# Results are produced on the server and pulled back with `make pull-results`.
set -euo pipefail

MODELS_DIR=${MODELS_DIR:-$HOME/models}
LLAMA_BIN=${LLAMA_BIN:-$HOME/src/llama.cpp/build/bin}
OUT_DIR=${OUT_DIR:-docs/experiments/d1-llm-benchmark/results}
NATIVE_PORT=8081
RUNS=3
IMAGE=""
ONLY=""
THREADS=${LLM_THREADS:-16}
THREADS_BATCH=${LLM_THREADS_BATCH:-16}

while [[ $# -gt 0 ]]; do
  case $1 in
    --image) IMAGE=$2; shift 2 ;;
    --runs) RUNS=$2; shift 2 ;;
    --only) ONLY=$2; shift 2 ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
mkdir -p "$OUT_DIR"
IMG_ARG=(); [[ -n $IMAGE ]] && IMG_ARG=(--image "$IMAGE")

wait_health() { # url
  for _ in $(seq 1 120); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 2; done
  echo "server at $1 did not become healthy" >&2; return 1
}

bench() { # name base_url model rss_pattern
  uv run python scripts/bench_llm.py --target "$1=$2,$3" --runs "$RUNS" "${IMG_ARG[@]}" \
    ${4:+--rss-pattern "$4"} --out "$OUT_DIR/$1.json" 2>&1 | tail -n +1
}

# --- native llama-server (built with -march=native) ---------------------------------------
native() { # name model_path extra_args...
  local name=$1 model=$2; shift 2
  echo "### native: $name"
  "$LLAMA_BIN/llama-server" -m "$model" --host 127.0.0.1 --port $NATIVE_PORT \
    -t "$THREADS" -tb "$THREADS_BATCH" -c 16384 -np 2 --jinja "$@" >"$OUT_DIR/$name.server.log" 2>&1 &
  local pid=$!
  wait_health "http://127.0.0.1:$NATIVE_PORT/health"
  bench "$name" "http://127.0.0.1:$NATIVE_PORT/v1" "$(basename "$model")" "llama-server"
  kill "$pid"; wait "$pid" 2>/dev/null || true
}

Q=$MODELS_DIR/qwen3.6-35b-a3b/Qwen3.6-35B-A3B-Q4_K_M.gguf
QD=$(dirname "$Q")
G=$MODELS_DIR/gemma-4-26b-a4b/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf
H=$MODELS_DIR/gpt-oss-120b/gpt-oss-120b-MXFP4.gguf
# Models still downloading are skipped (the downloader only moves a file into place when complete).
if [[ -z $ONLY || $ONLY == native ]]; then
  if [[ -f $Q ]]; then
    native native-qwen3.6-35b-a3b "$Q" --mmproj "$QD/mmproj-Qwen3.6-35B-A3B-Q8_0.gguf" --reasoning off
    native native-qwen3.6-35b-a3b-mtp "$Q" --reasoning off \
      --spec-type draft-mtp --model-draft "$QD/mtp-Qwen3.6-35B-A3B-Q4_0.gguf"
    native native-qwen3.6-35b-a3b-t32 "$Q" --reasoning off -t 32
    native native-qwen3.6-35b-a3b-tb16 "$Q" --reasoning off -tb 16
  fi
  [[ -f $G ]] && native native-gemma-4-26b-a4b "$G" --mmproj "$(dirname "$G")/mmproj-BF16.gguf"
  [[ -f $H ]] && native native-gpt-oss-120b "$H" -c 32768
fi

# --- docker llama-server (compose `llm` service, router mode, runtime CPU dispatch) ---------
if [[ -z $ONLY || $ONLY == docker ]]; then
  echo "### docker llama-server (compose llm)"
  docker compose up -d --wait llm
  [[ -f $Q ]] && bench docker-qwen3.6-35b-a3b http://127.0.0.1:8080/v1 qwen3.6-35b-a3b ""
  [[ -f $G ]] && bench docker-gemma-4-26b-a4b http://127.0.0.1:8080/v1 gemma-4-26b-a4b ""
  [[ -f $H ]] && bench docker-gpt-oss-120b http://127.0.0.1:8080/v1 gpt-oss-120b ""
  docker compose stop llm
fi

# --- ollama (host service on 11434) ---------------------------------------------------------
if [[ -z $ONLY || $ONLY == ollama ]] && curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "### ollama"
  # Same GGUF as the llama-server runs, imported once, so the engines are compared on equal weights.
  if [[ -f $Q ]] && ! ollama list 2>/dev/null | grep -q "^qwen3.6-35b-a3b-q4km"; then
    printf 'FROM %s\n' "$Q" > "$OUT_DIR/Modelfile.qwen3.6" && ollama create qwen3.6-35b-a3b-q4km -f "$OUT_DIR/Modelfile.qwen3.6" >/dev/null
  fi
  for m in $(curl -fsS http://127.0.0.1:11434/api/tags | python3 -c 'import sys,json; print(" ".join(m["name"] for m in json.load(sys.stdin)["models"]))'); do
    bench "ollama-${m//[:\/]/-}" http://127.0.0.1:11434/v1 "$m" "ollama"
  done
fi

echo; echo "### combined"
uv run python - "$OUT_DIR" <<'PY'
import json, sys, pathlib
rows = []
for p in sorted(pathlib.Path(sys.argv[1]).glob("*.json")):
    rows += json.loads(p.read_text())["results"]
cols = ["target", "model", "load_s", "prefill_tps", "gen_tps", "prompt_tokens", "image_s", "rss_mb"]
print("| " + " | ".join(cols) + " |"); print("|" + "|".join("---" for _ in cols) + "|")
for r in rows: print("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
PY
