#!/usr/bin/env bash
# download-models.sh
# Downloads the four vLLM model weights from HuggingFace into the expected
# local directories. Edit the HF_* variables below if the repo IDs differ.
set -euo pipefail

# ---------------------------------------------------------------------------
# HuggingFace repo IDs — update these if needed
# ---------------------------------------------------------------------------
HF_CODER_30B="QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ"
HF_CODER_FAST="Qwen/Qwen2.5-Coder-7B-Instruct"
HF_EMBEDDING="Qwen/Qwen3-Embedding-4B"
HF_GENERAL="Qwen/Qwen3-8B"

# FLUX.1 Kontext [dev] for ComfyUI (image editing, GPU 1).
# bf16 full-quality weights + VAE only exist in the GATED Black Forest Labs
# repo: accept the licence at
#   https://huggingface.co/black-forest-labs/FLUX.1-Kontext-dev
# and export an HF token before running (see HUGGING_FACE_HUB_TOKEN below).
# The text encoders are in a separate PUBLIC repo (no token needed).
# A public fp8 ComfyUI mirror (no token) also exists if you prefer smaller,
# lower-precision weights: Comfy-Org/flux1-kontext-dev_ComfyUI ->
#   split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors
HF_FLUX_KONTEXT="black-forest-labs/FLUX.1-Kontext-dev"
HF_FLUX_TEXT_ENCODERS="comfyanonymous/flux_text_encoders"

# ---------------------------------------------------------------------------
# Local destination directories (must match docker-compose.yml volume mounts)
# ---------------------------------------------------------------------------
BASE="${HOME}/models"
# Real Qwen3-Coder-30B-A3B-Instruct (AWQ 4-bit) lives in qwen3-coder-30b-a3b.
# The old qwen3-coder-30b dir held a misnamed Qwen2.5-Coder-32B checkpoint.
DEST_CODER_30B="${BASE}/foundation/qwen3-coder-30b-a3b"
DEST_CODER_FAST="${BASE}/foundation/qwen-coder-fast"
DEST_EMBEDDING="${BASE}/embedding/qwen3-embedding"
DEST_GENERAL="${BASE}/foundation/qwen3-general"

# ComfyUI models root (bind-mounted to /app/ComfyUI/models in docker-compose).
COMFY="${BASE}/comfyui"

# HuggingFace cache (shared with containers)
export HF_HOME="${HOME}/cache/huggingface"

# ---------------------------------------------------------------------------
# HuggingFace token for gated models (e.g. FLUX.1 Kontext).
# Loaded from ~/env/base.env (HF_TOKEN=...) if present, so no need to export
# it manually. You can still override by exporting HF_TOKEN before running.
# ---------------------------------------------------------------------------
ENV_FILE="${HOME}/env/base.env"
if [[ -z "${HF_TOKEN:-}" ]] && [[ -f "${ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    HF_TOKEN="$(grep -E '^HF_TOKEN=' "${ENV_FILE}" | tail -n1 | cut -d= -f2-)"
fi
export HF_TOKEN="${HF_TOKEN:-}"
# huggingface_hub also honours the legacy name; keep them in sync.
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"

VENV_DIR="${HOME}/cache/pip/hf-download-venv"

echo "==> Ensuring huggingface_hub is available..."
if [[ ! -x "${VENV_DIR}/bin/hf" ]]; then
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --quiet "huggingface_hub[hf_xet]>=0.27"
fi
HF_CLI="${VENV_DIR}/bin/hf"
PYTHON="${VENV_DIR}/bin/python"

get_remote_revision() {
    local repo_id="$1"
    "${PYTHON}" - <<EOF
from huggingface_hub import HfApi
info = HfApi().repo_info(repo_id="${repo_id}", repo_type="model")
print(info.sha)
EOF
}

download_model() {
    local repo_id="$1"
    local dest="$2"
    local revision_file="${dest}/.model_revision"

    echo ""
    echo "==> ${repo_id}"

    local remote_rev
    remote_rev=$(get_remote_revision "${repo_id}")

    if [[ -f "${revision_file}" ]] && [[ "$(cat "${revision_file}")" == "${remote_rev}" ]]; then
        echo "    Up to date (${remote_rev:0:12}), skipping."
        return 0
    fi

    echo "    Downloading -> ${dest}"
    mkdir -p "${dest}"
    "${HF_CLI}" download \
        --repo-type model \
        --local-dir "${dest}" \
        "${repo_id}"

    echo "${remote_rev}" > "${revision_file}"
    echo "    Done (${remote_rev:0:12})"
}

# Download one or more individual files from a repo into a flat destination dir.
# Usage: download_file <repo_id> <dest_dir> <file> [file ...]
download_file() {
    local repo_id="$1"; shift
    local dest="$1"; shift

    echo ""
    echo "==> ${repo_id} (files)"
    mkdir -p "${dest}"
    local f
    for f in "$@"; do
        if [[ -f "${dest}/$(basename "${f}")" ]]; then
            echo "    Present: $(basename "${f}"), skipping."
            continue
        fi
        echo "    Downloading ${f} -> ${dest}"
        "${HF_CLI}" download \
            --repo-type model \
            --local-dir "${dest}" \
            "${repo_id}" "${f}"
    done
    echo "    Done."
}

download_model "${HF_CODER_30B}"  "${DEST_CODER_30B}"
download_model "${HF_CODER_FAST}" "${DEST_CODER_FAST}"
download_model "${HF_EMBEDDING}"  "${DEST_EMBEDDING}"
download_model "${HF_GENERAL}"    "${DEST_GENERAL}"

# ── FLUX.1 Kontext [dev] for ComfyUI ────────────────────────────────────────
# bf16 transformer + VAE from the gated BFL repo (top-level files), and the
# CLIP-L / T5-XXL text encoders from the public repo. Requires that you have
# accepted the BFL licence and exported HUGGING_FACE_HUB_TOKEN.
download_file "${HF_FLUX_KONTEXT}" "${COMFY}/diffusion_models" \
    "flux1-kontext-dev.safetensors"
download_file "${HF_FLUX_KONTEXT}" "${COMFY}/vae" \
    "ae.safetensors"
download_file "${HF_FLUX_TEXT_ENCODERS}" "${COMFY}/text_encoders" \
    "clip_l.safetensors" "t5xxl_fp16.safetensors"

echo ""
echo "==> All models downloaded successfully."
echo "    You can now start the vLLM containers:"
echo "    cd ~/runtime/compose/full && docker compose up -d vllm-qwen3-coder-gpu0 vllm-qwen-coder-fast-gpu1 vllm-qwen3-embedding-gpu1 vllm-qwen3-general-gpu1"
echo ""
echo "    Start ComfyUI + FLUX Kontext (GPU 1) with the flux profile:"
echo "    cd ~/runtime/compose/full && docker compose --profile flux up -d comfyui-flux-gpu1"
