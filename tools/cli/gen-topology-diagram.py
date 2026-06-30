#!/usr/bin/env python3
"""
Generate a Mermaid flowchart of the LiteLLM → vLLM model topology.

Reads:
  ~/config/litellm/litellm.config.yaml   — model aliases and routing
  ~/runtime/compose/full/docker-compose.yml — vLLM container details

Usage:
  python3 tools/cli/gen-topology-diagram.py              # print to stdout
  python3 tools/cli/gen-topology-diagram.py -o out.md   # write markdown file
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

HOME = Path.home()
LITELLM_CONFIG = HOME / "config/litellm/litellm.config.yaml"
COMPOSE_FILE = HOME / "runtime/compose/full/docker-compose.yml"


def node_id(s: str) -> str:
    """Convert an arbitrary string to a valid Mermaid node identifier."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


def parse_vllm_services(compose_path: Path) -> dict:
    """
    Parse docker-compose.yml for vLLM service details.

    Returns a dict keyed by container_name:
      {
        "model_path": str,
        "served_name": str,
        "port": str,
        "quantization": str,
        "load_format": str,
        "max_model_len": str,
        "gpu": str,          # e.g. "GPU 0"
      }
    """
    with open(compose_path) as f:
        compose = yaml.safe_load(f)

    services = {}
    for svc_name, svc in compose.get("services", {}).items():
        if not svc_name.startswith("vllm"):
            continue

        container = svc.get("container_name", svc_name)
        cmd = svc.get("command", [])

        info = {
            "model_path": None,
            "served_name": None,
            "port": None,
            "quantization": "auto",
            "load_format": None,
            "max_model_len": None,
            "gpu": None,
        }

        i = 0
        while i < len(cmd):
            tok = str(cmd[i])
            nxt = str(cmd[i + 1]) if i + 1 < len(cmd) else ""
            if tok == "--model":
                info["model_path"] = nxt; i += 2
            elif tok == "--served-model-name":
                info["served_name"] = nxt; i += 2
            elif tok == "--port":
                info["port"] = nxt; i += 2
            elif tok == "--quantization":
                info["quantization"] = nxt; i += 2
            elif tok == "--load-format":
                info["load_format"] = nxt; i += 2
            elif tok == "--max-model-len":
                info["max_model_len"] = nxt; i += 2
            else:
                i += 1

        # bitsandbytes load-format implies INT4 NF4
        if info["load_format"] == "bitsandbytes":
            info["quantization"] = "bitsandbytes (INT4 NF4)"

        # GPU index from deploy.resources.reservations.devices
        try:
            devices = (
                svc["deploy"]["resources"]["reservations"]["devices"]
            )
            ids = devices[0].get("device_ids", [])
            if ids:
                info["gpu"] = f"GPU {ids[0]}"
        except (KeyError, IndexError, TypeError):
            pass

        services[container] = info

    return services


def parse_litellm_models(config_path: Path) -> list:
    """
    Parse litellm.config.yaml model_list.

    NOTE: The config uses a flat-indentation style where litellm_params
    keys (model, api_base, …) and model_info keys (mode, gpu, purpose)
    are siblings of model_name rather than nested.  yaml.safe_load
    therefore returns them all at the top level of each list entry.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)
    return config.get("model_list", [])


def container_from_url(api_base: str) -> str | None:
    """Extract the hostname (= container name) from an api_base URL."""
    m = re.match(r"https?://([^:/]+)", api_base or "")
    return m.group(1) if m else None


def generate_diagram(models: list, vllm_services: dict) -> str:
    lines: list[str] = ["flowchart LR"]
    lines.append("")

    # ── LiteLLM proxy node ───────────────────────────────────────────────
    lines.append('    LITELLM(["LiteLLM Proxy\\n:4000"])')
    lines.append("")

    # ── Group models by their api_base ───────────────────────────────────
    by_api_base: dict[str, list] = defaultdict(list)
    for entry in models:
        api_base = (entry.get("litellm_params") or {}).get("api_base", "unknown")
        by_api_base[api_base].append(entry)

    # ── Define model-alias nodes ─────────────────────────────────────────
    lines.append("    %% ── LiteLLM model aliases ──────────────────────")
    for entry in models:
        name = entry.get("model_name", "?")
        info = entry.get("model_info") or {}
        mode = info.get("mode", "chat")
        purpose = info.get("purpose", "")
        if purpose and len(purpose) > 45:
            purpose = purpose[:42] + "…"
        label = f"{name}\\n{mode}"
        if purpose:
            label += f"\\n{purpose}"
        lines.append(f'    {node_id("M_" + name)}["{label}"]')

    lines.append("")

    # ── Define vLLM container nodes ──────────────────────────────────────
    lines.append("    %% ── vLLM containers ────────────────────────────")
    seen_vllm: set[str] = set()
    for api_base in by_api_base:
        container = container_from_url(api_base) or api_base
        vid = node_id("V_" + container)
        if vid in seen_vllm:
            continue
        seen_vllm.add(vid)

        svc = vllm_services.get(container, {})
        port = svc.get("port") or re.search(r":(\d+)", api_base or "").group(1) if re.search(r":(\d+)", api_base or "") else "?"
        gpu = svc.get("gpu") or "?"
        quant = svc.get("quantization") or "auto"
        ctx = svc.get("max_model_len") or "?"

        label = f"{container}\\n:{port} · {gpu}\\nquant: {quant} · ctx: {ctx}"
        lines.append(f'    {vid}["{label}"]')

    lines.append("")

    # ── Define hosted-model nodes ─────────────────────────────────────────
    lines.append("    %% ── Hosted models (physical weights) ───────────")
    seen_model: set[str] = set()
    for api_base in by_api_base:
        container = container_from_url(api_base) or api_base
        mid = node_id("HM_" + container)
        if mid in seen_model:
            continue
        seen_model.add(mid)

        svc = vllm_services.get(container, {})
        served = svc.get("served_name") or "?"
        path = svc.get("model_path") or "?"
        dir_name = Path(path).name if path != "?" else path

        lines.append(f'    {mid}(["{served}\\n{dir_name}"])')

    lines.append("")

    # ── Edges: LiteLLM → model aliases ──────────────────────────────────
    lines.append("    %% ── LiteLLM → aliases ──────────────────────────")
    for entry in models:
        name = entry.get("model_name", "?")
        lines.append(f"    LITELLM --> {node_id('M_' + name)}")

    lines.append("")

    # ── Edges: model aliases → vLLM containers ───────────────────────────
    lines.append("    %% ── aliases → vLLM containers ──────────────────")
    for api_base, entries in by_api_base.items():
        container = container_from_url(api_base) or api_base
        vid = node_id("V_" + container)
        for entry in entries:
            name = entry.get("model_name", "?")
            lines.append(f"    {node_id('M_' + name)} --> {vid}")

    lines.append("")

    # ── Edges: vLLM containers → hosted models ───────────────────────────
    lines.append("    %% ── vLLM containers → hosted models ────────────")
    seen_edge: set[str] = set()
    for api_base in by_api_base:
        container = container_from_url(api_base) or api_base
        vid = node_id("V_" + container)
        mid = node_id("HM_" + container)
        if vid not in seen_edge:
            seen_edge.add(vid)
            lines.append(f"    {vid} --> {mid}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Mermaid topology diagram from LiteLLM + vLLM configs"
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="Write output to FILE instead of stdout"
    )
    parser.add_argument(
        "--wrap-md", action="store_true",
        help="Wrap the diagram in a ```mermaid code block (implies markdown output)"
    )
    args = parser.parse_args()

    models = parse_litellm_models(LITELLM_CONFIG)
    vllm_services = parse_vllm_services(COMPOSE_FILE)
    diagram = generate_diagram(models, vllm_services)

    output = f"```mermaid\n{diagram}\n```\n" if args.wrap_md else diagram + "\n"

    if args.output:
        Path(args.output).write_text(output)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
