"""HTTP inference wrapper for BAAI BGE cross-encoder rerankers."""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL_PATH = os.getenv("BGE_RERANKER_MODEL", "/models/bge-reranker-v2-m3")
MAX_LENGTH = int(os.getenv("BGE_RERANKER_MAX_LENGTH", "512"))
BATCH_SIZE = int(os.getenv("BGE_RERANKER_BATCH_SIZE", "8"))
DEVICE = os.getenv("BGE_RERANKER_DEVICE", "cpu")

app = FastAPI(title="bge-reranker", version="0.1.0")
_tokenizer: Any = None
_model: Any = None
_load_lock = threading.Lock()


@app.on_event("startup")
async def startup_event() -> None:
    await asyncio.to_thread(_load_model)


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    passages: list[str] = Field(min_length=1, max_length=40)


class RankedPassage(BaseModel):
    index: int
    score: float


class RerankResponse(BaseModel):
    model: str
    results: list[RankedPassage]


def _load_model() -> tuple[Any, Any]:
    global _tokenizer, _model
    if _model is not None and _tokenizer is not None:
        return _tokenizer, _model
    with _load_lock:
        if _model is None or _tokenizer is None:
            _tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
            _model = AutoModelForSequenceClassification.from_pretrained(MODEL_PATH)
            _model.to(DEVICE)
            _model.eval()
    return _tokenizer, _model


def _score(query: str, passages: list[str]) -> list[RankedPassage]:
    tokenizer, model = _load_model()
    scores: list[float] = []
    with torch.no_grad():
        for offset in range(0, len(passages), BATCH_SIZE):
            batch = passages[offset : offset + BATCH_SIZE]
            pairs = [[query, passage] for passage in batch]
            inputs = tokenizer(
                pairs,
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
            ).to(DEVICE)
            logits = model(**inputs, return_dict=True).logits.view(-1).float()
            scores.extend(torch.sigmoid(logits).cpu().tolist())
    return sorted(
        [RankedPassage(index=index, score=score) for index, score in enumerate(scores)],
        key=lambda item: item.score,
        reverse=True,
    )


@app.post("/rerank", response_model=RerankResponse)
async def rerank(request: RerankRequest) -> RerankResponse:
    try:
        results = await asyncio.to_thread(_score, request.query, request.passages)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Reranker unavailable: {exc}") from exc
    return RerankResponse(model=MODEL_PATH, results=results)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "model": MODEL_PATH, "loaded": _model is not None, "device": DEVICE}