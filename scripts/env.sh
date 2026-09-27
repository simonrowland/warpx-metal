#!/usr/bin/env bash
# env.sh — Shared paths and configuration for WarpX-on-Metal builds
# Source this file before running other scripts:  source scripts/env.sh

set -euo pipefail

# Project root (resolve relative to this script's location)
export WARPX_METAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# This value is derived below; never carry it across a re-source with a changed override.
unset WARPX_MACOS_SDK_RESOLVED

# Resolve Homebrew explicitly because non-login shells may not have it on PATH.
BREW_BIN_USING_FALLBACK=0
BREW_BIN_WAS_RELATIVE=0
BREW_BIN="$(command -v brew 2>/dev/null || true)"
if [ -z "${BREW_BIN}" ] || [ ! -x "${BREW_BIN}" ]; then
    BREW_BIN="/opt/homebrew/bin/brew"
    BREW_BIN_USING_FALLBACK=1
fi
if [ ! -x "${BREW_BIN}" ]; then
    echo "[FAIL] Homebrew not found: tried command -v brew and ${BREW_BIN}" >&2
    return 1
fi
case "${BREW_BIN}" in
    /*) ;;
    *)
        BREW_BIN_WAS_RELATIVE=1
        if ! BREW_BIN="$(cd "$(dirname "${BREW_BIN}")" && pwd -P)/$(basename "${BREW_BIN}")"; then
            echo "[FAIL] Unable to canonicalize Homebrew path: ${BREW_BIN}" >&2
            return 1
        fi
        ;;
esac
export BREW_BIN
if [ "${BREW_BIN_USING_FALLBACK}" -eq 1 ] || [ "${BREW_BIN_WAS_RELATIVE}" -eq 1 ]; then
    BREW_BIN_DIR="$(cd "$(dirname "${BREW_BIN}")" && pwd -P)"
    export PATH="${BREW_BIN_DIR}:${PATH}"
fi

brew_prefix() {
    local formula="$1"
    local fallback="${2:-}"
    local prefix

    if prefix="$("${BREW_BIN}" --prefix "${formula}" 2>/dev/null)"; then
        printf '%s\n' "${prefix}"
    elif [ -n "${fallback}" ]; then
        echo "[WARN] Homebrew formula ${formula} is unavailable; using fallback ${fallback}" >&2
        printf '%s\n' "${fallback}"
    else
        echo "[FAIL] Homebrew formula ${formula} is unavailable via ${BREW_BIN}" >&2
        echo "       Install it with: ${BREW_BIN} install ${formula}" >&2
        return 1
    fi
}

# Honour an explicit SDK pin while preserving xcrun's default when unset.
resolve_macos_sdk() {
    local sdk="${WARPX_MACOS_SDK:-}"
    if [ -n "${sdk}" ]; then
        if [ ! -d "${sdk}" ]; then
            echo "[FAIL] WARPX_MACOS_SDK does not exist: ${sdk}" >&2
            return 1
        fi
        if ! sdk="$(cd "${sdk}" && pwd -P)"; then
            echo "[FAIL] Unable to canonicalize WARPX_MACOS_SDK: ${sdk}" >&2
            return 1
        fi
        if [ ! -f "${sdk}/SDKSettings.plist" ]; then
            echo "[FAIL] WARPX_MACOS_SDK is not a macOS SDK: missing ${sdk}/SDKSettings.plist" >&2
            return 1
        fi
        local canonical_name
        if ! canonical_name="$(plutil -extract CanonicalName raw -o - "${sdk}/SDKSettings.plist" 2>/dev/null)" \
            || [[ "${canonical_name}" != macosx* ]]; then
            echo "[FAIL] WARPX_MACOS_SDK is not a macOS SDK: CanonicalName=${canonical_name:-<missing>}" >&2
            return 1
        fi
        if ! plutil -extract Version raw -o - "${sdk}/SDKSettings.plist" >/dev/null 2>&1 \
            && ! plutil -extract ProductBuildVersion raw -o - "${sdk}/SDKSettings.plist" >/dev/null 2>&1; then
            echo "[FAIL] WARPX_MACOS_SDK is not a readable macOS SDK: ${sdk}/SDKSettings.plist" >&2
            return 1
        fi
    else
        if ! sdk="$(xcrun --sdk macosx --show-sdk-path)"; then
            echo "[FAIL] Unable to resolve the default macOS SDK with xcrun" >&2
            return 1
        fi
    fi
    if [ ! -d "${sdk}" ]; then
        echo "[FAIL] Resolved macOS SDK does not exist: ${sdk}" >&2
        return 1
    fi
    printf '%s\n' "${sdk}"
}

ensure_macos_sdk() {
    if [ -z "${WARPX_MACOS_SDK_RESOLVED:-}" ]; then
        local resolved_sdk
        if ! resolved_sdk="$(resolve_macos_sdk)"; then
            return 1
        fi
        WARPX_MACOS_SDK_RESOLVED="${resolved_sdk}"
        export WARPX_MACOS_SDK_RESOLVED
    fi
}

if [ -n "${WARPX_MACOS_SDK:-}" ]; then
    if ! WARPX_MACOS_SDK_RESOLVED="$(resolve_macos_sdk)"; then
        return 1
    fi
    export WARPX_MACOS_SDK_RESOLVED
fi

# LLVM 20 (AdaptiveCpp develop branch Metal backend requires LLVM 20 APIs)
# LLVM 18 is also needed for ld64.lld (not shipped in LLVM 20 Homebrew bottle)
LLVM_PREFIX="$(brew_prefix llvm@20 /opt/homebrew/opt/llvm@20)"
export LLVM_PREFIX
export LLVM_DIR="${LLVM_PREFIX}/lib/cmake/llvm"
export CC="${LLVM_PREFIX}/bin/clang"
export CXX="${LLVM_PREFIX}/bin/clang++"

# Metal-cpp headers
export METAL_CPP_DIR="${WARPX_METAL_ROOT}/opt/metal-cpp"

# AdaptiveCpp install prefix
export ACPP_INSTALL_PREFIX="${WARPX_METAL_ROOT}/opt/adaptivecpp"

# AMReX install prefix
export AMREX_INSTALL_PREFIX="${WARPX_METAL_ROOT}/opt/amrex"

# AMReX source
export AMREX_SOURCE_DIR="${WARPX_METAL_ROOT}/extern/amrex"

# WarpX source
export WARPX_SOURCE_DIR="${WARPX_METAL_ROOT}/extern/warpx"
export WARPX_GPU_BUILD_DIR="${WARPX_SOURCE_DIR}/build-acpp"
export WARPX_CPU_BUILD_DIR="${WARPX_SOURCE_DIR}/build-cpu"

# AdaptiveCpp source
export ACPP_SOURCE_DIR="${WARPX_METAL_ROOT}/extern/AdaptiveCpp"

# Patches
export PATCHES_DIR="${WARPX_METAL_ROOT}/patches"

# Add installed AdaptiveCpp to PATH
if [ -d "${ACPP_INSTALL_PREFIX}/bin" ]; then
    export PATH="${ACPP_INSTALL_PREFIX}/bin:${PATH}"
fi

# Build parallelism
export NPROC="${NPROC:-$(sysctl -n hw.ncpu)}"

echo "=== WarpX-on-Metal Environment ==="
echo "  Project root:    ${WARPX_METAL_ROOT}"
echo "  LLVM prefix:     ${LLVM_PREFIX}"
echo "  LLVM_DIR:        ${LLVM_DIR}"
echo "  Metal-cpp:       ${METAL_CPP_DIR}"
echo "  AdaptiveCpp src: ${ACPP_SOURCE_DIR}"
echo "  AdaptiveCpp dst: ${ACPP_INSTALL_PREFIX}"
echo "  macOS SDK:       ${WARPX_MACOS_SDK_RESOLVED:-<deferred until configure>}"
echo "  Parallelism:     ${NPROC} cores"
echo "=================================="
