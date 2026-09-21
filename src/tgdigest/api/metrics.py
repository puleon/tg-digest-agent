"""A small in-process metrics registry exposed in Prometheus text format (SPEC §6.8 /metrics).

No client library: three metric kinds are enough — counters, a sum/count pair per label set
(what a summary is for latency and tokens), and gauges. Labels are stable strings.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Mapping


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._sums: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, int]] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._help: dict[str, str] = {}

    @staticmethod
    def _key(
        name: str, labels: Mapping[str, str] | None
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted((labels or {}).items()))

    def describe(self, name: str, help_text: str) -> None:
        self._help[name] = help_text

    def inc(self, name: str, labels: Mapping[str, str] | None = None, value: float = 1.0) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += value

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        with self._lock:
            k = self._key(name, labels)
            s, n = self._sums.get(k, (0.0, 0))
            self._sums[k] = (s + value, n + 1)

    def set(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    @staticmethod
    def _fmt(name: str, labels: tuple[tuple[str, str], ...], value: float) -> str:
        if labels:
            inner = ",".join(f'{k}="{v}"' for k, v in labels)
            return f"{name}{{{inner}}} {value:g}"
        return f"{name} {value:g}"

    def render(self) -> str:
        with self._lock:
            lines: list[str] = []
            seen: set[str] = set()

            def header(name: str, kind: str) -> None:
                if name not in seen:
                    seen.add(name)
                    if name in self._help:
                        lines.append(f"# HELP {name} {self._help[name]}")
                    lines.append(f"# TYPE {name} {kind}")

            for (name, labels), v in sorted(self._counters.items()):
                header(name, "counter")
                lines.append(self._fmt(name, labels, v))
            for (name, labels), (s, n) in sorted(self._sums.items()):
                header(name, "summary")
                lines.append(self._fmt(name + "_sum", labels, s))
                lines.append(self._fmt(name + "_count", labels, n))
            for (name, labels), v in sorted(self._gauges.items()):
                header(name, "gauge")
                lines.append(self._fmt(name, labels, v))
        return "\n".join(lines) + "\n"


metrics = Metrics()
metrics.describe("tgdigest_requests_total", "API requests by endpoint and status")
metrics.describe("tgdigest_request_seconds", "wall-clock seconds per request")
metrics.describe("tgdigest_llm_tokens_total", "LLM tokens by kind and endpoint")
metrics.describe("tgdigest_agent_iterations", "retrieval rounds per search")
metrics.describe("tgdigest_critic_iterations", "critic rounds per digest")
metrics.describe("tgdigest_feedback_total", "feedback signals received")
