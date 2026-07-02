# ComfyUI image server for FLUX.1 Kontext [dev] on GPU 1.
#
# RTX 5090 is Blackwell (compute capability sm_120), which requires a
# CUDA 12.8 PyTorch build. The stock ComfyUI images ship cu121 wheels that
# cannot launch on Blackwell, so we install torch/torchvision from the
# official cu128 wheel index explicitly.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    COMFYUI_VERSION=master

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        python3.11 \
        python3.11-venv \
        python3-pip \
        libgl1 \
        libglib2.0-0 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

WORKDIR /app

# Blackwell-compatible PyTorch (cu128).
RUN python -m pip install --upgrade pip \
    && python -m pip install \
        --index-url https://download.pytorch.org/whl/cu128 \
        torch torchvision torchaudio

# ComfyUI itself.
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git /app/ComfyUI \
    && python -m pip install -r /app/ComfyUI/requirements.txt

WORKDIR /app/ComfyUI

EXPOSE 8188

# --lowvram streams model blocks between CPU and GPU so FLUX (~24 GB in bf16)
# can share GPU 1 with the resident vLLM services via CPU offload. Remove it
# (or use --highvram) when GPU 1 is otherwise idle for maximum throughput.
CMD ["python", "main.py", \
     "--listen", "0.0.0.0", \
     "--port", "8188", \
     "--lowvram"]
