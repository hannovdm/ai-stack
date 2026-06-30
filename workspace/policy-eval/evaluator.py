"""Policy evaluation service.

Accepts code, IaC, or implementation artefacts and evaluates them against
the organisation's policy rules. Returns a structured compliance report.

POST /evaluate
  {
    "implementation": "<full code/IaC text>",
    "spec":           "<spec.md content>",          (optional)
    "constitution":   "<constitution.md content>",  (optional)
    "model":          "speckit.validate"             (optional override)
  }

GET /healthz  →  {"status": "ok"}
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="policy-eval", version="0.1.0")

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm:4000") + "/v1"
LITELLM_API_KEY  = os.getenv("LITELLM_API_KEY",  "supersecret123")
POLICIES_DIR     = os.getenv("POLICIES_DIR",      "/config/policies")
DEFAULT_MODEL    = os.getenv("POLICY_MODEL",      "speckit.validate")

# ── Policy loader ─────────────────────────────────────────────────────────────

def _load_policies() -> str:
    """Concatenate all markdown/text files from POLICIES_DIR into one string."""
    policies_path = Path(POLICIES_DIR)
    if not policies_path.is_dir():
        return ""
    texts: list[str] = []
    for f in sorted(policies_path.glob("**/*.md")):
        texts.append(f"### {f.name}\n{f.read_text(encoding='utf-8')}")
    for f in sorted(policies_path.glob("**/*.txt")):
        texts.append(f"### {f.name}\n{f.read_text(encoding='utf-8')}")
    return "\n\n".join(texts)


# ── Request / Response models ─────────────────────────────────────────────────

class EvaluateRequest(BaseModel):
    implementation: str
    spec:           Optional[str] = ""
    constitution:   Optional[str] = ""
    model:          Optional[str] = None


class EvaluateResponse(BaseModel):
    verdict:  str    # PASS | FAIL | WARNING
    report:   str    # Full markdown evaluation report
    model:    str


# ── Endpoint ──────────────────────────────────────────────────────────────────

@app.post("/evaluate", response_model=EvaluateResponse)
async def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    policies = _load_policies()
    model    = req.model or DEFAULT_MODEL

    system_prompt = (
        "You are a policy compliance reviewer. "
        "Evaluate the provided implementation against all policy rules and "
        "the project constitution.\n\n"
        "Return your review in this exact format:\n"
        "## VERDICT: PASS|FAIL|WARNING\n"
        "## Policy Compliance\n"
        "(per-rule checklist)\n"
        "## Security (OWASP Top 10)\n"
        "(any issues)\n"
        "## Required Changes\n"
        "(numbered list, or 'None')\n"
        "## Notes\n"
        "(optional context)\n"
    )

    user_parts = []
    if policies:
        user_parts.append(f"### Organisation policies\n{policies}")
    if req.constitution:
        user_parts.append(f"### Project constitution\n{req.constitution}")
    if req.spec:
        user_parts.append(f"### Specification\n{req.spec}")
    user_parts.append(f"### Implementation\n{req.implementation}")
    user_prompt = "\n\n".join(user_parts)

    payload = {
        "model":    model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": 4096,
        "temperature": 0.05,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(
                f"{LITELLM_BASE_URL}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail=f"LiteLLM error: {exc}")
        except httpx.RequestError as exc:
            raise HTTPException(status_code=503, detail=f"LiteLLM unreachable: {exc}")

    report = resp.json()["choices"][0]["message"]["content"]

    # Extract verdict from first heading
    verdict = "WARNING"
    for line in report.splitlines():
        upper = line.upper()
        if "PASS" in upper and "VERDICT" in upper:
            verdict = "PASS"; break
        if "FAIL" in upper and "VERDICT" in upper:
            verdict = "FAIL"; break
        if "WARNING" in upper and "VERDICT" in upper:
            verdict = "WARNING"; break

    return EvaluateResponse(verdict=verdict, report=report, model=model)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
