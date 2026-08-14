"""Brave Search -> Crawl4AI -> BGE reranker retrieval service."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")
CRAWL4AI_URL = os.getenv("CRAWL4AI_URL", "http://crawl4ai:11235").rstrip("/")
CRAWL4AI_API_TOKEN = os.getenv("CRAWL4AI_API_TOKEN", "")
RERANKER_URL = os.getenv("RERANKER_URL", "http://bge-reranker:8092").rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("RETRIEVAL_TIMEOUT_SECONDS", "20"))
MAX_PAGE_CHARACTERS = int(os.getenv("MAX_PAGE_CHARACTERS", "100000"))
CHUNK_CHARACTERS = int(os.getenv("CHUNK_CHARACTERS", "2400"))
CHUNK_OVERLAP_CHARACTERS = int(os.getenv("CHUNK_OVERLAP_CHARACTERS", "300"))
MAX_RERANK_CANDIDATES = int(os.getenv("MAX_RERANK_CANDIDATES", "12"))

app = FastAPI(title="search-retrieval", version="0.1.0")


class ResearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    max_sources: int = Field(default=2, ge=1, le=5)
    max_passages: int = Field(default=3, ge=1, le=8)
    freshness: str | None = None
    country: str | None = None
    search_language: str = Field(default="en", min_length=2, max_length=10)

    @field_validator("freshness")
    @classmethod
    def validate_freshness(cls, value: str | None) -> str | None:
        if value is None or value in {"pd", "pw", "pm", "py"}:
            return value
        raise ValueError("freshness must be one of pd, pw, pm, or py")

    @field_validator("country")
    @classmethod
    def normalize_country(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 2 or not value.isalpha():
            raise ValueError("country must be a two-letter country code")
        return value.upper()


class SearchResult(BaseModel):
    title: str
    url: str
    description: str = ""
    published_at: str | None = None


class Citation(BaseModel):
    title: str
    url: str
    heading: str | None = None
    published_at: str | None = None


class Passage(BaseModel):
    title: str
    url: str
    heading: str | None = None
    text: str
    score: float
    published_at: str | None = None
    retrieved_at: str
    citation: Citation
    citation_index: int | None = None
    source_reference: str | None = None
    text_with_citation: str | None = None


class ResearchResponse(BaseModel):
    query: str
    passages: list[Passage]
    citations: list[Citation] = []
    citation_summary: list[str] = []
    source_references: list[str] = []
    sources: list[SearchResult]
    failures: list[dict[str, str]]


async def _is_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False

    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        return False

    for address in addresses:
        try:
            if not ipaddress.ip_address(address[4][0]).is_global:
                return False
        except ValueError:
            return False
    return bool(addresses)


async def _brave_search(request: ResearchRequest) -> list[SearchResult]:
    if not BRAVE_SEARCH_API_KEY:
        raise HTTPException(status_code=503, detail="BRAVE_SEARCH_API_KEY is not configured")

    params: dict[str, Any] = {
        "q": request.query.strip(),
        "count": min(max(request.max_sources, 2), 10),
        "search_lang": request.search_language,
        "safesearch": "moderate",
        "extra_snippets": "true",
    }
    if request.freshness:
        params["freshness"] = request.freshness
    if request.country:
        params["country"] = request.country

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            BRAVE_SEARCH_URL,
            params=params,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
            },
        )
        response.raise_for_status()

    results: list[SearchResult] = []
    for item in response.json().get("web", {}).get("results", []):
        url = str(item.get("url", ""))
        if not url or not await _is_public_url(url):
            continue
        results.append(
            SearchResult(
                title=str(item.get("title", "Untitled")),
                url=url,
                description=str(item.get("description", "")),
                published_at=item.get("age") or item.get("page_age"),
            )
        )
        if len(results) >= request.max_sources:
            break
    return results


def _markdown_from_result(result: dict[str, Any]) -> str:
    markdown = result.get("markdown", "")
    if isinstance(markdown, str):
        return markdown
    if isinstance(markdown, dict):
        return str(
            markdown.get("fit_markdown")
            or markdown.get("raw_markdown")
            or markdown.get("markdown_with_citations")
            or ""
        )
    return ""


async def _crawl_one_page(
    client: httpx.AsyncClient,
    source: SearchResult,
    headers: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    payload = {
        "urls": [source.url],
        "browser_config": {
            "type": "BrowserConfig",
            "params": {
                "headless": True,
                "text_mode": True,
                "light_mode": True,
                "accept_downloads": False,
            },
        },
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {
                "stream": False,
                "cache_mode": "enabled",
                "check_robots_txt": True,
                "page_timeout": 6000,
                "wait_until": "domcontentloaded",
                "exclude_all_images": True,
                "remove_forms": True,
                "excluded_tags": ["script", "style", "nav", "footer", "form"],
                "word_count_threshold": 20,
            },
        },
    }
    try:
        response = await client.post(f"{CRAWL4AI_URL}/crawl", json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        return None, {"url": source.url, "error": f"crawl failed: {exc}"}

    raw_results = body if isinstance(body, list) else body.get("results", [])
    if not raw_results:
        return None, {"url": source.url, "error": "crawler returned no results"}

    result = raw_results[0]
    url = str(result.get("url", source.url))
    if not result.get("success"):
        return None, {"url": url, "error": str(result.get("error_message", "crawl failed"))}

    markdown = _markdown_from_result(result)[:MAX_PAGE_CHARACTERS]
    if not markdown.strip():
        return None, {"url": url, "error": "crawler returned no markdown"}

    return {"source": source, "url": url, "markdown": markdown}, None


async def _crawl(sources: list[SearchResult]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not sources:
        return [], []

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        headers = {"Authorization": f"Bearer {CRAWL4AI_API_TOKEN}"} if CRAWL4AI_API_TOKEN else {}
        crawl_tasks = [_crawl_one_page(client, source, headers) for source in sources]
        crawl_results = await asyncio.gather(*crawl_tasks, return_exceptions=True)

    pages: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for result in crawl_results:
        if isinstance(result, Exception):
            failures.append({"url": "unknown", "error": str(result)})
            continue
        page, failure = result
        if failure is not None:
            failures.append(failure)
            continue
        if page is not None:
            pages.append(page)
    return pages, failures


def _clean_crawled_text(markdown: str) -> str:
    text = markdown or ""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\[[^\]]*\]\((https?:\/\/[^)]+)\)", " ", text)
    text = re.sub(r"https?:\/\/\S+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?im)^(?:Home page|Weather|South Africa|Johannesburg|Updated .*|Advertising|Need some help\?|See more current weather|Scroll right to see more|\* Updated .*|\s*\|.*\|\s*)$", "", text)
    text = re.sub(r"(?im)^(?:\*|-|\d+\.|\|).*?$", "", text)
    text = re.sub(r"\|.*?\|", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("\n ", "\n").strip()
    return text


def _chunk_page(page: dict[str, Any]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current_heading: str | None = None
    buffer = ""
    source = page["source"]
    cleaned_markdown = _clean_crawled_text(page["markdown"])

    def append_chunk(text: str) -> None:
        clean_text = text.strip()
        if clean_text:
            chunks.append(
                {
                    "title": source.title if source else page["url"],
                    "url": page["url"],
                    "heading": current_heading,
                    "text": clean_text,
                    "published_at": source.published_at if source else None,
                }
            )

    for block in cleaned_markdown.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("#"):
            current_heading = block.lstrip("# ")[:200]
        candidate = f"{buffer}\n\n{block}".strip()
        if len(candidate) <= CHUNK_CHARACTERS:
            buffer = candidate
            continue
        append_chunk(buffer)
        overlap = buffer[-CHUNK_OVERLAP_CHARACTERS:] if buffer else ""
        buffer = f"{overlap}\n\n{block}".strip()
        while len(buffer) > CHUNK_CHARACTERS:
            append_chunk(buffer[:CHUNK_CHARACTERS])
            buffer = buffer[CHUNK_CHARACTERS - CHUNK_OVERLAP_CHARACTERS :]
    append_chunk(buffer)
    return chunks


async def _rerank(query: str, candidates: list[dict[str, Any]], limit: int) -> list[Passage]:
    candidates = candidates[:MAX_RERANK_CANDIDATES]
    if not candidates:
        return []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            f"{RERANKER_URL}/rerank",
            json={"query": query, "passages": [item["text"] for item in candidates]},
        )
        response.raise_for_status()

    retrieved_at = datetime.now(timezone.utc).isoformat()
    ranked: list[Passage] = []
    for offset, item in enumerate(response.json().get("results", [])[:limit], start=1):
        index = int(item["index"])
        if index < 0 or index >= len(candidates):
            continue
        candidate = candidates[index]
        citation = Citation(
            title=candidate["title"],
            url=candidate["url"],
            heading=candidate.get("heading"),
            published_at=candidate.get("published_at"),
        )
        passage = Passage(
            title=candidate["title"],
            url=candidate["url"],
            heading=candidate.get("heading"),
            text=candidate["text"],
            score=float(item["score"]),
            published_at=candidate.get("published_at"),
            retrieved_at=retrieved_at,
            citation=citation,
            citation_index=offset,
        )
        passage.source_reference = f"[{offset}] {citation.title or citation.url or 'Source'}"
        if passage.text:
            passage.text_with_citation = f"{passage.text.rstrip()} [{offset}]"
        ranked.append(passage)
    return ranked


@app.post("/research", response_model=ResearchResponse)
async def research(request: ResearchRequest) -> ResearchResponse:
    try:
        sources = await _brave_search(request)
        pages, failures = await _crawl(sources)
        candidates = [chunk for page in pages for chunk in _chunk_page(page)]
        passages = await _rerank(request.query, candidates, request.max_passages)
        citations: list[Citation] = []
        citation_summary: list[str] = []
        source_references: list[str] = []
        seen_urls: set[str] = set()
        for passage in passages:
            if passage.citation.url in seen_urls:
                continue
            seen_urls.add(passage.citation.url)
            citations.append(passage.citation)
            citation_summary.append(f"[{len(citation_summary) + 1}] {passage.citation.title or passage.citation.url} — {passage.citation.url}")
            source_references.append(f"[{len(source_references) + 1}] {passage.citation.title or passage.citation.url} — {passage.citation.url}")

        return ResearchResponse(
            query=request.query,
            passages=passages,
            citations=citations,
            citation_summary=citation_summary,
            source_references=source_references,
            sources=sources,
            failures=failures,
        )
    except httpx.HTTPStatusError as exc:
        provider = exc.request.url.host or "upstream"
        raise HTTPException(status_code=502, detail=f"{provider} returned {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        provider = exc.request.url.host or "upstream"
        raise HTTPException(status_code=503, detail=f"{provider} is unavailable") from exc


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "brave_configured": bool(BRAVE_SEARCH_API_KEY),
        "crawl4ai_url": CRAWL4AI_URL,
        "crawl4ai_auth_configured": bool(CRAWL4AI_API_TOKEN),
        "reranker_url": RERANKER_URL,
    }