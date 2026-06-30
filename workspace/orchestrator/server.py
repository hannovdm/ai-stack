"""LangGraph SpecKit — self-hosted REST API server.

Exposes a LangGraph-compatible REST interface so the MCP gateway
and any other client can start and poll SpecKit workflow runs.

API surface (compatible with langgraph-sdk client):
  POST /threads                              → {"thread_id": "<uuid>"}
  GET  /threads/{thread_id}                  → thread metadata
  POST /threads/{thread_id}/runs            → {"run_id": "<uuid>"}
  GET  /threads/{thread_id}/runs/{run_id}   → run status + result
  GET  /threads/{thread_id}/state           → latest state snapshot
  GET  /healthz                             → {"status": "ok"}
  GET  /metrics                             → Prometheus text
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from speckit_graph.graph import graph

app = FastAPI(title="langgraph-speckit", version="0.1.0")

# ── In-process run store ───────────────────────────────────────────────────────
# Keyed by thread_id → {run_id → RunRecord}
_threads: dict[str, dict] = {}
_runs:    dict[str, dict[str, dict]] = defaultdict(dict)


class RunRecord:
    def __init__(self, run_id: str, thread_id: str, input_data: dict):
        self.run_id    = run_id
        self.thread_id = thread_id
        self.input     = input_data
        self.status    = "pending"   # pending | running | success | error
        self.result:  Optional[dict] = None
        self.error:   Optional[str]  = None
        self.created  = time.time()
        self.finished = 0.0


# ── Background execution ───────────────────────────────────────────────────────

async def _execute_run(record: RunRecord) -> None:
    record.status = "running"
    try:
        config = {"configurable": {"thread_id": record.thread_id}}
        result = await graph.ainvoke(record.input, config=config)
        record.result = result
        record.status = "success"
    except Exception as exc:
        record.error  = str(exc)
        record.status = "error"
    finally:
        record.finished = time.time()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/threads")
async def create_thread(body: Optional[dict] = None) -> dict:
    thread_id = str(uuid.uuid4())
    _threads[thread_id] = {"thread_id": thread_id, "created": time.time()}
    _runs[thread_id]    = {}
    return {"thread_id": thread_id}


@app.get("/threads/{thread_id}")
async def get_thread(thread_id: str) -> dict:
    if thread_id not in _threads:
        raise HTTPException(404, f"Thread {thread_id} not found")
    return _threads[thread_id]


class RunRequest(BaseModel):
    assistant_id: str = "speckit"
    input: dict[str, Any]


@app.post("/threads/{thread_id}/runs")
async def create_run(
    thread_id: str,
    req: RunRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    if thread_id not in _threads:
        raise HTTPException(404, f"Thread {thread_id} not found")
    run_id = str(uuid.uuid4())
    record = RunRecord(run_id, thread_id, req.input)
    _runs[thread_id][run_id] = record
    background_tasks.add_task(_execute_run, record)
    return {"run_id": run_id, "thread_id": thread_id, "status": "pending"}


@app.get("/threads/{thread_id}/runs/{run_id}")
async def get_run(thread_id: str, run_id: str) -> dict:
    run = _runs.get(thread_id, {}).get(run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    return {
        "run_id":    run.run_id,
        "thread_id": run.thread_id,
        "status":    run.status,
        "error":     run.error,
        "created":   run.created,
        "finished":  run.finished,
    }


@app.get("/threads/{thread_id}/state")
async def get_state(thread_id: str) -> dict:
    # Return the result of the most recent successful run for this thread
    runs_for_thread = _runs.get(thread_id, {})
    for record in sorted(
        runs_for_thread.values(), key=lambda r: r.finished, reverse=True
    ):
        if record.status == "success" and record.result:
            return {"values": record.result, "thread_id": thread_id}
    return {"values": {}, "thread_id": thread_id}


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


# ── Prometheus metrics ────────────────────────────────────────────────────────

@app.get("/metrics")
async def metrics() -> str:
    total_runs  = sum(len(v) for v in _runs.values())
    success     = sum(1 for v in _runs.values() for r in v.values() if r.status == "success")
    running     = sum(1 for v in _runs.values() for r in v.values() if r.status == "running")
    errors      = sum(1 for v in _runs.values() for r in v.values() if r.status == "error")
    lines = [
        "# HELP langgraph_runs_total Total workflow runs",
        "# TYPE langgraph_runs_total counter",
        f"langgraph_runs_total {total_runs}",
        f'langgraph_runs_by_status{{status="success"}} {success}',
        f'langgraph_runs_by_status{{status="running"}} {running}',
        f'langgraph_runs_by_status{{status="error"}} {errors}',
    ]
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
