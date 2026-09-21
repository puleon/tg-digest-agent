"""The research-task grading of scripts/agent_eval.py and the shape of its task set."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import yaml

from tgdigest.eval.langfuse_sync import _items


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_eval", Path("scripts/agent_eval.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TASK = {"id": "t", "query": "q", "must_mention": [["батист"], ["хёрст", "травм"]]}


def test_research_success_needs_answer_citations_and_every_fact_group() -> None:
    mod = _load()
    good = {"answer": "Кратоса сыграет Дэйв Батиста: Хёрст выбыл.", "citations": [1]}
    assert mod.research_success(TASK, good) == (True, "ok")
    ok, why = mod.research_success(TASK, {**good, "answer": "Кратоса сыграет Дэйв Батиста."})
    assert not ok and why == "missing: хёрст/травм"
    assert mod.research_success(TASK, {**good, "citations": []}) == (False, "no citations")
    assert mod.research_success(TASK, {**good, "caveat": "budget"}) == (False, "caveat")
    assert mod.research_success(TASK, {"answer": ""}) == (False, "no answer")


def test_research_task_set_is_well_formed() -> None:
    tasks = yaml.safe_load(Path("docs/experiments/d9-agent/research.yaml").read_text("utf-8"))
    assert len(tasks) == 20 and len({t["id"] for t in tasks}) == 20
    for t in tasks:
        assert t["topic"] in ("cinema", "scifi", "humor")
        assert t["must_mention"] and all(g for g in t["must_mention"])
        assert all(k == k.lower() for g in t["must_mention"] for k in g)
    items = _items("research-tasks", tasks)
    assert items[0]["id"] == "research-tasks:r01"
    assert items[0]["expected_output"] == {"must_mention": tasks[0]["must_mention"]}
