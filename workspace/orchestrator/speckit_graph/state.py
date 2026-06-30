"""SpecKit workflow state definition."""
from __future__ import annotations

from typing import Annotated, Optional
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages


class SpecKitState(TypedDict):
    # ── Input ──────────────────────────────────────────────────────────────
    feature_request: str          # The user's feature description
    repo_path: str                # Absolute path to the target repository
    feature_dir: str              # Where spec artefacts are written
    #   e.g. /data/artifacts/speckit/<slug>

    # ── Project context (loaded at start) ─────────────────────────────────
    constitution: str             # .specify/memory/constitution.md content
    repo_summary: str             # Brief summary of repo tech stack / structure

    # ── Step outputs ──────────────────────────────────────────────────────
    spec: str                     # spec.md content
    plan: str                     # plan.md content
    tasks_raw: str                # tasks.md full content (checklist markdown)
    tasks: list[str]              # parsed task IDs in order
    completed_tasks: list[str]    # task IDs marked done
    validation_result: str        # validate / policy-eval output

    # ── Control ───────────────────────────────────────────────────────────
    current_step: str             # discover | specify | plan | tasks | implement | validate | done
    error: Optional[str]

    # ── Message history (for future multi-turn support) ───────────────────
    messages: Annotated[list, add_messages]
