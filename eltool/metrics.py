"""
Lightweight performance tracking for HELIX — latency + memory, toggle-able at
runtime, with aggregated insights.

Enable it BEFORE a run (UI toggle → POST /api/metrics/toggle). While enabled,
instrumented operations (retrieval, LLM re-rank, graph queries, writes) record a
timing + memory sample to sessions/<name>/metrics.jsonl. Insights aggregate those
samples: per-operation p50/p95/max latency, component time shares, peak memory,
and throughput.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

from .session import session_dir

try:  # clean cross-platform RSS; optional
    import psutil
    _PROC = psutil.Process()
except Exception:  # pragma: no cover
    _PROC = None

# Persisted so the toggle survives a uvicorn --reload restart.
_STATE_FILE = Path(__file__).resolve().parent.parent / ".helix_metrics_enabled"


def _rss_mb() -> float:
    if _PROC is not None:
        try:
            return _PROC.memory_info().rss / 1_000_000.0
        except Exception:  # pragma: no cover
            pass
    try:  # fallback: ru_maxrss (bytes on macOS, KiB on Linux)
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r / 1_000_000.0 if r > 10_000_000 else r / 1_000.0
    except Exception:  # pragma: no cover
        return 0.0


def _pctl(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


class Metrics:
    def __init__(self) -> None:
        self._enabled = _STATE_FILE.exists()

    # ── toggle ────────────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        try:
            if value:
                _STATE_FILE.write_text("1")
            elif _STATE_FILE.exists():
                _STATE_FILE.unlink()
        except OSError:
            pass

    # ── recording ───────────────────────────────────────────────────────────
    def _file(self, session: str) -> Path:
        return session_dir(session) / "metrics.jsonl"

    def record(self, op: str, ms: float, session: str, **tags) -> None:
        if not self._enabled or not session:
            return
        entry = {"t": round(time.time(), 3), "op": op, "ms": round(ms, 2),
                 "rss_mb": round(_rss_mb(), 1), **tags}
        try:
            path = self._file(session)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @contextmanager
    def timer(self, op: str, session: str, **tags):
        if not self._enabled or not session:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.record(op, (time.perf_counter() - t0) * 1000.0, session, **tags)

    # ── insights ─────────────────────────────────────────────────────────────
    def reset(self, session: str) -> None:
        try:
            f = self._file(session)
            if f.exists():
                f.unlink()
        except OSError:
            pass

    def read(self, session: str) -> list[dict]:
        f = self._file(session)
        if not f.exists():
            return []
        out = []
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def insights(self, session: str) -> dict:
        rows = self.read(session)
        if not rows:
            return {"samples": 0, "operations": [], "memory": {}, "throughput": {}}

        by_op: dict[str, list[float]] = {}
        for r in rows:
            by_op.setdefault(r.get("op", "?"), []).append(float(r.get("ms", 0)))

        total_ms = sum(sum(v) for v in by_op.values())
        ops = []
        for op, vals in sorted(by_op.items(), key=lambda kv: -sum(kv[1])):
            s = sum(vals)
            ops.append({
                "op": op,
                "count": len(vals),
                "mean_ms": round(s / len(vals), 1),
                "p50_ms": round(_pctl(vals, 0.50), 1),
                "p95_ms": round(_pctl(vals, 0.95), 1),
                "max_ms": round(max(vals), 1),
                "total_s": round(s / 1000.0, 2),
                "share": round(s / total_ms, 3) if total_ms else 0.0,
            })

        rss = [float(r.get("rss_mb", 0)) for r in rows if r.get("rss_mb")]
        ts = [float(r.get("t", 0)) for r in rows if r.get("t")]
        wall = (max(ts) - min(ts)) if len(ts) > 1 else 0.0

        # throughput: distinct mentions timed at the top-level "candidates" op
        mentions = sum(1 for r in rows if r.get("op") == "candidates")
        return {
            "samples": len(rows),
            "operations": ops,
            "memory": {
                "peak_mb": round(max(rss), 1) if rss else 0.0,
                "mean_mb": round(sum(rss) / len(rss), 1) if rss else 0.0,
                "psutil": _PROC is not None,
            },
            "throughput": {
                "total_op_time_s": round(total_ms / 1000.0, 2),
                "wall_time_s": round(wall, 1),
                "mentions": mentions,
                "mentions_per_s": round(mentions / wall, 2) if wall > 0 and mentions else 0.0,
            },
        }


    # ── graph optimisation query stats (always recorded, cheap) ──────────────
    def _graph_file(self, session: str) -> Path:
        return session_dir(session) / "graph_stats.jsonl"

    def record_graph(self, session: str, **stats) -> None:
        """Record one graph-optimise call's cost/scale. NOT gated by the toggle."""
        if not session:
            return
        entry = {"t": round(time.time(), 3), "rss_mb": round(_rss_mb(), 1), **stats}
        try:
            f = self._graph_file(session)
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def reset_graph(self, session: str) -> None:
        try:
            f = self._graph_file(session)
            if f.exists():
                f.unlink()
        except OSError:
            pass

    def graph_insights(self, session: str) -> dict:
        f = self._graph_file(session)
        rows: list[dict] = []
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if not rows:
            return {"count": 0, "docs": [], "totals": {}, "query_ms": {}, "nodes": {}}

        qms = [float(r.get("query_ms", 0)) for r in rows]
        nodes = [int(r.get("nodes", 0)) for r in rows]
        edges = [int(r.get("edges", 0)) for r in rows]
        iters = [int(r.get("iterations", 0)) for r in rows]

        def dist(v: list[float]) -> dict:
            return {"mean": round(sum(v) / len(v), 2) if v else 0.0,
                    "p50": round(_pctl(v, 0.50), 2),
                    "p95": round(_pctl(v, 0.95), 2),
                    "max": round(max(v), 2) if v else 0.0}

        docs = [{
            "doc": r.get("doc", ""),
            "nodes": int(r.get("nodes", 0)),
            "edges": int(r.get("edges", 0)),
            "query_ms": round(float(r.get("query_ms", 0)), 2),
            "iterations": int(r.get("iterations", 0)),
            "changed": int(r.get("changed", 0)),
            "mentions": int(r.get("mentions", 0)),
            "edges_before": int(r.get("edges_before", 0)),
            "edges_after": int(r.get("edges_after", 0)),
        } for r in rows]
        docs.sort(key=lambda d: -d["query_ms"])

        return {
            "count": len(rows),
            "totals": {"nodes": sum(nodes), "edges": sum(edges),
                       "query_ms": round(sum(qms), 1), "iterations": sum(iters)},
            "query_ms": dist(qms),
            "nodes": dist([float(n) for n in nodes]),
            "docs": docs,
        }


metrics = Metrics()
