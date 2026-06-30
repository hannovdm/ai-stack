#!/usr/bin/env python3
"""Langfuse → Prometheus metrics exporter.

Polls the Langfuse REST API for new LLM generations and numeric scores,
then exposes cumulative counters and a latency histogram at /metrics so that
Prometheus can scrape them.

Exposed metrics (matching the Grafana dashboard queries):
  langfuse_total_cost          counter   – cumulative USD cost
  langfuse_observations_total  counter   – cumulative generation count  {model}
  langfuse_latency_seconds     histogram – generation latency           {model}
  langfuse_scores_numeric_avg  gauge     – latest avg numeric score      {score_name}
"""

import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

# ── Config ──────────────────────────────────────────────────────────────────
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://langfuse:3000")
PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-local-ai-stack")
SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-local-ai-stack")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
PORT = int(os.getenv("PORT", "9420"))

# Histogram bucket upper bounds (seconds)
HIST_BUCKETS = [0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, float("inf")]

# ── In-memory state ──────────────────────────────────────────────────────────
# All counters are cumulative since exporter start (Prometheus detects resets).
_state: dict = {
    "seen_obs": set(),       # observation IDs already counted
    "total_cost": 0.0,       # USD
    "model_counts": {},      # model -> int
    "model_lat_sum": {},     # model -> float (seconds)
    "model_lat_count": {},   # model -> int
    "model_lat_buckets": {}, # model -> {le: cumulative_count}
    "scores": {},            # score_name -> latest avg value
    "last_poll": 0.0,
}


# ── Langfuse API helpers ─────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None):
    try:
        r = requests.get(
            f"{LANGFUSE_HOST}/api/public/{path}",
            auth=(PUBLIC_KEY, SECRET_KEY),
            params=params or {},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"[WARN] Langfuse API /{path}: {exc}", flush=True)
        return None


def _wait_for_langfuse(max_wait: int = 120):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = requests.get(f"{LANGFUSE_HOST}/api/public/health", timeout=5)
            if r.status_code == 200:
                print("[INFO] Langfuse is ready", flush=True)
                return
        except Exception:
            pass
        print("[INFO] Waiting for Langfuse…", flush=True)
        time.sleep(10)
    print("[WARN] Langfuse health check timed out – continuing anyway", flush=True)


# ── Polling logic ────────────────────────────────────────────────────────────

def _poll():
    """Fetch new observations and scores; update cumulative state."""
    # Observations (LLM generations only)
    data = _get("observations", {"limit": 100, "type": "GENERATION"})
    if data:
        for obs in data.get("data", []):
            oid = obs.get("id")
            if not oid or oid in _state["seen_obs"]:
                continue
            _state["seen_obs"].add(oid)

            model: str = (obs.get("model") or "unknown").lower()

            # Count
            _state["model_counts"][model] = _state["model_counts"].get(model, 0) + 1

            # Cost
            cost = float(obs.get("calculatedTotalCost") or 0)
            _state["total_cost"] += cost

            # Latency: Langfuse reports it in milliseconds
            lat_ms = obs.get("latency")
            if lat_ms is not None:
                lat_s = float(lat_ms) / 1000.0
                _state["model_lat_sum"][model] = _state["model_lat_sum"].get(model, 0.0) + lat_s
                _state["model_lat_count"][model] = _state["model_lat_count"].get(model, 0) + 1
                buckets = _state["model_lat_buckets"].setdefault(
                    model, {b: 0 for b in HIST_BUCKETS}
                )
                # Increment every bucket whose upper bound >= lat_s
                for b in HIST_BUCKETS:
                    if lat_s <= b:
                        buckets[b] += 1

    # Numeric scores
    scores_data = _get("scores", {"limit": 100, "dataType": "NUMERIC"})
    if scores_data:
        agg: dict[str, list[float]] = {}
        for s in scores_data.get("data", []):
            name = s.get("name", "unknown")
            val = s.get("value")
            if val is not None:
                agg.setdefault(name, []).append(float(val))
        for name, vals in agg.items():
            _state["scores"][name] = sum(vals) / len(vals)

    _state["last_poll"] = time.time()


# ── Prometheus exposition ────────────────────────────────────────────────────

def _render() -> str:
    """Return Prometheus text exposition (format 0.0.4)."""
    _poll()
    lines: list[str] = []

    # ── langfuse_total_cost (counter) ──────────────────────────────────────
    lines += [
        "# HELP langfuse_total_cost Cumulative LLM cost in USD since exporter start",
        "# TYPE langfuse_total_cost counter",
        f"langfuse_total_cost {_state['total_cost']:.6f}",
    ]

    # ── langfuse_observations_total (counter, per model) ──────────────────
    lines += [
        "# HELP langfuse_observations_total Cumulative LLM generation count since exporter start",
        "# TYPE langfuse_observations_total counter",
    ]
    for model, count in sorted(_state["model_counts"].items()):
        lines.append(f'langfuse_observations_total{{model="{model}"}} {count}')

    # ── langfuse_latency_seconds (histogram, per model) ───────────────────
    lines += [
        "# HELP langfuse_latency_seconds LLM generation latency in seconds",
        "# TYPE langfuse_latency_seconds histogram",
    ]
    for model in sorted(_state["model_lat_buckets"]):
        buckets = _state["model_lat_buckets"][model]
        for le in HIST_BUCKETS:
            le_str = "+Inf" if le == float("inf") else str(le)
            lines.append(
                f'langfuse_latency_seconds_bucket{{model="{model}",le="{le_str}"}}'
                f' {buckets[le]}'
            )
        lines.append(
            f'langfuse_latency_seconds_sum{{model="{model}"}}'
            f' {_state["model_lat_sum"].get(model, 0.0):.3f}'
        )
        lines.append(
            f'langfuse_latency_seconds_count{{model="{model}"}}'
            f' {_state["model_lat_count"].get(model, 0)}'
        )

    # ── langfuse_scores_numeric_avg (gauge, per score name) ───────────────
    lines += [
        "# HELP langfuse_scores_numeric_avg Average numeric score value by name",
        "# TYPE langfuse_scores_numeric_avg gauge",
    ]
    for name, avg in sorted(_state["scores"].items()):
        lines.append(f'langfuse_scores_numeric_avg{{score_name="{name}"}} {avg:.4f}')

    return "\n".join(lines) + "\n"


# ── HTTP server ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/metrics":
            body = _render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # suppress per-request access logs
        pass


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[INFO] Langfuse Prometheus exporter – listening on :{PORT}", flush=True)
    print(f"[INFO] Target: {LANGFUSE_HOST}  poll interval: {POLL_INTERVAL}s", flush=True)

    _wait_for_langfuse()

    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"[INFO] Ready – scrape http://langfuse-exporter:{PORT}/metrics", flush=True)
    server.serve_forever()
