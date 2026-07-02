"""SpecKit LangGraph node implementations.

Each node maps to one SpecKit pipeline step and calls the matching
LiteLLM model alias (speckit.discover, speckit.specify, …).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .state import SpecKitState

# ── LLM factory ──────────────────────────────────────────────────────────────

_BASE = os.getenv("LITELLM_BASE_URL", "http://litellm:4000") + "/v1"
_KEY  = os.getenv("LITELLM_API_KEY")


def _llm(model: str, **kw) -> ChatOpenAI:
    return ChatOpenAI(base_url=_BASE, api_key=_KEY, model=model, **kw)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write(path: str | Path, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _read(path: str | Path, default: str = "") -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else default


def _parse_tasks(tasks_md: str) -> list[str]:
    """Return ordered task IDs from a checklist markdown."""
    return re.findall(r"-\s*\[[ x]\]\s*(\[?T\d+\]?)", tasks_md)


# ── Node 1: discover ─────────────────────────────────────────────────────────

async def discover_node(state: SpecKitState) -> dict:
    """
    Read project context (constitution + repo structure) and produce a
    concise repo summary that subsequent steps can use as grounding context.
    """
    repo_path = state.get("repo_path", "")
    feature_dir = state["feature_dir"]

    # Try to load project constitution
    constitution = _read(
        Path(repo_path) / ".specify" / "memory" / "constitution.md"
    ) if repo_path else ""

    # Gather a lightweight repo snapshot (top-level dirs + key config files)
    repo_context = ""
    if repo_path and Path(repo_path).is_dir():
        lines: list[str] = []
        for entry in sorted(Path(repo_path).iterdir()):
            if entry.name.startswith(".") and entry.name not in (".specify",):
                continue
            lines.append(f"{'DIR ' if entry.is_dir() else 'FILE'} {entry.name}")
        repo_context = "\n".join(lines[:60])

    llm = _llm("speckit.discover", temperature=0.2, max_tokens=2048)
    response = await llm.ainvoke([
        SystemMessage(content=(
            "You are a senior software architect performing an initial discovery "
            "for a new feature request. Summarise the repository's technology "
            "stack, key architectural patterns, and any relevant constraints "
            "that will affect the implementation. Be concise (≤400 words)."
        )),
        HumanMessage(content=(
            f"Feature request:\n{state['feature_request']}\n\n"
            f"Repository structure:\n{repo_context or '(no repo path provided)'}\n\n"
            f"Project constitution:\n{constitution or '(none found)'}"
        )),
    ])

    summary = response.content
    _write(Path(feature_dir) / "discovery.md", summary)

    return {
        "constitution": constitution,
        "repo_summary": summary,
        "current_step": "specify",
    }


# ── Node 2: specify ──────────────────────────────────────────────────────────

async def specify_node(state: SpecKitState) -> dict:
    """Write spec.md — requirements, user scenarios, success criteria."""
    llm = _llm("speckit.specify", temperature=0.15, max_tokens=8192)
    response = await llm.ainvoke([
        SystemMessage(content=(
            "You are a requirements engineer writing a feature specification.\n"
            "Produce a spec.md with these sections:\n"
            "1. Overview — one-paragraph summary\n"
            "2. User Scenarios & Testing — Given/When/Then acceptance scenarios "
            "   ordered by priority (P1 highest). Each scenario must be "
            "   independently testable.\n"
            "3. Functional Requirements — labelled FR-001, FR-002 …\n"
            "4. Key Entities — data entities involved (if any)\n"
            "5. Success Criteria — measurable SC-001, SC-002 …\n"
            "6. Assumptions — explicit defaults for under-specified details\n\n"
            "Every requirement MUST be testable. Use markdown."
        )),
        HumanMessage(content=(
            f"Feature request:\n{state['feature_request']}\n\n"
            f"Discovery / repo context:\n{state.get('repo_summary', '')}\n\n"
            f"Project constitution:\n{state.get('constitution', '')}"
        )),
    ])

    spec = response.content
    _write(Path(state["feature_dir"]) / "spec.md", spec)

    return {"spec": spec, "current_step": "plan"}


# ── Node 3: plan ─────────────────────────────────────────────────────────────

async def plan_node(state: SpecKitState) -> dict:
    """Write plan.md — architecture decisions, file layout, design choices."""
    llm = _llm("speckit.plan", temperature=0.2, max_tokens=8192)
    response = await llm.ainvoke([
        SystemMessage(content=(
            "You are a senior engineer writing an implementation plan.\n"
            "Produce a plan.md with:\n"
            "1. Technical Context — stack, dependencies, constraints\n"
            "2. Design Decisions — key choices with rationale (ADR-style)\n"
            "3. Architecture — component diagram (ASCII), data flow\n"
            "4. File / Module Layout — files to create or modify with purpose\n"
            "5. Testing Strategy — unit, integration, e2e approach\n"
            "6. Open Questions — anything needing clarification before coding\n\n"
            "Be specific. Reference file paths relative to the repo root."
        )),
        HumanMessage(content=(
            f"Feature request:\n{state['feature_request']}\n\n"
            f"Specification:\n{state['spec']}\n\n"
            f"Repository context:\n{state.get('repo_summary', '')}\n\n"
            f"Project constitution:\n{state.get('constitution', '')}"
        )),
    ])

    plan = response.content
    _write(Path(state["feature_dir"]) / "plan.md", plan)

    return {"plan": plan, "current_step": "tasks"}


# ── Node 4: tasks ────────────────────────────────────────────────────────────

async def tasks_node(state: SpecKitState) -> dict:
    """Write tasks.md — ordered checklist of atomic implementation tasks."""
    llm = _llm("speckit.tasks", temperature=0.15, max_tokens=8192)
    response = await llm.ainvoke([
        SystemMessage(content=(
            "You are a tech lead breaking work into implementation tasks.\n"
            "Produce tasks.md as a dependency-ordered checklist:\n"
            "  - [ ] [T001] Description — <file-path>\n"
            "  - [ ] [T002] Description — <file-path>\n\n"
            "Rules:\n"
            "• Each task is atomic (< 2 hours work), targets ONE file.\n"
            "• Tasks are grouped by phase: Setup → Foundation → "
            "  User Stories (P1 first) → Polish.\n"
            "• Every acceptance scenario in the spec maps to ≥1 task.\n"
            "• Test tasks immediately follow the code task they test."
        )),
        HumanMessage(content=(
            f"Specification:\n{state['spec']}\n\n"
            f"Implementation Plan:\n{state['plan']}\n\n"
            f"Project constitution:\n{state.get('constitution', '')}"
        )),
    ])

    tasks_raw = response.content
    tasks = _parse_tasks(tasks_raw)
    _write(Path(state["feature_dir"]) / "tasks.md", tasks_raw)

    return {"tasks_raw": tasks_raw, "tasks": tasks, "current_step": "implement"}


# ── Node 5: implement ────────────────────────────────────────────────────────

async def implement_node(state: SpecKitState) -> dict:
    """
    Execute the task list.  Generates implementation code/content for each
    uncompleted task and appends a summary to tasks.md (marking done).
    In an agentic setup this would call tool functions; here we produce the
    code as LLM output for a coding agent to apply.
    """
    llm = _llm("speckit.implement", temperature=0.1, max_tokens=12000)
    response = await llm.ainvoke([
        SystemMessage(content=(
            "You are an expert software engineer implementing a feature.\n"
            "Given the specification, plan, and task list, produce the complete "
            "implementation as a series of file blocks:\n\n"
            "```path/to/file.ext\n"
            "<full file content>\n"
            "```\n\n"
            "Rules:\n"
            "• Output EVERY file needed — new files AND modified files in full.\n"
            "• Mark completed tasks with [x] in your response preamble.\n"
            "• Include test files immediately after the code they test.\n"
            "• Follow the project constitution coding standards exactly."
        )),
        HumanMessage(content=(
            f"Feature request:\n{state['feature_request']}\n\n"
            f"Specification:\n{state['spec']}\n\n"
            f"Plan:\n{state['plan']}\n\n"
            f"Tasks:\n{state['tasks_raw']}\n\n"
            f"Project constitution:\n{state.get('constitution', '')}"
        )),
    ])

    implementation = response.content

    # Extract completed task IDs from model output
    completed = re.findall(r"-\s*\[x\]\s*(\[?T\d+\]?)", implementation, re.I)

    # Write implementation output next to the spec artefacts
    _write(Path(state["feature_dir"]) / "implementation.md", implementation)

    # Apply file blocks to the repo if a repo_path was given
    repo_path = state.get("repo_path", "")
    if repo_path:
        _apply_file_blocks(implementation, Path(repo_path))

    return {
        "completed_tasks": completed,
        "current_step": "validate",
    }


def _apply_file_blocks(text: str, repo_root: Path) -> None:
    """Write fenced code blocks with a path annotation to the repo."""
    pattern = re.compile(r"```(\S+)\n(.*?)```", re.DOTALL)
    for m in pattern.finditer(text):
        rel_path = m.group(1).strip()
        content  = m.group(2)
        # Skip language-only labels (python, typescript, …) with no /
        if "/" not in rel_path and "." not in rel_path:
            continue
        target = repo_root / rel_path
        _write(target, content)


# ── Node 6: validate ─────────────────────────────────────────────────────────

async def validate_node(state: SpecKitState) -> dict:
    """
    Review the implementation against the spec and the project constitution.
    Calls the policy-eval service if available, then the speckit.validate model.
    """
    import httpx

    # Optional: call policy-eval service
    policy_report = ""
    policy_url = os.getenv("POLICY_EVAL_URL", "http://policy-eval:8090/evaluate")
    feature_dir = Path(state["feature_dir"])
    impl_text = _read(feature_dir / "implementation.md")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(policy_url, json={
                "implementation": impl_text,
                "spec":  state.get("spec", ""),
                "constitution": state.get("constitution", ""),
            })
            if resp.status_code == 200:
                policy_report = resp.json().get("report", "")
    except Exception:
        pass  # policy-eval is optional

    llm = _llm("speckit.validate", temperature=0.05, max_tokens=8192)
    response = await llm.ainvoke([
        SystemMessage(content=(
            "You are a senior code reviewer validating a feature implementation.\n"
            "Check:\n"
            "1. Every acceptance scenario in the spec has a passing test.\n"
            "2. All functional requirements (FR-xxx) are implemented.\n"
            "3. All success criteria (SC-xxx) are achievable.\n"
            "4. Code follows the project constitution.\n"
            "5. No security issues (OWASP Top 10).\n\n"
            "Output a structured review:\n"
            "## PASS / FAIL\n"
            "## Coverage (per FR and SC)\n"
            "## Issues Found\n"
            "## Required Changes (if any)\n"
        )),
        HumanMessage(content=(
            f"Specification:\n{state['spec']}\n\n"
            f"Implementation:\n{impl_text}\n\n"
            f"Policy evaluation:\n{policy_report or '(not available)'}\n\n"
            f"Project constitution:\n{state.get('constitution', '')}"
        )),
    ])

    validation = response.content
    _write(feature_dir / "validation.md", validation)

    return {"validation_result": validation, "current_step": "done"}
