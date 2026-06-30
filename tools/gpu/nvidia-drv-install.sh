#===============================================================================
# Universal NVIDIA Driver + CUDA Installer for Ubuntu 22.04/24.04/26.04 LTS
# - Legacy GPUs: pinned to nvidia-driver-535
# - Blackwell GPUs: open kernel modules (nvidia-open)
# - Modern GPUs: latest proprietary driver via cuda-drivers meta-package
#===============================================================================
set -euo pipefail

#-------------------------------------------------------------------------------
# CONFIGURATION FLAGS
#-------------------------------------------------------------------------------
DO_OS_POLICY_CHECK=1 # Enforce policy: only Ubuntu 22.04/24.04/26.04 LTS
ALLOWED_UBUNTU_VERSIONS=("22.04" "24.04" "26.04")

DO_APT_UPGRADE=1 # apt update/upgrade
DO_INSTALL_HWE=1 # Install linux-generic-hwe-22.04 on 22.04
DO_INSTALL_KERNEL_HEADERS=1 # Install linux-headers for current kernel
DO_INSTALL_BUILD_TOOLS=1 # Install GCC/G++

DO_PURGE_OLD_PACKAGES=1  # Best effort purge old NVIDIA/CUDA packages
DO_SETUP_CUDA_REPO=1 # Install CUDA apt repo via cuda-keyring
DO_INSTALL_CUDA_STACK=1 # Install CUDA toolkit (development environment)

# Legacy GPUs: pin to nvidia-driver-535 (last branch supporting Pascal/Volta)
# Format: "10de:PCI_ID|Human Readable Name|Driver Package"
LEGACY_GPUS=(
  "10de:1b06|GeForce GTX 1080 Ti|nvidia-driver-535"
  "10de:1db1|Tesla V100|nvidia-driver-580"
  "10de:1b80|GeForce GTX 1070|nvidia-driver-535"
  "10de:1c03|GeForce GTX 1060|nvidia-driver-535"
)

# Blackwell GPUs: require open kernel modules + driver 575+
# Format: "10de:PCI_ID|Human Readable Name"
BLACKWELL_GPUS=(
  "10de:2b85|GeForce RTX 5090"
  "10de:2b84|GeForce RTX 5080"
  "10de:2b83|GeForce RTX 5070 Ti"
  "10de:2b82|GeForce RTX 5070"
  "10de:2bb4|RTX PRO 6000 Blackwell Workstation"
  "10de:2bb1|RTX PRO 6000 Blackwell Max-Q"
  "10de:2c38|RTX PRO 5000 Blackwell"
  "10de:2c37|RTX PRO 4000 Blackwell"
  "10de:2c36|RTX PRO 3000 Blackwell"
  "10de:2c35|RTX PRO 2000 Blackwell"
)

DO_BLACKLIST_NOUVEAU=1 # Create blacklist + update-initramfs (reboot needed)
DO_TRY_RMMOD_NOUVEAU=1  # Best effort rmmod nouveau (may fail if in use)
DO_USER_GROUPS=0 # Add user to video,render (optional)
DO_BASHRC_CUDA_PATHS=1 # Add CUDA PATH/LD_LIBRARY_PATH via ~/.bashrc (idempotent)
DO_VERIFY_NVIDIA_SMI=1 # Run nvidia-smi
DO_VERIFY_NVCC=1 # Run nvcc -V (only if nvcc exists)
DO_INSTALL_NVIDIA_CONTAINER_TOOLKIT=1  # Install NVIDIA Container Toolkit if docker exists
DO_CONFIGURE_DOCKER_RUNTIME=1 # Run nvidia-ctk runtime configure --runtime=docker
DO_RESTART_DOCKER=1 # Restart docker service

# ---- Start ----
echo "Starting NVIDIA driver + CUDA installation (Non-Interactive)..."

#-------------------------------------------------------------------------------
# DEPENDENCY CHECK
#-------------------------------------------------------------------------------
for cmd in lspci wget gpg curl sed awk uname; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing dependency: $cmd"; exit 1; }
done

#-------------------------------------------------------------------------------
# OS DETECTION
#-------------------------------------------------------------------------------
osr="/etc/os-release"
[[ -r "$osr" ]] || osr="/usr/lib/os-release"
[[ -r "$osr" ]] || { echo "Cannot read os-release file. Exiting."; exit 1; }

set -a
. "$osr"
set +a

[[ "${ID:-}" != "ubuntu" ]] && { echo "This script is intended for Ubuntu only. Exiting."; exit 1; }

UBUNTU_VERSION="${VERSION_ID:-}"
UBUNTU_CODENAME="${VERSION_CODENAME:-}"

if [[ -z "${UBUNTU_CODENAME}" ]]; then
  case "${UBUNTU_VERSION}" in
    "22.04") UBUNTU_CODENAME="jammy" ;;
    "24.04") UBUNTU_CODENAME="noble" ;;
    "26.04") UBUNTU_CODENAME="resolute" ;;
    *) UBUNTU_CODENAME="unknown" ;;
  esac
fi

[[ -z "${UBUNTU_VERSION}" ]] && { echo "Cannot detect Ubuntu VERSION_ID. Exiting."; exit 1; }

# Policy check
if [[ "${DO_OS_POLICY_CHECK:-0}" -eq 1 ]]; then
  ok=0
  for v in "${ALLOWED_UBUNTU_VERSIONS[@]}"; do
    [[ "${UBUNTU_VERSION}" == "${v}" ]] && { ok=1; break; }
  done
  [[ "${ok}" -ne 1 ]] && { echo "Unsupported Ubuntu version: ${UBUNTU_VERSION}"; exit 1; }
fi

RELEASE_VERSION="$(echo "${UBUNTU_VERSION}" | sed 's/\([0-9]\+\)\.\([0-9]\+\)/\1\2/')"
echo "OS: Ubuntu ${UBUNTU_VERSION} (${UBUNTU_CODENAME})"

#-------------------------------------------------------------------------------
# GPU DETECTION
#-------------------------------------------------------------------------------
NVIDIA_GPU_LINES="$(lspci -nn | grep -iE 'vga|3d' | grep -i '10de:' || true)"
[[ -z "${NVIDIA_GPU_LINES}" ]] && { echo "No NVIDIA GPU detected (vendor 10de). Exiting."; exit 1; }

echo "NVIDIA GPUs detected:"
echo "${NVIDIA_GPU_LINES}"

DETECTED_GPUS="$(lspci -nn)"
REBOOT_REQUIRED=0

#-------------------------------------------------------------------------------
# SYSTEM PREPARATION
#-------------------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

if [[ "${DO_APT_UPGRADE}" -eq 1 ]]; then
  echo "Updating system packages..."
  sudo -E apt-get update
  sudo -E apt-get upgrade -y \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold"
fi

if [[ "${DO_INSTALL_HWE}" -eq 1 ]]; then
  case "${UBUNTU_VERSION}" in
    "22.04") HWE_PKG="linux-generic-hwe-22.04" ;;
    "24.04") HWE_PKG="linux-generic-hwe-24.04" ;;
    "26.04") HWE_PKG="" ;;
    *) HWE_PKG="" ;;
  esac
  if [[ -n "${HWE_PKG}" ]]; then
    echo "Installing HWE kernel: ${HWE_PKG}"
    sudo -E apt-get install -y \
      -o Dpkg::Options::="--force-confdef" \
      -o Dpkg::Options::="--force-confold" \
      "${HWE_PKG}"
    REBOOT_REQUIRED=1
  fi
fi

if [[ "${DO_INSTALL_KERNEL_HEADERS}" -eq 1 ]]; then
  CURRENT_KERNEL="$(uname -r)"
  echo "Installing kernel headers for: ${CURRENT_KERNEL}"
  sudo -E apt-get install -y \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold" \
    "linux-headers-${CURRENT_KERNEL}" || {
    echo "Failed to install linux-headers. DKMS may fail."; exit 1; }
fi

if [[ "${DO_INSTALL_BUILD_TOOLS}" -eq 1 ]]; then
  case "${UBUNTU_VERSION}" in
    "22.04") GCC_PACKAGES=("gcc-12" "g++-12") ;;
    "24.04") GCC_PACKAGES=("gcc-13" "g++-13") ;;
    "26.04") GCC_PACKAGES=("gcc-15" "g++-15") ;;
    *) GCC_PACKAGES=("gcc" "g++") ;;
  esac
  echo "Installing build tools: ${GCC_PACKAGES[*]}"
  sudo -E apt-get install -y -o Dpkg::Options::="--force-confdef" "${GCC_PACKAGES[@]}"
fi

#-------------------------------------------------------------------------------
# PURGE OLD PACKAGES
#-------------------------------------------------------------------------------
if [[ "${DO_PURGE_OLD_PACKAGES}" -eq 1 ]]; then
  echo "Purging previous NVIDIA/CUDA installations..."
  sudo dpkg --configure -a || true
  sudo -E apt-get purge -y "nvidia-*" "libnvidia-*" "cuda-*" "nvidia-driver-*" "*cudnn*" "*nsight*" 2>/dev/null || true
  sudo -E apt-get remove --purge -y nvidia-cuda-toolkit nvidia-prime nvidia-settings 2>/dev/null || true
  sudo -E apt-get autoremove -y || true
  sudo -E apt-get --fix-broken install -y || true
  sudo -E apt-get clean -y || true
fi

#-------------------------------------------------------------------------------
# SETUP CUDA REPOSITORY
#-------------------------------------------------------------------------------
if [[ "${DO_SETUP_CUDA_REPO}" -eq 1 ]]; then
  echo "Setting up CUDA repo for ubuntu${RELEASE_VERSION}..."
  wget -q "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu${RELEASE_VERSION}/x86_64/cuda-keyring_1.1-1_all.deb"
  sudo dpkg -i cuda-keyring_1.1-1_all.deb 2>/dev/null || true
  rm -f cuda-keyring_1.1-1_all.deb
  sudo -E apt-get update
fi

#-------------------------------------------------------------------------------
# BLACKLIST NOUVEAU
#-------------------------------------------------------------------------------
if [[ "${DO_BLACKLIST_NOUVEAU}" -eq 1 ]]; then
  BL_FILE="/etc/modprobe.d/blacklist-nouveau.conf"
  if [[ ! -f "${BL_FILE}" ]] || ! grep -q "^blacklist nouveau" "${BL_FILE}" 2>/dev/null; then
    echo "Blacklisting nouveau (requires reboot)..."
    sudo tee "${BL_FILE}" >/dev/null <<'EOF'
blacklist nouveau
options nouveau modeset=0
EOF
    sudo update-initramfs -u
    REBOOT_REQUIRED=1
  fi
fi

#-------------------------------------------------------------------------------
# INSTALL DRIVER + CUDA
#-------------------------------------------------------------------------------
IS_BLACKWELL=0
IS_LEGACY=0

# 1) Check Blackwell first (requires open modules)
for gpu_spec in "${BLACKWELL_GPUS[@]}"; do
  IFS='|' read -r pci_id gpu_name <<< "$gpu_spec"
  if echo "$DETECTED_GPUS" | grep -q "$pci_id"; then
    echo "Detected Blackwell GPU: $gpu_name ($pci_id)"
    echo "Installing open kernel modules + driver 575+..."
    sudo -E apt-get install -y -o Dpkg::Options::="--force-confdef" \
      nvidia-open nvidia-driver-open nvidia-dkms-open
    IS_BLACKWELL=1
    break
  fi
done

# 2) Check Legacy GPUs (pin to 535)
if [[ "$IS_BLACKWELL" -eq 0 ]]; then
  for gpu_spec in "${LEGACY_GPUS[@]}"; do
    IFS='|' read -r pci_id gpu_name driver_pkg <<< "$gpu_spec"
    if echo "$DETECTED_GPUS" | grep -q "$pci_id"; then
      echo "Detected legacy GPU: $gpu_name ($pci_id) -> pinning to $driver_pkg"
      sudo -E apt-get install -y -o Dpkg::Options::="--force-confdef" \
        "$driver_pkg" "nvidia-dkms-${driver_pkg#nvidia-driver-}"
      IS_LEGACY=1
      break
    fi
  done
fi

# 3) Install CUDA toolkit + drivers
if [[ "${DO_INSTALL_CUDA_STACK}" -eq 1 ]]; then
  if [[ "$IS_BLACKWELL" -eq 1 ]]; then
    echo "Installing CUDA toolkit (Blackwell path)..."
    sudo -E apt-get install -y -o Dpkg::Options::="--force-confdef" cuda-toolkit
  elif [[ "$IS_LEGACY" -eq 1 ]]; then
    # 🔒 LEGACY FIX: V100/Pascal/Volta support CUDA <= 12.2 max
    # Using versioned package to prevent auto-upgrade to CUDA 13.x
    echo "Installing CUDA toolkit 12.2 (legacy path)..."
    sudo -E apt-get install -y -o Dpkg::Options::="--force-confdef" cuda-toolkit-12-2
  else
    # Modern GPUs: use cuda-drivers meta-package for latest proprietary driver
    echo "Installing CUDA toolkit + latest proprietary driver via cuda-drivers..."
    sudo -E apt-get install -y -o Dpkg::Options::="--force-confdef" cuda-toolkit cuda-drivers
  fi
fi

#-------------------------------------------------------------------------------
# POST-INSTALL STEPS
#-------------------------------------------------------------------------------
if [[ "${DO_TRY_RMMOD_NOUVEAU}" -eq 1 ]]; then
  sudo rmmod -f nouveau 2>/dev/null || true
fi

if [[ "${DO_USER_GROUPS}" -eq 1 ]]; then
  TARGET_USER="${SUDO_USER:-$USER}"
  sudo usermod -aG video,render "${TARGET_USER}" || true
  echo "User ${TARGET_USER} added to video/render groups."
  REBOOT_REQUIRED=1
fi

if [[ "${DO_BASHRC_CUDA_PATHS}" -eq 1 ]]; then
  TARGET_USER="${SUDO_USER:-$USER}"
  TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
  TARGET_BASHRC="${TARGET_HOME}/.bashrc"
  MARKER="NVIDIA CUDA Paths"

  [[ -f "${TARGET_BASHRC}" ]] || sudo -u "${TARGET_USER}" touch "${TARGET_BASHRC}" || true

  if ! grep -q "${MARKER}" "${TARGET_BASHRC}" 2>/dev/null; then
    cat >> "${TARGET_BASHRC}" <<'EOF'

# NVIDIA CUDA Paths
export PATH="/usr/local/cuda/bin:$PATH"
export LD_LIBRARY_PATH="/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
EOF
    echo "Added CUDA PATH to ${TARGET_BASHRC}"
  fi
fi

#-------------------------------------------------------------------------------
# VERIFICATION
#-------------------------------------------------------------------------------
if [[ "${DO_VERIFY_NVIDIA_SMI}" -eq 1 ]]; then
  echo "Verifying nvidia-smi..."
  nvidia-smi || true
fi

if [[ "${DO_VERIFY_NVCC}" -eq 1 ]]; then
  if command -v nvcc >/dev/null 2>&1; then
    echo "Verifying nvcc..."
    nvcc -V || true
  else
    echo "nvcc not found in PATH yet (re-login/reboot may be needed)"
  fi
fi

#-------------------------------------------------------------------------------
# NVIDIA CONTAINER TOOLKIT
#-------------------------------------------------------------------------------
if [[ "${DO_INSTALL_NVIDIA_CONTAINER_TOOLKIT}" -eq 1 ]]; then
  if command -v docker >/dev/null 2>&1; then
    echo "Docker detected: installing NVIDIA Container Toolkit..."
    sudo -E apt-get update
    sudo -E apt-get install -y -o Dpkg::Options::="--force-confdef" curl gnupg2

    sudo install -d -m 0755 /usr/share/keyrings
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
          | sudo gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

    sudo -E apt-get update
    sudo -E apt-get install -y -o Dpkg::Options::="--force-confdef" nvidia-container-toolkit

    if [[ "${DO_CONFIGURE_DOCKER_RUNTIME}" -eq 1 ]]; then
      command -v nvidia-ctk >/dev/null 2>&1 && sudo nvidia-ctk runtime configure --runtime=docker || true
    fi

    if [[ "${DO_RESTART_DOCKER}" -eq 1 ]]; then
      sudo systemctl restart docker || true
    fi
  else
    echo "Docker not found. Skipping container toolkit."
  fi
fi

#-------------------------------------------------------------------------------
# FINAL
#-------------------------------------------------------------------------------
echo ""
echo "========================================"
echo "Installation finished"
echo "========================================"

if [[ "${REBOOT_REQUIRED}" -eq 1 ]]; then
  echo ">> REBOOT REQUIRED: sudo reboot"
fi

echo "After reboot, verify with:"
echo "  nvidia-smi"
echo "  nvcc -V"
