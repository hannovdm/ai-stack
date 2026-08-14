"""Foundry Local — self-hosted REST API server.

Exposes a REST interface for Foundry Local functionality.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
import uvicorn

# Ports: REST API on 8100, browser UI on 30000
API_PORT = int(os.getenv("API_PORT", "8100"))
UI_PORT = int(os.getenv("UI_PORT", "30000"))

# Initialize Prometheus metrics
foundry_local_requests_total = Counter('foundry_local_requests_total', 'Total requests to foundry-local', ['deployment', 'status'])
foundry_local_request_latency_seconds = Histogram('foundry_local_request_latency_seconds', 'Request latency in seconds', ['deployment'])
foundry_local_tokens_total = Counter('foundry_local_tokens_total', 'Total tokens processed', ['deployment', 'type'])

# Application state
app = FastAPI(title="foundry-local", version="0.1.0")

# ── Health check endpoint ──────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Health check endpoint for Foundry Local service."""
    return {"status": "ok", "service": "foundry-local"}

# ── Metrics endpoint ───────────────────────────────────────────────────────────

@app.get("/metrics")
async def metrics():
    """Expose Prometheus metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ── Main application endpoints ─────────────────────────────────────────────────

@app.get("/")
async def root():
    """Root endpoint for Foundry Local service."""
    # Record a request
    deployment = os.getenv("DEPLOYMENT", "unknown")
    foundry_local_requests_total.labels(deployment=deployment, status="success").inc()
    
    # Record latency
    start_time = time.time()
    # Simulate some work
    time.sleep(0.01)
    latency = time.time() - start_time
    foundry_local_request_latency_seconds.labels(deployment=deployment).observe(latency)
    
    return {"message": "Foundry Local Service", "status": "running"}

# ── Configuration from environment variables ──────────────────────────────────

MODEL_ROOT = os.getenv("MODEL_ROOT", "/models")
CACHE_DIR = os.getenv("CACHE_DIR", "/cache")
CONFIG_DIR = os.getenv("CONFIG_DIR", "/app/config")


async def _serve() -> None:
    """Serve the same app on the REST API port and the UI port concurrently."""
    servers = [
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port))
        for port in {API_PORT, UI_PORT}
    ]
    await asyncio.gather(*(server.serve() for server in servers))


# Start the server
if __name__ == "__main__":
    asyncio.run(_serve())