"""SpecKit LangGraph workflow graph definition."""
from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from .state import SpecKitState
from .nodes import (
    discover_node,
    specify_node,
    plan_node,
    tasks_node,
    implement_node,
    validate_node,
)

# ── Router ───────────────────────────────────────────────────────────────────

def _route(state: SpecKitState) -> Literal[
    "specify", "plan", "tasks", "implement", "validate", "__end__"
]:
    if state.get("error"):
        return END
    step = state.get("current_step", "specify")
    return step if step != "done" else END


# ── Graph ────────────────────────────────────────────────────────────────────

builder = StateGraph(SpecKitState)

builder.add_node("discover",   discover_node)
builder.add_node("specify",    specify_node)
builder.add_node("plan",       plan_node)
builder.add_node("tasks",      tasks_node)
builder.add_node("implement",  implement_node)
builder.add_node("validate",   validate_node)

builder.add_edge(START,       "discover")
builder.add_conditional_edges("discover",   _route)
builder.add_conditional_edges("specify",    _route)
builder.add_conditional_edges("plan",       _route)
builder.add_conditional_edges("tasks",      _route)
builder.add_conditional_edges("implement",  _route)
builder.add_edge("validate",  END)

# Compile without checkpointer here; langgraph-cli injects the DB checkpointer
# at serve time via the connection string in the environment.
graph = builder.compile()
graph.name = "SpecKit Workflow"
