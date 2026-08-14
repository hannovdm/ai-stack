# Local AI Stack

A self-hosted, GPU-accelerated AI development environment running multiple LLMs via vLLM, routed through LiteLLM, with observability, orchestration, and tooling for AI-assisted software engineering.

## Architecture Overview

```mermaid
flowchart TD
    Client["VS Code / MCP Clients"]
    Comfy["ComfyUI · Web UI<br/>(image edit / generation)<br/>:8188"]

    subgraph Proxy["Routing & Observability"]
        LiteLLM["LiteLLM Proxy<br/>(routing · auth · rate limiting)<br/>:4000"]
        Langfuse["Langfuse<br/>(tracing)<br/>:3001"]
        Prometheus["Prometheus<br/>(metrics collection)<br/>:9090"]
        Grafana["Grafana<br/>(dashboards)<br/>:3000"]
        DGCMExporter["DGCM Exporter<br/>(custom metrics)<br/>:9091"]
    end

    subgraph GPU0["vLLM · GPU 0"]
        Q30["Qwen3-Coder-30B · 32K<br/>speckit.* · vscode.chat<br/>vscode.debug · azure.*"]
    end

    subgraph GPU1["vLLM + Diffusion · GPU 1"]
        Q7["Qwen2.5-Coder-7B · 8K<br/>vscode.autocomplete"]
        QEmb["Qwen3-Embedding-4B<br/>embeddings"]
        QGen["Qwen3-8B · 4K<br/>general.chat · office.assist"]
        Flux["FLUX.1-Kontext-dev · bf16<br/>(diffusion image model)"]
    end

    subgraph Orchestration["Orchestration & Policy"]
        LangGraph["LangGraph Orchestrator<br/>(speckit_graph pipeline)<br/>:8080"]
        Policy["Policy Eval Service<br/>(LLM-based review)<br/>:8090"]
        MCP["MCP Gateway<br/>(tool exposure over HTTP/MCP)<br/>:9000"]
        FoundryLocal["Foundry Local<br/>(REST API)<br/>:8100"]
    end

    subgraph Research["Local Web Research"]
        Retrieval["Search Retrieval<br/>(Brave orchestration)<br/>:8091"]
        Crawl4AI["Crawl4AI<br/>(page extraction)<br/>:11235"]
        BGEReranker["BGE Reranker v2-m3<br/>(CPU cross-encoder)<br/>:8092"]
    end

    subgraph Databases["Databases"]
        PostgreSQL["PostgreSQL<br/>(persistent data)<br/>:5432"]
        Redis["Redis<br/>(caching · sessions)<br/>:6379"]
    end

    Client --> LiteLLM
    LiteLLM -.-> Langfuse
    LiteLLM --> Q30
    LiteLLM --> Q7
    LiteLLM --> QEmb
    LiteLLM --> QGen
    Comfy --> Flux
    
    LangGraph --> LiteLLM
    LangGraph --> Policy
    Policy --> LiteLLM
    MCP --> LangGraph
    MCP --> Retrieval
    Retrieval -->|search| Brave["Brave Search API"]
    Retrieval --> Crawl4AI
    Retrieval --> BGEReranker
    Client --> MCP
    FoundryLocal --> LiteLLM
    FoundryLocal --> PostgreSQL
    FoundryLocal --> Redis
    LangGraph --> PostgreSQL
    LangGraph --> Redis
    Langfuse --> Redis
    Policy --> PostgreSQL
    Policy --> Redis
    Prometheus --> Grafana
    DGCMExporter --> Prometheus
    Prometheus -.->|scrapes| FoundryLocal
    Prometheus -.->|scrapes| LiteLLM
    Prometheus -.->|scrapes| LangGraph

    %% ---- warm theme · bold text · thick borders ----
    classDef client fill:#ffcaa8,stroke:#e07b42,color:#5a2600,stroke-width:3px,font-weight:bold;
    classDef comfy fill:#ffd98a,stroke:#e0a12d,color:#5a3a00,stroke-width:3px,font-weight:bold;
    classDef proxy fill:#ffe0c2,stroke:#e8965a,color:#5a2f10,stroke-width:3px,font-weight:bold;
    classDef gpu0 fill:#ffe7ba,stroke:#e0b055,color:#513800,stroke-width:3px,font-weight:bold;
    classDef gpu1 fill:#f7cdb0,stroke:#d98a55,color:#552d0c,stroke-width:3px,font-weight:bold;
    classDef orch fill:#ffd7c2,stroke:#e89370,color:#5a2c14,stroke-width:3px,font-weight:bold;
    classDef db fill:#f3dcae,stroke:#d6a94f,color:#4d3800,stroke-width:3px,font-weight:bold;

    class Client client;
    class Comfy comfy;
    class LiteLLM,Langfuse,Prometheus,Grafana,DGCMExporter proxy;
    class Q30 gpu0;
    class Q7,QEmb,QGen gpu1;
    class Flux comfy;
    class LangGraph,Policy,MCP,FoundryLocal,Retrieval,Crawl4AI,BGEReranker orch;
    class PostgreSQL,Redis db;

    %% subgraph container tints (warm)
    style Proxy fill:#fff3e8,stroke:#f0c39a,color:#7a3d16,stroke-width:2px;
    style GPU0 fill:#fff6e6,stroke:#eecf94,color:#6b4a00,stroke-width:2px;
    style GPU1 fill:#fdeadd,stroke:#e8b48c,color:#6b3c14,stroke-width:2px;
    style Orchestration fill:#fff0e8,stroke:#f0b79a,color:#7a3a1c,stroke-width:2px;
    style Research fill:#fff0e8,stroke:#f0b79a,color:#7a3a1c,stroke-width:2px;
    style Databases fill:#fbf1d9,stroke:#e6cd8f,color:#5c4400,stroke-width:2px;

    %% thick connector lines for visibility
    linkStyle default stroke:#c26a33,stroke-width:2.5px;
```

## Web Research Flow

The local web research pipeline runs in this sequence:

1. Brave search
2. Crawl4AI fetch/extract for each result
3. text chunking
4. BGE rerank
5. return ranked passages back to the app

This is the flow used by the search-retrieval service in [workspace/search-retrieval/server.py](workspace/search-retrieval/server.py) and the reranker in [workspace/bge-reranker/server.py](workspace/bge-reranker/server.py).

### 1) Brave Search: sources
The process starts with Brave Search, which returns candidate web pages. Each result becomes a source record with metadata such as:

- title
- URL
- description
- published date

These source items are the web pages the system is willing to investigate. They are not the final answer yet; they are the raw candidates discovered by the search engine.

### 2) Crawl4AI fetch/extract: page content
For each source, the system calls Crawl4AI to fetch and extract page text. Crawl4AI removes boilerplate and extracts readable markdown/text from the page. At this stage, the app has page content, but not yet a clean, tightly scoped retrieval unit.

### 3) Chunking: passages
The extracted page text is then split into smaller, more manageable text blocks called chunks or passages. A chunk is a section of a page that fits within a bounded size and is easier for a retrieval/reranking model to evaluate. Each chunk keeps metadata like:

- source title
- source URL
- heading (when available)
- text content
- publication time

These chunks are the actual retrieval candidates that will later be ranked for relevance.

### 4) BGE rerank: relevance scoring
The search-retrieval service sends the query together with all candidate chunks to the BGE reranker. The reranker is a cross-encoder model that scores each query/chunk pair. In other words, it answers: "How relevant is this chunk to the user query?"

The reranker then sorts by score and keeps the most relevant ones at the top. The result is a final ranked list of passages, not a flat list of source pages. The score is the relevancy estimate that decides which evidence should be surfaced first.

### 5) Return to the app
Once the passages are reranked, the result is returned to the application as a final research response with:

- the original query
- the ranked passages
- the source list that produced them
- any crawl failures or warnings

In summary:

- sources = pages discovered by search
- chunks/passages = text slices extracted from those pages
- reranking = reordering chunks by relevance to the query
- final output = the highest-quality evidence returned to the app

## Directory Structure

```
.
├── cache/                          # Runtime caches — not committed
│   ├── build/                      # Docker build layer cache
│   ├── docker/                     # Docker image cache
│   ├── huggingface/
│   │   └── hub/                    # HuggingFace model shard cache
│   │       ├── models--Qwen--Qwen2.5-Coder-32B-Instruct/
│   │       ├── models--Qwen--Qwen2.5-Coder-7B-Instruct/
│   │       ├── models--Qwen--Qwen3-8B/
│   │       └── models--Qwen--Qwen3-Embedding-4B/
│   ├── pip/
│   │   └── hf-download-venv/       # Python venv for model download scripts
│   └── poetry/                     # Poetry dependency cache
│
├── config/                         # Static service configuration — committed
│   ├── docker/
│   │   ├── buildkit.toml           # BuildKit daemon config
│   │   └── daemon.json             # Docker daemon config
│   ├── env/
│   │   ├── base.env                # Shared environment variables
│   │   ├── dev.env                 # Development profile overrides
│   │   └── gpu.env                 # GPU-specific overrides
│   ├── grafana/
│   │   ├── dashboards/             # Dashboard JSON sources
│   │   └── provisioning/           # Grafana provisioning config
│   ├── langgraph/                  # LangGraph service config
│   ├── litellm/
│   │   ├── litellm.config.yaml     # Model list, routing, per-model params
│   │   └── routing.config.yaml     # Load-balancing and fallback rules
│   ├── otel/
│   │   └── config.yaml             # OpenTelemetry collector config
│   ├── policies/                   # OPA / policy documents for policy-eval
│   └── prometheus/
│       └── prometheus.yml          # Scrape targets
│
├── data/                           # Persistent runtime data — not committed
│   ├── artifacts/
│   │   └── builds/
│   │       └── speckit/            # SpecKit pipeline output artefacts
│   ├── db/
│   │   ├── postgres/               # PostgreSQL data directory
│   │   └── sqlite/                 # SQLite databases
│   ├── grafana/                    # Grafana state (dashboards, plugins, exports)
│   │   ├── csv/ pdf/ png/          # Dashboard exports
│   │   ├── dashboards/
│   │   ├── grafana-apiserver/
│   │   ├── plugins/
│   │   └── unified-search/
│   ├── logs/                       # Per-service log files
│   │   ├── langgraph/
│   │   ├── litellm/
│   │   ├── vllm-gpu0/
│   │   ├── vllm-gpu1/
│   │   ├── vllm-qwen-coder-fast-gpu1/
│   │   ├── vllm-qwen3-coder-gpu0/
│   │   ├── vllm-qwen3-embedding-gpu1/
│   │   └── vllm-qwen3-general-gpu1/
│   ├── prometheus/                 # Prometheus TSDB blocks + WAL
│   ├── redis/                      # Redis AOF + RDB persistence
│   └── vectorstores/
│       └── chroma/                 # ChromaDB persistent store
│
├── models/                         # Model weight storage
│   ├── cache/
│   │   ├── huggingface/            # Secondary HF cache
│   │   └── vllm/                   # vLLM compiled model cache
│   ├── embedding/
│   │   ├── bge/                    # BGE embedding models
│   │   ├── e5/                     # E5 embedding models
│   │   └── qwen3-embedding/        # Qwen3-Embedding-4B
│   ├── foundation/
│   │   ├── codellama/
│   │   ├── deepseek/
│   │   ├── glm/
│   │   ├── llama/
│   │   ├── qwen-coder-fast/        # Qwen2.5-Coder-7B  (GPU1 · fast chat + FIM)
│   │   ├── qwen3-coder-30b-a3b/    # Qwen3-Coder-30B-A3B AWQ (GPU0 · primary coding)
│   │   └── qwen3-general/          # Qwen3-8B          (GPU1 · general chat)
│   └── rerankers/
│       └── bge-rerankers/          # BGE cross-encoder rerankers
│
├── runtime/                        # Container runtime definitions
│   ├── compose/
│   │   ├── full/                   # Full stack — primary compose file
│   │   ├── dev/                    # Lightweight profile (no GPU)
│   │   └── gpu/                    # GPU-only vLLM services
│   ├── gpu/
│   │   ├── gpu0/                   # GPU 0 startup / config (Qwen3-Coder-30B)
│   │   └── gpu1/                   # GPU 1 startup / config (fast models)
│   ├── services/                   # Per-service Dockerfiles / overrides
│   │   ├── foundry-local/
│   │   ├── langfuse/
│   │   ├── langgraph/
│   │   ├── litellm/
│   │   ├── otel/
│   │   └── vllm/
│   ├── vllm/                       # vLLM argument files (per model)
│   │   ├── gpu0/
│   │   ├── gpu1/
│   │   ├── qwen-coder-fast/
│   │   ├── qwen3-coder/
│   │   ├── qwen3-embedding/
│   │   └── qwen3-general/
│   └── volumes/                    # Named-volume bind-mount targets
│       ├── grafana/
│       ├── langfuse/
│       ├── postgres/
│       └── redis/
│
├── tools/                          # Utility scripts
│   ├── cli/
│   │   ├── download-models.sh      # HuggingFace model download helper
│   │   └── gen-topology-diagram.py # Auto-generate architecture diagrams
│   ├── gpu/
│   │   └── nvidia-drv-install.sh   # NVIDIA driver installation
│   └── maintenance/                # DB migrations, cleanup scripts
│
└── workspace/                      # Custom application services (source)
    ├── dashboards/                 # Grafana dashboard source JSON
    ├── experiments/                # Ad-hoc notebooks and experiments
    ├── litellm/                    # LiteLLM customisation / plugins
    ├── mcp-gateway/                # MCP server — exposes tools over HTTP
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── server.py               # FastMCP, port 9000
    ├── search-retrieval/           # Brave → Crawl4AI → BGE orchestration
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── server.py               # FastAPI, port 8091
    ├── bge-reranker/               # Local cross-encoder inference service
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── server.py               # FastAPI, port 8092
    ├── orchestrator/               # LangGraph orchestration service
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── server.py               # FastAPI wrapper, port 8080
    │   ├── config/                 # langgraph.json and service config
    │   └── speckit_graph/          # SpecKit LangGraph pipeline definition
    └── policy-eval/                # LLM-based policy evaluation service
        ├── Dockerfile
        ├── requirements.txt
        └── evaluator.py            # FastAPI, port 8090
```

## Services

| Service | Port | Purpose |
|---|---|---|
| LiteLLM proxy | 4000 | Model routing, auth, rate limiting, usage tracking |
| vLLM GPU 0 | 8000 | Qwen3-Coder-30B — speckit, vscode.chat, azure IaC |
| vLLM GPU 1 | 8001 | Qwen2.5-Coder-7B — fast chat + FIM autocomplete |
| vLLM GPU 1 | 8002 | Qwen3-Embedding-4B — semantic search / RAG |
| vLLM GPU 1 | 8003 | Qwen3-8B — general chat |
| LangGraph orchestrator | 8080 | SpecKit pipeline execution |
| Policy eval | 8090 | LLM-based policy / code review |
| Search retrieval | 8091 | Brave discovery, Crawl4AI extraction, BGE reranking |
| BGE reranker | 8092 | Local `BAAI/bge-reranker-v2-m3` cross-encoder |
| MCP gateway | 9000 | MCP tool server for VS Code / agents |
| Crawl4AI | 11235 | Authenticated browser extraction API |
| Foundry Local | 8100 | REST API for Foundry Local functionality |
| Langfuse | 3001 | LLM tracing and observability |
| Grafana | 3000 | Metrics dashboards |
| Prometheus | 9090 | Metrics collection |
| PostgreSQL | 5432 | Persistent storage (Langfuse, LiteLLM, app state) |
| Redis | 6379 | Caching, queues, rate limiting |

## LiteLLM Model Aliases

| Alias | Backend | GPU | Context | Purpose |
|---|---|---|---|---|
| `speckit.discover` | Qwen3-Coder-30B | 0 | 32K | Repo scan, architecture intake |
| `speckit.specify` | Qwen3-Coder-30B | 0 | 32K | Requirements authoring |
| `speckit.plan` | Qwen3-Coder-30B | 0 | 32K | Architecture and implementation planning |
| `speckit.tasks` | Qwen3-Coder-30B | 0 | 32K | Task breakdown |
| `speckit.implement` | Qwen3-Coder-30B | 0 | 32K | Code generation |
| `speckit.validate` | Qwen3-Coder-30B | 0 | 32K | Code review, policy validation |
| `vscode.chat` | Qwen3-Coder-30B | 0 | 32K | VS Code Copilot chat (high quality) |
| `vscode.debug` | Qwen3-Coder-30B | 0 | 32K | Debugging, stack trace analysis |
| `vscode.autocomplete` | Qwen2.5-Coder-7B | 1 | 8K | Fast inline FIM completions |
| `azure.iac` | Qwen3-Coder-30B | 0 | 32K | Bicep / Terraform / AVM |
| `azure.deploy.review` | Qwen3-Coder-30B | 0 | 32K | Deployment readiness review |
| `general.chat` | Qwen3-8B | 1 | 4K | General assistant, summarisation, approved web research |
| `office.assist` | Qwen3-8B | 1 | 4K | O365 helper workflows |
| `embeddings` | Qwen3-Embedding-4B | 1 | — | RAG, repo indexing |

## Quick Start

```bash
# Export variables such as BRAVE_SEARCH_API_KEY from the local env file
set -a; source ~/env/base.env; set +a

# Start the full stack
docker compose -f ~/runtime/compose/full/docker-compose.yml up -d

# Restart a single service (e.g. after config change)
docker compose -f ~/runtime/compose/full/docker-compose.yml restart litellm

# View logs
docker compose -f ~/runtime/compose/full/docker-compose.yml logs -f litellm

# Download models
bash ~/tools/cli/download-models.sh
```

## VS Code Integration

VS Code reads model configuration from the **Windows** settings file:
`C:\Users\hanno\AppData\Roaming\Code\User\chatLanguageModels.json`

LiteLLM requires an API key (the master key enables the admin UI). Add it once in
VS Code via **Command Palette → "GitHub Copilot: Manage Models"** → the
"LiteLLM — Local AI Stack" endpoint, and paste the value of `LITELLM_MASTER_KEY`
(set in the `litellm` service in the compose file).

MCP gateway (`settings.json`):
```json
"mcp": {
  "servers": {
        "local-ai-speckit": {
            "type": "http",
            "url": "http://localhost:9000/mcp",
            "headers": { "Authorization": "Bearer <LITELLM_API_KEY>" }
        }
  }
}
```

The local chat's master-key field authenticates both LiteLLM and the MCP gateway;
the key is not embedded in the HTML or persisted by the page. The MCP gateway exposes
`web_research`, which accepts a query and optional source,
passage, freshness, country, and language limits. It returns reranked passages with
source URLs and crawl timestamps. The local chat uses this tool for current web
information and treats crawled page text as untrusted evidence. The local chat attaches
`web_research` only to `general.chat`; all other model requests omit external tool
definitions. The legacy `web_search` and `current_weather` MCP tools and routes have been
removed.

The gateway is published only on `127.0.0.1:9000`, requires bearer authentication,
and permits browser requests only from the local file origin or configured localhost
origins. Every external search first returns a five-minute, one-time approval challenge
bound to the exact query. The local chat displays that query for explicit approval;
models cannot approve challenges through MCP. Queries containing credential patterns,
private keys, or local filesystem paths are rejected before any external request.
