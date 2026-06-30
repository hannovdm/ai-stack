"""MCP gateway — exposes the LangGraph SpecKit workflow as MCP tools.

VS Code GitHub Copilot Chat connects to this server via HTTP/SSE and
can invoke each SpecKit step directly from the chat panel.

Configure in VS Code settings.json:
  "mcp": {
    "servers": {
      "local-ai-speckit": {
        "type": "http",
        "url": "http://localhost:9000"
      }
    }
  }
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Optional

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP

LANGGRAPH_URL  = os.getenv("LANGGRAPH_URL",  "http://langgraph:8080")
ARTIFACTS_ROOT = os.getenv("ARTIFACTS_ROOT", "/data/artifacts/speckit")

mcp = FastMCP("local-ai-speckit", stateless_http=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

async def _lg_post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(f"{LANGGRAPH_URL}{path}", json=body)
        r.raise_for_status()
        return r.json()


async def _lg_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{LANGGRAPH_URL}{path}")
        r.raise_for_status()
        return r.json()


async def _run_graph_and_wait(
    graph_id: str,
    input_data: dict,
    timeout: int = 600,
) -> dict:
    """Create a thread + run; poll until terminal state; return final state."""
    thread = await _lg_post("/threads", {})
    thread_id = thread["thread_id"]

    run = await _lg_post(
        f"/threads/{thread_id}/runs",
        {"assistant_id": graph_id, "input": input_data},
    )
    run_id = run["run_id"]

    # Poll
    import asyncio
    elapsed = 0
    while elapsed < timeout:
        await asyncio.sleep(3)
        elapsed += 3
        status = await _lg_get(f"/threads/{thread_id}/runs/{run_id}")
        if status.get("status") in ("success", "error", "timeout"):
            break

    state = await _lg_get(f"/threads/{thread_id}/state")
    return state.get("values", {})


def _feature_dir(feature_name: str) -> str:
    slug = feature_name.lower().replace(" ", "-")[:50]
    return f"{ARTIFACTS_ROOT}/{slug}"


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def speckit_run_full(
    feature_request: str,
    repo_path: str = "",
) -> str:
    """
    Run the complete SpecKit workflow (discover → specify → plan → tasks →
    implement → validate) for a feature request.

    Returns a summary with links to the generated artefacts.
    """
    feature_dir = _feature_dir(feature_request[:40])
    final_state = await _run_graph_and_wait(
        "speckit",
        {
            "feature_request": feature_request,
            "repo_path":       repo_path,
            "feature_dir":     feature_dir,
            "messages":        [],
        },
    )
    step  = final_state.get("current_step", "unknown")
    error = final_state.get("error")
    if error:
        return f"Workflow failed at step '{step}': {error}"

    val = final_state.get("validation_result", "")
    lines = [
        f"## SpecKit Workflow Complete",
        f"**Feature**: {feature_request}",
        f"**Artefacts**: {feature_dir}/",
        "",
        "### Files generated",
        f"- `{feature_dir}/discovery.md`",
        f"- `{feature_dir}/spec.md`",
        f"- `{feature_dir}/plan.md`",
        f"- `{feature_dir}/tasks.md`",
        f"- `{feature_dir}/implementation.md`",
        f"- `{feature_dir}/validation.md`",
        "",
        "### Validation summary",
        val[:1000] if val else "(no validation output)",
    ]
    return "\n".join(lines)


@mcp.tool()
async def speckit_specify(
    feature_request: str,
    repo_path: str = "",
    constitution: str = "",
) -> str:
    """
    Run only the **specify** step: produce a structured spec.md for a feature
    request. Useful when you want a spec review before proceeding to planning.
    """
    feature_dir = _feature_dir(feature_request[:40])
    final_state = await _run_graph_and_wait(
        "speckit",
        {
            "feature_request": feature_request,
            "repo_path":       repo_path,
            "feature_dir":     feature_dir,
            "constitution":    constitution,
            "current_step":    "specify",
            "messages":        [],
        },
    )
    return final_state.get("spec", "(no spec generated)")


@mcp.tool()
async def speckit_plan(
    feature_request: str,
    spec: str,
    repo_path: str = "",
    constitution: str = "",
) -> str:
    """
    Run only the **plan** step: produce a technical implementation plan
    from an existing spec. Provide the spec content directly.
    """
    feature_dir = _feature_dir(feature_request[:40])
    final_state = await _run_graph_and_wait(
        "speckit",
        {
            "feature_request": feature_request,
            "repo_path":       repo_path,
            "feature_dir":     feature_dir,
            "spec":            spec,
            "constitution":    constitution,
            "current_step":    "plan",
            "messages":        [],
        },
    )
    return final_state.get("plan", "(no plan generated)")


@mcp.tool()
async def speckit_tasks(
    feature_request: str,
    spec: str,
    plan: str,
    constitution: str = "",
) -> str:
    """
    Run only the **tasks** step: break an implementation plan into an ordered,
    atomic task checklist.
    """
    feature_dir = _feature_dir(feature_request[:40])
    final_state = await _run_graph_and_wait(
        "speckit",
        {
            "feature_request": feature_request,
            "feature_dir":     feature_dir,
            "spec":            spec,
            "plan":            plan,
            "constitution":    constitution,
            "current_step":    "tasks",
            "messages":        [],
        },
    )
    return final_state.get("tasks_raw", "(no tasks generated)")


@mcp.tool()
async def speckit_status(feature_dir: str) -> str:
    """
    Check the status of a SpecKit workflow by inspecting its artefact directory.
    Returns which steps are complete and a brief summary of each.
    """
    from pathlib import Path

    steps = ["discovery", "spec", "plan", "tasks", "implementation", "validation"]
    lines = [f"## SpecKit Status: `{feature_dir}`", ""]
    for step in steps:
        p = Path(feature_dir) / f"{step}.md"
        if p.exists():
            preview = p.read_text(encoding="utf-8")[:120].replace("\n", " ")
            lines.append(f"- [x] **{step}.md** — {preview}…")
        else:
            lines.append(f"- [ ] **{step}.md** — not yet generated")
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        mcp.streamable_http_app(),
        host="0.0.0.0",
        port=9000,
        log_level="info",
    )
