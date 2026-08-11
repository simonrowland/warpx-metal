# Patched WarpX on Apple Metal

> **Public source of truth.**
>
> These instructions follow the public fork's default branch at
> `https://github.com/simonrowland/warpx-metal.git`. Clone it normally with no
> branch flag, cherry-pick, or detached checkout. Record the resolved HEAD
> informationally, but accept the package only when the patch-payload digest
> below matches the reviewed build inputs.
>
> The validated patch payload is:
>
> - `patches/warpx/` — `0001`, `0003`, `0004`, `0005`, `0006`, `0007`, `0008`, `0009`
> - `patches/amrex-post/` — `0004`, `0005`, `0006`
> - `patches/adaptivecpp/` — `0008`, `0018`–`0020`, `0022`, `0023`–`0033`
> - `patches/amrex/` — three replacement source files, not patches
>
> The numbering gaps are deliberate: retired patches keep their numbers rather
> than renumbering the series. In particular `patches/warpx/0002` no longer
> exists — it was the parsed-momentum *workaround*, retired once
> `patches/amrex-post/0006` fixed the underlying `ParserExecutor` defect.
>
> Always record the public default ref and HEAD plus the resolved clone HEAD.
> The clone HEAD is informational: acceptance fails closed on the expected
> patch-payload digest, not on an exact documentation-branch commit.

This installs single-precision WarpX for the four currently licensed
geometries—1D, 2D Cartesian, 3D Cartesian, and RZ—with:

- a NOMPI SYCL/Metal GPU executable; and
- a matching NOMPI Apple-Clang/OpenMP CPU executable.

The fleet layout used here is:

```text
/Applications/warpx-metal-prod-20260728/
  bin/
    gpu-1.warpx.1d.NOMPI.SYCL.SP.PSP.EB
    gpu-2.warpx.2d.NOMPI.SYCL.SP.PSP.EB
    gpu-3.warpx.3d.NOMPI.SYCL.SP.PSP.EB
    gpu-rz.warpx.rz.NOMPI.SYCL.SP.PSP.EB
    cpu-patched-1.warpx.1d.NOMPI.OMP.SP.PSP.EB
    cpu-patched-2.warpx.2d.NOMPI.OMP.SP.PSP.EB
    cpu-patched-3.warpx.3d.NOMPI.OMP.SP.PSP.EB
    cpu-patched-rz.warpx.rz.NOMPI.OMP.SP.PSP.EB
  meta/
  opt/adaptivecpp/
  extern/
  patches/
  scripts/

/Applications/warpx-metal-prod-current
  -> /Applications/warpx-metal-prod-20260728
```

Use a new dated prefix for a later build. Never build over an existing
production prefix.

## 1. Prerequisites

Required on the build host:

- Apple Silicon macOS with working Command Line Tools:
  `xcrun --sdk macosx --show-sdk-path` and `xcrun -find clang++`;
- Homebrew;
- `git`, `python3`, `curl`, `unzip`, and normal POSIX utilities;
- network access to GitHub, Homebrew, and Apple's `metal-cpp` archive;
- enough space for AdaptiveCpp, AMReX, WarpX, and separate per-geometry build
  trees; and
- write access to the new `/Applications/warpx-metal-prod-<date>` prefix.

`scripts/00-install-deps.sh` installs these Homebrew formulae:

```text
llvm@20 llvm@18 boost ninja cmake libomp
```

LLVM 20 builds AdaptiveCpp. LLVM 18 supplies `ld64.lld`, which the LLVM 20
Homebrew bottle does not. The script also downloads Apple `metal-cpp` headers
under the package prefix.

> **★ Install the Metal Toolchain before building, or the resulting binary will
> be about twice as slow.**
>
> `xcodebuild -downloadComponent MetalToolchain`
>
> A build *without* it still succeeds and still runs correctly — which is the
> problem, because nothing about the slow binary looks wrong. With the component
> present, AdaptiveCpp compiles kernels ahead of time with Apple's `metal`
> compiler and loads them as `.metallib`; without it, kernels go through the
> runtime SSCP JIT path instead, and Apple's compiler produces materially better
> device code.
>
> Measured on one host, one deck (3D 96×96×320 PPC1, single box), identical
> runtime environment, differing only in whether the binary was built with the
> component available:
>
> | build | steady per-step | first step (compile) |
> |---|---:|---:|
> | with Metal Toolchain (AOT `.metallib`) | **~24 ms** | ~14 s |
> | without (runtime JIT) | **~51 ms** | ~57 s |
>
> The per-step gap is sustained, not a warm-up artifact: the JIT build stays flat
> near 51 ms through step 999. Both builds produce identical particle counts, so
> correctness is unaffected — only speed.
>
> Confirm the component resolves before building:
>
> ```bash
> xcrun -f metal
> ```
>
> Then confirm at runtime that the ahead-of-time path is actually being used, by
> setting `ACPP_METAL_ENABLE_OFFLINE_METALLIB=1` and checking the run log for
> `ACPP_METAL_AOT` lines — `metallib-cache-load=hit` means it is working. **No
> such lines means the binary has no ahead-of-time support compiled in**, and you
> are on the slow path regardless of the environment variable.

Suggested policy when installing across several machines:

- designate one machine as the build host, and put the build and every
  CPU-heavy command under `nice -n 15` if it has other tenants;
- designate one test machine to absorb GPU work that may wedge the device, because
  recovering a wedged GPU requires a reboot;
- keep any machine carrying long-running or shared workloads to the small
  acceptance smoke below; and
- do not push any Git ref as part of installation.

## 2. Public checkout

Run this on the build host. `PREFIX` must not already exist.

```bash
export PREFIX=/Applications/warpx-metal-prod-20260728
export FORK_URL=https://github.com/simonrowland/warpx-metal.git
export EXPECTED_PATCH_PAYLOAD_SHA256=bd622de06e835a4e1021447f4ee875b5273457fa01deb8026ea63b5b341ebead

test ! -e "$PREFIX"
git ls-remote --exit-code "$FORK_URL" HEAD
git clone "$FORK_URL" "$PREFIX"
git -C "$PREFIX" remote set-head origin --auto
REMOTE_DEFAULT="$(git -C "$PREFIX" symbolic-ref --short refs/remotes/origin/HEAD)"
test "$REMOTE_DEFAULT" = origin/main
test "$(git -C "$PREFIX" rev-parse HEAD)" = \
     "$(git -C "$PREFIX" rev-parse "$REMOTE_DEFAULT")"
test -z "$(git -C "$PREFIX" status --porcelain=v1)"
```

Stop if:

- `git ls-remote` cannot resolve public default-branch HEAD;
- the remote default is not `origin/main`;
- local HEAD differs from fetched `origin/main`;
- the clone is dirty;
- any required package path below is absent; or
- the patch-payload digest differs from the reviewed value. A mismatch means
  the package changed and review and acceptance must be repeated.

```bash
for path in \
  scripts/00-install-deps.sh \
  scripts/01-build-adaptivecpp.sh \
  scripts/02-validate-metal.sh \
  scripts/03-build-amrex.sh \
  scripts/04-validate-amrex.sh \
  scripts/05-build-warpx.sh \
  scripts/06-validate-warpx.sh \
  scripts/07-build-warpx-cpu.sh \
  patches/adaptivecpp \
  patches/amrex \
  patches/amrex-post \
  patches/warpx
do
  test -e "$PREFIX/$path" || {
    printf 'missing package path: %s\n' "$path" >&2
    exit 1
  }
done
```

Compute the patch-payload digest before creating metadata. It covers the
relative path and SHA-256 of every regular file under `patches/`, plus the
AdaptiveCpp, AMReX, and WarpX pin values read from the three build scripts.
Sorting both the paths and manifest makes the result order-independent. It does
not cover documentation, CI files, metadata, build outputs, or build-script
content other than those three pins, so a docs-only or CI-only commit does not
invalidate an otherwise identical payload.

```bash
ACPP_REV="$(
  sed -nE 's/^ACPP_REV="([0-9a-f]{40})"$/\1/p' \
    "$PREFIX/scripts/01-build-adaptivecpp.sh"
)"
AMREX_REV="$(
  sed -nE 's/^AMREX_REV="([0-9a-f]{40})"$/\1/p' \
    "$PREFIX/scripts/03-build-amrex.sh"
)"
WARPX_REV="$(
  sed -nE 's/^WARPX_REV="([0-9a-f]{40})"$/\1/p' \
    "$PREFIX/scripts/05-build-warpx.sh"
)"
for pin in "$ACPP_REV" "$AMREX_REV" "$WARPX_REV"; do
  [[ "$pin" =~ ^[0-9a-f]{40}$ ]]
done

PATCH_PAYLOAD_SHA256="$(
  cd "$PREFIX"
  {
    find patches -type f -print | LC_ALL=C sort |
      while IFS= read -r patch; do
        printf '%s  %s\n' \
          "$(shasum -a 256 "$patch" | awk '{print $1}')" "$patch"
      done
    printf '%s  %s\n' \
      "$ACPP_REV" upstream/AdaptiveCpp \
      "$AMREX_REV" upstream/AMReX \
      "$WARPX_REV" upstream/WarpX
  } | LC_ALL=C sort | shasum -a 256 | awk '{print $1}'
)"
if test "$PATCH_PAYLOAD_SHA256" != "$EXPECTED_PATCH_PAYLOAD_SHA256"; then
  printf '%s\n' \
    "package patch payload changed; repeat package review and acceptance" >&2
  exit 1
fi
```

Record the resolved remote identity and informational clone HEAD. Verify the
checkout is clean before writing metadata into it.

```bash
FORK_DIRTY="$(git -C "$PREFIX" status --porcelain=v1)"
test -z "$FORK_DIRTY"
mkdir -p "$PREFIX/meta"
git -C "$PREFIX" rev-parse HEAD > "$PREFIX/meta/fork-head.txt"
git -C "$PREFIX" remote get-url origin > "$PREFIX/meta/fork-origin.txt"
git -C "$PREFIX" symbolic-ref --short refs/remotes/origin/HEAD \
  > "$PREFIX/meta/fork-default-ref.txt"
git -C "$PREFIX" rev-parse refs/remotes/origin/HEAD \
  > "$PREFIX/meta/fork-default-head.txt"
printf '%s' "$FORK_DIRTY" > "$PREFIX/meta/fork-dirty.txt"
```

## 3. What the scripts actually do

The package does not contain a vendored WarpX source checkout. Its scripts
fetch pinned upstream trees and apply the fork's patch payload:

| tree | upstream pin | package action |
|---|---|---|
| AdaptiveCpp | `4fe52accc3dca8640f2fbcc80dfd260a51fc2fd8` | clone/reset, then apply every `patches/adaptivecpp/*.patch` |
| AMReX | `fa795322b44fff24fef3a795c3b00d24e015ee42` | clone/reset; apply `patches/amrex/*.patch`; copy the three AMReX replacement files; make the scripted source edits; then apply `patches/amrex-post/*.patch` |
| WarpX | `f7db079f9a8ca96d179e02709a29fe6c027ed8ed` | clone/reset, then apply every `patches/warpx/*.patch` |

`05-build-warpx.sh` deliberately repeats the AMReX preparation before applying
the WarpX patches, because WarpX builds that patched AMReX source as a
subproject. `07-build-warpx-cpu.sh` keeps the already-patched WarpX source and
prepares the AMReX source for its CPU build.

The build scripts currently configure 2D and 3D together with
`WarpX_DIMS="2;3"`. The explicit 1D and RZ configure lines below use the same
prepared source trees and flags. Do not infer dimensions from old deployed
prefixes: the licensed set is 1D, 2D, 3D, and RZ.

## 4. Build and validate the toolchain

All commands in this section run on the build host.

```bash
cd "$PREFIX"

nice -n 15 ./scripts/00-install-deps.sh

# Build AdaptiveCpp from the pinned source through the public script.
nice -n 15 ./scripts/01-build-adaptivecpp.sh
./scripts/02-validate-metal.sh

nice -n 15 ./scripts/03-build-amrex.sh
./scripts/04-validate-amrex.sh
```

`02-validate-metal.sh` must report zero failures. `04-validate-amrex.sh` must
complete all ten HeatEquation steps and report `RESULT: PASS`.

The AdaptiveCpp configure performed by `01-build-adaptivecpp.sh` is:

```bash
cmake .. \
  -G Ninja \
  -DCMAKE_INSTALL_PREFIX="${ACPP_INSTALL_PREFIX}" \
  -DCMAKE_C_COMPILER="${CC}" \
  -DCMAKE_CXX_COMPILER="${CXX}" \
  -DCMAKE_OSX_SYSROOT="${MACOS_SDK}" \
  -DLLVM_DIR="${LLVM_DIR}" \
  -DACPP_COMPILER_FEATURE_PROFILE=full \
  -DACPP_LLD_PATH="${LLD_PATH}" \
  -DOpenMP_C_FLAGS="-Xclang -fopenmp -I${OMP_PREFIX}/include" \
  -DOpenMP_CXX_FLAGS="-Xclang -fopenmp -I${OMP_PREFIX}/include" \
  -DOpenMP_C_LIB_NAMES=omp \
  -DOpenMP_CXX_LIB_NAMES=omp \
  -DOpenMP_omp_LIBRARY="${OMP_PREFIX}/lib/libomp.dylib" \
  -DMETAL_INCLUDE_DIR="${METAL_CPP_DIR}" \
  -DWITH_METAL_BACKEND=ON \
  -DWITH_CPU_BACKEND=ON \
  -DWITH_CUDA_BACKEND=OFF \
  -DWITH_ROCM_BACKEND=OFF \
  -DWITH_OPENCL_BACKEND=OFF \
  -DWITH_LEVEL_ZERO_BACKEND=OFF \
  -DCMAKE_BUILD_TYPE=Release
```

The standalone AMReX configure performed by `03-build-amrex.sh` is:

```bash
cmake .. \
  -G Ninja \
  -DCMAKE_INSTALL_PREFIX="${AMREX_INSTALL_PREFIX}" \
  -DCMAKE_CXX_COMPILER="${ACPP}" \
  -DCMAKE_OSX_SYSROOT="${MACOS_SDK}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DAMReX_GPU_BACKEND=SYCL \
  -DAMReX_PRECISION=SINGLE \
  -DAMReX_PARTICLES_PRECISION=SINGLE \
  -DAMReX_SYCL_SUB_GROUP_SIZE=32 \
  -DAMReX_MPI=OFF \
  -DAMReX_OMP=OFF \
  -DAMReX_FORTRAN=OFF \
  -DAMReX_SYCL_AOT=OFF \
  -DAMReX_SYCL_SPLIT_KERNEL=OFF \
  -DAMReX_SYCL_ONEDPL=OFF
```

## 5. Build GPU-SP

First build the script's native 2D+3D pair:

```bash
cd "$PREFIX"
nice -n 15 ./scripts/05-build-warpx.sh
```

The exact configure line used by the script is:

```bash
cmake -S "${WARPX_SOURCE_DIR}" -B . \
  -G Ninja \
  -DCMAKE_CXX_COMPILER="${ACPP}" \
  -DCMAKE_OSX_SYSROOT="${MACOS_SDK}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DWarpX_COMPUTE=SYCL \
  -DWarpX_PRECISION=SINGLE \
  -DWarpX_PARTICLE_PRECISION=SINGLE \
  -DWarpX_DIMS="2;3" \
  -DWarpX_MPI=OFF \
  -DWarpX_FFT=OFF \
  -DWarpX_QED=OFF \
  -DWarpX_OPENPMD=OFF \
  -DWarpX_amrex_src="${AMREX_SOURCE_DIR}"
```

Before running `07-build-warpx-cpu.sh`, build the additional 1D and RZ GPU
trees while AMReX is still in the Metal-prepared state:

```bash
export ACPP="$PREFIX/opt/adaptivecpp/bin/acpp"
export WARPX_SRC="$PREFIX/extern/warpx"
export AMREX_SRC="$PREFIX/extern/amrex"
export MACOS_SDK="$(xcrun --sdk macosx --show-sdk-path)"
export NPROC="$(sysctl -n hw.ncpu)"

for DIM in 1 RZ; do
  BUILD="$WARPX_SRC/build-acpp-$DIM"
  rm -rf "$BUILD"
  nice -n 15 cmake -S "$WARPX_SRC" -B "$BUILD" \
    -G Ninja \
    -DCMAKE_CXX_COMPILER="$ACPP" \
    -DCMAKE_OSX_SYSROOT="$MACOS_SDK" \
    -DCMAKE_BUILD_TYPE=Release \
    -DWarpX_COMPUTE=SYCL \
    -DWarpX_PRECISION=SINGLE \
    -DWarpX_PARTICLE_PRECISION=SINGLE \
    -DWarpX_DIMS="$DIM" \
    -DWarpX_MPI=OFF \
    -DWarpX_FFT=OFF \
    -DWarpX_QED=OFF \
    -DWarpX_OPENPMD=OFF \
    -DWarpX_amrex_src="$AMREX_SRC"
  nice -n 15 cmake --build "$BUILD" --parallel "$NPROC"
done
```

The only per-geometry change is `-DWarpX_DIMS=<1|2|3|RZ>` and a separate
build directory. All GPU deliverables must identify themselves as
`NOMPI.SYCL.SP.PSP`.

## 6. Build patched CPU-SP

Run the package CPU script for 2D+3D:

```bash
cd "$PREFIX"
nice -n 15 ./scripts/07-build-warpx-cpu.sh
```

The exact configure line used by the script is:

```bash
cmake -S "${WARPX_SOURCE_DIR}" -B . \
  -G Ninja \
  -DCMAKE_CXX_COMPILER="${CPU_CXX}" \
  -DCMAKE_C_COMPILER="${CPU_CC}" \
  -DCMAKE_OSX_SYSROOT="${MACOS_SDK}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_FLAGS="-Xpreprocessor -fopenmp -I${LIBOMP_PREFIX}/include" \
  -DCMAKE_EXE_LINKER_FLAGS="-L${LIBOMP_PREFIX}/lib -lomp" \
  -DCMAKE_SHARED_LINKER_FLAGS="-L${LIBOMP_PREFIX}/lib -lomp" \
  -DWarpX_COMPUTE=OMP \
  -DWarpX_PRECISION=SINGLE \
  -DWarpX_PARTICLE_PRECISION=SINGLE \
  -DWarpX_DIMS="2;3" \
  -DWarpX_MPI=OFF \
  -DWarpX_FFT=OFF \
  -DWarpX_QED=OFF \
  -DWarpX_OPENPMD=OFF \
  -DWarpX_amrex_src="${AMREX_SOURCE_DIR}"
```

Build the additional CPU geometries from the CPU-prepared source:

```bash
export CPU_CXX="$(xcrun -find clang++)"
export CPU_CC="$(xcrun -find clang)"
export LIBOMP_PREFIX="$(brew --prefix libomp)"
export WARPX_SRC="$PREFIX/extern/warpx"
export AMREX_SRC="$PREFIX/extern/amrex"
export MACOS_SDK="$(xcrun --sdk macosx --show-sdk-path)"
export NPROC="$(sysctl -n hw.ncpu)"

for DIM in 1 RZ; do
  BUILD="$WARPX_SRC/build-cpu-$DIM"
  rm -rf "$BUILD"
  nice -n 15 cmake -S "$WARPX_SRC" -B "$BUILD" \
    -G Ninja \
    -DCMAKE_CXX_COMPILER="$CPU_CXX" \
    -DCMAKE_C_COMPILER="$CPU_CC" \
    -DCMAKE_OSX_SYSROOT="$MACOS_SDK" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_FLAGS="-Xpreprocessor -fopenmp -I${LIBOMP_PREFIX}/include" \
    -DCMAKE_EXE_LINKER_FLAGS="-L${LIBOMP_PREFIX}/lib -lomp" \
    -DCMAKE_SHARED_LINKER_FLAGS="-L${LIBOMP_PREFIX}/lib -lomp" \
    -DWarpX_COMPUTE=OMP \
    -DWarpX_PRECISION=SINGLE \
    -DWarpX_PARTICLE_PRECISION=SINGLE \
    -DWarpX_DIMS="$DIM" \
    -DWarpX_MPI=OFF \
    -DWarpX_FFT=OFF \
    -DWarpX_QED=OFF \
    -DWarpX_OPENPMD=OFF \
    -DWarpX_amrex_src="$AMREX_SRC"
  nice -n 15 cmake --build "$BUILD" --parallel "$NPROC"
done
```

All CPU deliverables must identify themselves as `NOMPI.OMP.SP.PSP`. These are
the matching patched CPU fallback, not a vanilla upstream comparison build.

## 7. Normalize binaries and record identity

```bash
mkdir -p "$PREFIX/bin" "$PREFIX/meta"

install -m 0755 \
  "$PREFIX/extern/warpx/build-acpp-1/bin/warpx.1d.NOMPI.SYCL.SP.PSP.EB" \
  "$PREFIX/bin/gpu-1.warpx.1d.NOMPI.SYCL.SP.PSP.EB"
install -m 0755 \
  "$PREFIX/extern/warpx/build-acpp/bin/warpx.2d.NOMPI.SYCL.SP.PSP.EB" \
  "$PREFIX/bin/gpu-2.warpx.2d.NOMPI.SYCL.SP.PSP.EB"
install -m 0755 \
  "$PREFIX/extern/warpx/build-acpp/bin/warpx.3d.NOMPI.SYCL.SP.PSP.EB" \
  "$PREFIX/bin/gpu-3.warpx.3d.NOMPI.SYCL.SP.PSP.EB"
install -m 0755 \
  "$PREFIX/extern/warpx/build-acpp-RZ/bin/warpx.rz.NOMPI.SYCL.SP.PSP.EB" \
  "$PREFIX/bin/gpu-rz.warpx.rz.NOMPI.SYCL.SP.PSP.EB"

install -m 0755 \
  "$PREFIX/extern/warpx/build-cpu-1/bin/warpx.1d.NOMPI.OMP.SP.PSP.EB" \
  "$PREFIX/bin/cpu-patched-1.warpx.1d.NOMPI.OMP.SP.PSP.EB"
install -m 0755 \
  "$PREFIX/extern/warpx/build-cpu/bin/warpx.2d.NOMPI.OMP.SP.PSP.EB" \
  "$PREFIX/bin/cpu-patched-2.warpx.2d.NOMPI.OMP.SP.PSP.EB"
install -m 0755 \
  "$PREFIX/extern/warpx/build-cpu/bin/warpx.3d.NOMPI.OMP.SP.PSP.EB" \
  "$PREFIX/bin/cpu-patched-3.warpx.3d.NOMPI.OMP.SP.PSP.EB"
install -m 0755 \
  "$PREFIX/extern/warpx/build-cpu-RZ/bin/warpx.rz.NOMPI.OMP.SP.PSP.EB" \
  "$PREFIX/bin/cpu-patched-rz.warpx.rz.NOMPI.OMP.SP.PSP.EB"

test "$(find "$PREFIX/bin" -type f -perm +111 | wc -l | tr -d ' ')" = 8
shasum -a 256 "$PREFIX"/bin/* > "$PREFIX/meta/binaries.sha256"
find "$PREFIX/patches" -type f -print0 |
  sort -z |
  xargs -0 shasum -a 256 > "$PREFIX/meta/patches.sha256"
sw_vers > "$PREFIX/meta/build-host-sw_vers.txt"
uname -a > "$PREFIX/meta/build-host-uname.txt"

for binary in "$PREFIX"/bin/*; do
  {
    printf 'BINARY=%s\n' "$binary"
    otool -L "$binary"
  } > "$PREFIX/meta/$(basename "$binary").otool-L.txt"
done
```

Check every GPU `otool -L` record. Any absolute AdaptiveCpp path must resolve
inside this exact production prefix on every target. Do not deploy a binary
whose loader path points at a home directory or any other build, test, or
experimental prefix.

## 8. Mandatory acceptance smoke

Do not accept a binary because CMake or Ninja finished. Every binary on every
target host must:

1. reach `STEP 3`;
2. write finite field-energy values with at least one non-zero field channel;
3. write an initialization (`dump-0`) particle plotfile; and
4. show non-zero, analytically correct `momentum_x` for the first parsed
   species in that dump.

The parser-momentum defect leaves the run capable of completing, so steps 1
and 2 do not imply step 4.

Use a tiny, neutral two-species deck with the first species' normalized
momentum parsed as `u_x=0.02`, a static guide field, Full diagnostics at steps
0 and 3, and `FieldEnergy` every step. Keep one grid box. Run the matching
geometry for each binary.

Create the four inputs with this generator:

```bash
mkdir -p "$PREFIX/acceptance"
cat > "$PREFIX/acceptance/write_smoke_input.py" <<'PY'
#!/usr/bin/env python3
from pathlib import Path
import sys

dim = sys.argv[1]
out = Path(sys.argv[2])

spec = {
    "1": {
        "geometry": "1",
        "n_cell": "32",
        "max_grid": "32",
        "lo": "-20.e-6",
        "hi": "20.e-6",
        "field_lo": "periodic",
        "field_hi": "periodic",
        "particle_lo": "periodic",
        "particle_hi": "periodic",
        "ppc": "1",
        "fields": "Ex Ey Ez Bx By Bz jx jy jz rho",
        "extra": "",
    },
    "2": {
        "geometry": "2",
        "n_cell": "16 16",
        "max_grid": "16",
        "lo": "-20.e-6 -20.e-6",
        "hi": "20.e-6 20.e-6",
        "field_lo": "periodic periodic",
        "field_hi": "periodic periodic",
        "particle_lo": "periodic periodic",
        "particle_hi": "periodic periodic",
        "ppc": "1 1",
        "fields": "Ex Ey Ez Bx By Bz jx jy jz rho",
        "extra": "",
    },
    "3": {
        "geometry": "3",
        "n_cell": "8 8 8",
        "max_grid": "8",
        "lo": "-20.e-6 -20.e-6 -20.e-6",
        "hi": "20.e-6 20.e-6 20.e-6",
        "field_lo": "periodic periodic periodic",
        "field_hi": "periodic periodic periodic",
        "particle_lo": "periodic periodic periodic",
        "particle_hi": "periodic periodic periodic",
        "ppc": "1 1 1",
        "fields": "Ex Ey Ez Bx By Bz jx jy jz rho",
        "extra": "",
    },
    "rz": {
        "geometry": "RZ",
        "n_cell": "8 16",
        "max_grid": "16",
        "lo": "0.0 -20.e-6",
        "hi": "20.e-6 20.e-6",
        "field_lo": "none periodic",
        "field_hi": "none periodic",
        "particle_lo": "none periodic",
        "particle_hi": "none periodic",
        "ppc": "1 1 1",
        "fields": "jr jz rho Er Ez Br Bt Bz",
        "extra": "warpx.n_rz_azimuthal_modes = 1",
    },
}[dim.lower()]

text = f"""\
max_step = 3
amr.n_cell = {spec["n_cell"]}
amr.max_grid_size = {spec["max_grid"]}
amr.blocking_factor = 8
amr.max_level = 0
geometry.dims = {spec["geometry"]}
geometry.prob_lo = {spec["lo"]}
geometry.prob_hi = {spec["hi"]}
boundary.field_lo = {spec["field_lo"]}
boundary.field_hi = {spec["field_hi"]}
boundary.particle_lo = {spec["particle_lo"]}
boundary.particle_hi = {spec["particle_hi"]}
warpx.serialize_initial_conditions = 1
warpx.verbose = 1
warpx.cfl = 0.5
warpx.use_filter = 0
{spec["extra"]}
algo.particle_shape = 1
algo.current_deposition = direct
algo.particle_pusher = boris
my_constants.u0 = 0.02
my_constants.B0 = 0.1
warpx.B_ext_grid_init_style = parse_B_ext_grid_function
warpx.Bx_external_grid_function(x,y,z) = "0.0"
warpx.By_external_grid_function(x,y,z) = "0.0"
warpx.Bz_external_grid_function(x,y,z) = "B0"
particles.species_names = electrons ions
electrons.charge = -q_e
electrons.mass = m_e
electrons.injection_style = "NUniformPerCell"
electrons.num_particles_per_cell_each_dim = {spec["ppc"]}
electrons.profile = constant
electrons.density = 1.e20
electrons.momentum_distribution_type = parse_momentum_function
electrons.momentum_function_ux(x,y,z) = "u0"
electrons.momentum_function_uy(x,y,z) = "0.0"
electrons.momentum_function_uz(x,y,z) = "0.0"
ions.charge = q_e
ions.mass = m_p
ions.injection_style = "NUniformPerCell"
ions.num_particles_per_cell_each_dim = {spec["ppc"]}
ions.profile = constant
ions.density = 1.e20
ions.momentum_distribution_type = constant
ions.ux = 0.0
ions.uy = 0.0
ions.uz = 0.0
warpx.reduced_diags_names = FieldEnergy
FieldEnergy.type = FieldEnergy
FieldEnergy.intervals = 1
diagnostics.diags_names = diag1
diag1.intervals = 0,3
diag1.diag_type = Full
diag1.fields_to_plot = {spec["fields"]}
diag1.species = electrons
"""
out.write_text(text)
PY

for dim in 1 2 3 rz; do
  python3 "$PREFIX/acceptance/write_smoke_input.py" \
    "$dim" "$PREFIX/acceptance/inputs-$dim"
done
```

The particle oracle must read the named `momentum_x` component from the
`diag1000000/electrons` particle payload. For this deck its expected SI value
is:

```text
m_e c u_x = 9.1093837139e-31 kg × 299792458 m/s × 0.02
          = 5.4618490689105e-24 kg m/s
```

Require every electron to be finite, non-zero, and within an SP-appropriate
relative tolerance of the analytic value. A scan that merely finds any small
float in the particle file is invalid because particle positions can make
that test pass while momentum is zero.

Create the fail-closed oracle:

```bash
cat > "$PREFIX/acceptance/check_smoke.py" <<'PY'
#!/usr/bin/env python3
from pathlib import Path
import argparse
import hashlib
import json
import math
import re
import struct

M_E = 9.1093837139e-31
C = 299_792_458.0
U0 = 0.02
EXPECTED = M_E * C * U0
REL_TOL = 2.0e-5
ABS_TOL = abs(EXPECTED) * REL_TOL


def particle_rows(plotfile: Path):
    species = plotfile / "electrons"
    lines = [
        line.strip()
        for line in (species / "Header").read_text().splitlines()
        if line.strip()
    ]
    version = lines[0]
    if not version.endswith("_single"):
        raise ValueError(f"expected single particle output, got {version}")
    ndim = int(lines[1])
    nreal = int(lines[2])
    names = lines[3 : 3 + nreal]
    cursor = 3 + nreal
    nint = int(lines[cursor])
    cursor += 1 + nint
    cursor += 1  # checkpoint flag
    count = int(lines[cursor])
    files = sorted((species / "Level_0").glob("DATA_*"))
    if len(files) != 1:
        raise ValueError(f"one-box smoke expected one DATA file, got {len(files)}")
    raw = files[0].read_bytes()
    width = ndim + nreal
    expected_bytes = count * (8 + 4 * width)
    if len(raw) != expected_bytes:
        raise ValueError(
            f"unexpected particle layout: {len(raw)} != {expected_bytes}"
        )
    offset = count * 8
    rows = [
        struct.unpack_from(f"<{width}f", raw, offset + 4 * width * i)
        for i in range(count)
    ]
    columns = [*[f"position_{axis}" for axis in "xyz"[:ndim]], *names]
    return columns, rows


def field_energy(run: Path):
    candidates = []
    for base in (run / "reduced_diags", run / "diags" / "reducedfiles", run / "diags"):
        if base.is_dir():
            candidates.extend(base.rglob("*FieldEnergy*.txt"))
    # de-dupe
    files = sorted({f.resolve() for f in candidates})
    if len(files) != 1:
        raise ValueError(f"expected one FieldEnergy file, got {files}")
    rows = []
    for line in files[0].read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        rows.append([float(token) for token in stripped.replace(",", " ").split()])
    if len(rows) < 3:
        raise ValueError(f"expected at least three FieldEnergy rows, got {len(rows)}")
    field_values = [value for row in rows for value in row[2:]]
    return {
        "path": str(files[0]),
        "rows": len(rows),
        "finite": all(math.isfinite(value) for value in field_values),
        "nonzero": any(abs(value) > 0.0 for value in field_values),
        "max_abs": max((abs(value) for value in field_values), default=0.0),
    }


parser = argparse.ArgumentParser()
parser.add_argument("--run", type=Path, required=True)
parser.add_argument("--binary", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

log = (args.run / "run.log").read_text(errors="replace")
step3 = bool(re.search(r"\bSTEP\s+3\b", log))
plot = args.run / "diags" / "diag1000000"
if not plot.is_dir():
    raise SystemExit(f"missing exact dump-0 plotfile: {plot}")
columns, rows = particle_rows(plot)
momentum_index = columns.index("momentum_x")
observed = [row[momentum_index] for row in rows]
momentum = {
    "particle_count": len(observed),
    "expected_kg_m_s": EXPECTED,
    "observed_min_kg_m_s": min(observed),
    "observed_max_kg_m_s": max(observed),
    "finite": all(math.isfinite(value) for value in observed),
    "nonzero": bool(observed) and all(abs(value) > ABS_TOL for value in observed),
    "analytic_match": bool(observed) and all(
        math.isclose(value, EXPECTED, rel_tol=REL_TOL, abs_tol=ABS_TOL)
        for value in observed
    ),
    "relative_tolerance": REL_TOL,
    "absolute_tolerance_kg_m_s": ABS_TOL,
}
fields = field_energy(args.run)
binary_sha256 = hashlib.sha256(args.binary.read_bytes()).hexdigest()
checks = {
    "step3": step3,
    "field_energy_finite": fields["finite"],
    "field_energy_nonzero": fields["nonzero"],
    "dump0_particles_present": momentum["particle_count"] > 0,
    "dump0_momentum_finite": momentum["finite"],
    "dump0_momentum_nonzero": momentum["nonzero"],
    "dump0_momentum_analytic_match": momentum["analytic_match"],
}
result = {
    "schema": "warpx-metal-install-smoke.v1",
    "binary": str(args.binary),
    "binary_sha256": binary_sha256,
    "run": str(args.run),
    "fields": fields,
    "momentum_x": momentum,
    "checks": checks,
    "verdict": "PASS" if all(checks.values()) else "FAIL",
}
args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
print(json.dumps(result, indent=2, sort_keys=True))
raise SystemExit(0 if result["verdict"] == "PASS" else 1)
PY
chmod 0755 "$PREFIX/acceptance/"*.py
```

For one binary, run from a fresh directory so an older dump cannot satisfy the
oracle:

```bash
export DIM=3
export GPU_BINARY="$PREFIX/bin/gpu-3.warpx.3d.NOMPI.SYCL.SP.PSP.EB"
export RUN="$PREFIX/acceptance/results/$(hostname)/gpu-$DIM"
test ! -e "$RUN"
mkdir -p "$RUN"
cp "$PREFIX/acceptance/inputs-$DIM" "$RUN/inputs"
cd "$RUN"
env -u MTL_SHADER_VALIDATION \
  WARPX_MAX_MEM_FRACTION=0.99 \
  "$GPU_BINARY" inputs \
  particles.redistribute_uniform_tile_leave=1 \
  > run.log 2>&1
python3 "$PREFIX/acceptance/check_smoke.py" \
  --run "$RUN" \
  --binary "$GPU_BINARY" \
  --out "$RUN/verdict.json"
```

Repeat from a fresh result directory for GPU and CPU in all four geometries.
Use the corresponding `gpu-{1,2,3,rz}` or `cpu-patched-{1,2,3,rz}` binary and
`inputs-{1,2,3,rz}`. Preserve every `verdict.json`.

For each GPU invocation:

```bash
env -u MTL_SHADER_VALIDATION \
  WARPX_MAX_MEM_FRACTION=0.99 \
  "$GPU_BINARY" inputs \
  particles.redistribute_uniform_tile_leave=1
```

For each CPU invocation, set `CPU_BINARY` to the matching normalized artifact.
For example, for 3D:

```bash
export CPU_BINARY="$PREFIX/bin/cpu-patched-3.warpx.3d.NOMPI.OMP.SP.PSP.EB"
OMP_NUM_THREADS=4 OMP_PROC_BIND=close OMP_PLACES=cores \
  "$CPU_BINARY" inputs
```

Do not enable `particles.redistribute_uniform_tile_leave` on a normal CPU
fallback run. On non-GPU builds it deliberately exercises host full-leave
semantics and warns; it is not required for CPU stability.

Acceptance evidence for every `(host, backend, geometry)` tuple must retain:

```text
binary path
binary SHA-256
fork remote URL, public default ref and HEAD, resolved clone HEAD, and patch-payload SHA-256
host macOS version and build
exact command and environment
stdout/stderr log
STEP-3 assertion
FieldEnergy finite/non-zero assertion with numeric extrema
dump-0 particle count
dump-0 momentum observed min/max, analytic value, tolerance, and verdict
wall time
```

Do not reuse the build-host smoke as a target-host result. In particular, an
artifact built on the designated build host must also run on a separate
compatibility target running macOS 26.5.1 / build 25F80, one OS build behind
the build host.

## 9. Metal runtime rules

These are operating requirements, not tuning suggestions.

- **W3 is mandatory for GPU particle runs:** pass
  `particles.redistribute_uniform_tile_leave=1`. It avoids the mixed
  stay/leave redistribution path implicated in the Metal stall. The workaround
  relocates the whole tile and can change in-tile ordering; compare particle
  identity/order-insensitively.
- **Single rank only:** these production artifacts are `NOMPI`; run them
  directly, never through `mpirun`. The W3 implementation is unvalidated for
  multi-rank GPU and its rank guard aborts.
- **Give every GPU process at least 600 seconds of startup grace.** Cold Metal
  compilation has been silent for as long as approximately 346 seconds, and
  the cache does not remove that cost. A shorter watchdog kills healthy runs.
- **Compilation and a hang look identical in WarpX stdout.** Sample
  `MTLCompilerService` while the WarpX process is quiet:

  ```bash
  pgrep -fl MTLCompilerService
  ps -axo pid,etime,%cpu,command | grep '[M]TLCompilerService'
  ```

  Sustained compiler CPU means compilation is active: wait. A live, quiet
  WarpX process with an idle compiler over repeated samples after the startup
  grace is a hang, not compilation. Preserve the log and process sample. Treat
  the run as hung, and reboot the affected host if the GPU remains wedged.
- Keep `MTL_SHADER_VALIDATION` unset for accepted runs.
- **The offline-metallib and binary-archive paths are opt-in at runtime and
  default off.** A build that never sets them carries the code but never
  executes it, and will not reproduce the timings these patches were measured
  against. Set `ACPP_METAL_ENABLE_OFFLINE_METALLIB=1` to publish and load
  precompiled `.metallib` output, and `ACPP_METAL_ENABLE_BINARY_ARCHIVE=1` to
  enable the pipeline binary archive. Both write into `ACPP_APPDB_DIR`, so give
  each distinct binary its own; a stale directory shared with a different build
  yields misses rather than errors, which is easy to mistake for slow hardware.
- **Bound the stall detection explicitly.** `ACPP_METAL_COMMAND_TIMEOUT_MS`
  (default `120000`) sets how long a queue waits before latching a failure and
  aborting; `ACPP_METAL_CMDBUF_WATCHDOG_SEC` enables command-buffer stall
  diagnostics. `30000` and `30` are reasonable for long runs and cut the time
  spent inside a stall roughly fourfold without affecting healthy runs. Note
  the abort is fail-closed by design: the run ends with a named error rather
  than hanging, but the GPU itself does not recover and the host still needs a
  reboot before the next attempt.
- Keep `WARPX_MAX_MEM_FRACTION=0.99` on GPU launches unless a later,
  separately verified package removes that requirement.
- The W3 soak establishes a workaround envelope, not unlimited production
  stability. Do not infer a supported problem size beyond this three-step smoke
  without separate validation at the intended scale.

## 10. Deploy the identical prefix to the fleet

Do not rebuild separately on the target hosts. Transfer the accepted build-host
prefix byte-for-byte to the same absolute path on every target host. The
same-path rule is load-bearing for absolute runtime library paths and for the
AdaptiveCpp appdb subdirectory, which is derived from the executable's absolute
path.

Before transfer, record on each target:

```bash
sw_vers
readlink /Applications/warpx-metal-prod-current || true
find /Applications -maxdepth 1 \
  \( -type d -o -type l \) \
  -name 'warpx-metal*' -print | sort
```

Copy into a new incoming directory, verify the source manifest, then rename it
to the final prefix. Never overwrite or delete an existing prefix.

After transfer on each host:

```bash
export PREFIX=/Applications/warpx-metal-prod-20260728
cd "$PREFIX"
shasum -a 256 -c meta/binaries.sha256
test "$(find bin -type f -perm +111 | wc -l | tr -d ' ')" = 8
```

Run the complete acceptance smoke in section 8 on that host, directly from the
dated prefix. Only after every required target-host check passes, update the
symlink:

```bash
ln -sfn "$PREFIX" /Applications/warpx-metal-prod-current
test "$(readlink /Applications/warpx-metal-prod-current)" = "$PREFIX"
```

This operation does not remove the prior target or any experimental prefix.
Rollback is the prior symlink target recorded before deployment.

## 11. Fleet completion criterion

Declare `DEPLOYMENT-CONSISTENT` only when every target host has:

- the same dated prefix and current symlink target;
- the same eight executable SHA-256 values;
- resolvable loader dependencies;
- a retained host identity;
- GPU and patched-CPU three-step smokes for every licensed geometry;
- finite non-zero field evidence; and
- a parsed, named dump-0 first-species momentum result matching the analytic
  value.

Any missing host, backend, geometry, hash, or dump-0 oracle is `PARTIAL`, not a
pass.
