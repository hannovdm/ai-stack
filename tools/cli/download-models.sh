#!/usr/bin/env bash
# download-models.sh
# Downloads the four vLLM model weights from HuggingFace into the expected
# local directories. Edit the HF_* variables below if the repo IDs differ.
set -euo pipefail

# ---------------------------------------------------------------------------
# HuggingFace repo IDs — update these if needed
# ---------------------------------------------------------------------------
HF_CODER_30B="Qwen/Qwen2.5-Coder-32B-Instruct"
HF_CODER_FAST="Qwen/Qwen2.5-Coder-7B-Instruct"
HF_EMBEDDING="Qwen/Qwen3-Embedding-4B"
HF_GENERAL="Qwen/Qwen3-8B"

# ---------------------------------------------------------------------------
# Local destination directories (must match docker-compose.yml volume mounts)
# ---------------------------------------------------------------------------
BASE="${HOME}/models"
DEST_CODER_30B="${BASE}/foundation/qwen3-coder-30b"
DEST_CODER_FAST="${BASE}/foundation/qwen-coder-fast"
DEST_EMBEDDING="${BASE}/embedding/qwen3-embedding"
DEST_GENERAL="${BASE}/foundation/qwen3-general"

# HuggingFace cache (shared with containers)
export HF_HOME="${HOME}/cache/huggingface"

# ---------------------------------------------------------------------------
# Optional: set your HF token if downloading gated models
# export HUGGING_FACE_HUB_TOKEN="hf_..."
# ---------------------------------------------------------------------------

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

download_model "${HF_CODER_30B}"  "${DEST_CODER_30B}"
download_model "${HF_CODER_FAST}" "${DEST_CODER_FAST}"
download_model "${HF_EMBEDDING}"  "${DEST_EMBEDDING}"
download_model "${HF_GENERAL}"    "${DEST_GENERAL}"

echo ""
echo "==> All models downloaded successfully."
echo "    You can now start the vLLM containers:"
echo "    cd ~/runtime/compose/full && docker compose up -d vllm-qwen3-coder-gpu0 vllm-qwen-coder-fast-gpu1 vllm-qwen3-embedding-gpu1 vllm-qwen3-general-gpu1"
