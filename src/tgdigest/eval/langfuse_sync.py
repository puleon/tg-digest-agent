"""Langfuse datasets and experiment runs for the §8 test sets (SPEC §7.3).

The evaluation scripts do the real work locally (they need the box's models and hours of
CPU); what goes to Langfuse is the *record*: each test set as a dataset, each scored run as a
dataset run with per-item scores, so two versions of the system can be compared in the UI.
Items are pushed with stable ids (``<dataset>:<item id>``) — a re-push updates, never
duplicates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger(__name__)

RETRIEVAL_QUERIES = Path("docs/experiments/d8-retrieval/queries.yaml")
ROUTER_REQUESTS = Path("docs/experiments/d9-agent/routes.yaml")
INJECTION_ATTACKS = Path("docs/experiments/d17-injection/attacks.yaml")

DATASETS: dict[str, tuple[Path, str]] = {
    "retrieval-queries": (RETRIEVAL_QUERIES, "40 search requests, 12–14 per topic (SPEC §8.1)"),
    "router-requests": (ROUTER_REQUESTS, "30 requests with the expected mode/topic/period"),
    "injection-attacks": (INJECTION_ATTACKS, "45 planted posts with success predicates (§8.4)"),
}


def _items(name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, r in enumerate(rows):
        item_id = str(r.get("id") or i)
        if name == "router-requests":
            inp = {"query": r["query"]}
            expected = {k: r.get(k) for k in ("mode", "topic", "period", "exclude_spoilers")}
        elif name == "injection-attacks":
            inp = {"query": r["query"], "post_text": r["post_text"], "ocr": r.get("ocr")}
            expected = {"success": r["success"], "canary": r.get("canary")}
        else:
            inp = {"query": r["query"]}
            expected = None
        meta = {k: v for k, v in r.items() if k not in ("query", "post_text", "ocr", "page")}
        out.append(
            {"id": f"{name}:{item_id}", "input": inp, "expected_output": expected, "metadata": meta}
        )
    return out


def push_datasets(client: Any, names: Sequence[str] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, (path, description) in DATASETS.items():
        if names and name not in names:
            continue
        rows = list(yaml.safe_load(path.read_text(encoding="utf-8")))
        client.create_dataset(name=name, description=description, metadata={"source": str(path)})
        items = _items(name, rows)
        for it in items:
            client.create_dataset_item(
                dataset_name=name,
                id=it["id"],
                input=it["input"],
                expected_output=it["expected_output"],
                metadata=it["metadata"],
            )
        counts[name] = len(items)
        log.info("dataset_pushed", dataset=name, items=len(items))
    client.flush()
    return counts


def record_run(
    client: Any,
    dataset: str,
    run_name: str,
    outputs: dict[str, Any],
    scores: Mapping[str, Mapping[str, float | str]],
    *,
    description: str | None = None,
    metadata: dict[str, str] | None = None,
) -> Any:
    """Attach precomputed outputs and scores to a dataset run.

    ``outputs`` and ``scores`` are keyed by the item id used at push time (``<dataset>:<id>``);
    items without a recorded output are skipped rather than re-run.
    """
    ds = client.get_dataset(dataset)
    items = [it for it in ds.items if it.id in outputs]

    def task(*, item: Any, **kw: Any) -> Any:
        return outputs.get(item.id)

    result = client.run_experiment(
        name=run_name,
        run_name=run_name,
        description=description,
        data=items,
        task=task,
        evaluators=[],
        max_concurrency=4,
        metadata=metadata,
    )
    for item_result in result.item_results:  # per-item scores go to the item's trace
        item_id = str(getattr(item_result.item, "id", "") or "")
        trace_id = getattr(item_result, "trace_id", None)
        if item_id in scores and trace_id:
            for score_name, value in scores[item_id].items():
                if isinstance(value, str):
                    client.create_score(
                        trace_id=trace_id, name=score_name, value=value, data_type="CATEGORICAL"
                    )
                else:
                    client.create_score(trace_id=trace_id, name=score_name, value=float(value))
    client.flush()
    log.info("run_recorded", dataset=dataset, run=run_name, items=len(items))
    return result
