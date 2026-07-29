# WarpX Metal GPU Backend

This repository packages an Apple-Metal GPU backend path for
[WarpX](https://github.com/ECP-WarpX/WarpX):

```text
WarpX -> AMReX SYCL -> AdaptiveCpp SSCP -> Metal
```

The patch set covers the AdaptiveCpp SSCP/Metal emitter and runtime, AMReX
particle/parser/reduction/base support, and the WarpX source changes needed for
standard PIC workloads on Apple Silicon GPUs.

## Current Status

- WarpX PIC workloads execute on Apple GPUs through AMReX SYCL and AdaptiveCpp
  SSCP-generated Metal.
- The inherited Metal port baseline was cross-chip field-validated in June 2026
  on M3 Ultra, M4 Max, and M5 Max at source identity
  `952811fb5486adab8b1cdb6d961912a896988401` (commit message: "Add validated
  Apple-Metal GPU WarpX port"). Raw per-run logs from that campaign are not in
  this package; the numbers live in [`VALIDATION.md`](VALIDATION.md).
- Later handoff deltas (parser-momentum SSCP device-executor fix, RZ `J_ext`,
  process supervisor, uniform tile-leave, startup memory guard) have the
  narrower validation boundaries stated in their individual reports; they have
  not all been re-run across those three systems.

## Validation

The June cross-chip baseline was field-level, not only run-completion based:

- Langmuir oscillation: GPU and CPU fields (`Ex`, `jx`) agree to about `1e-6`
  at both 1 and 4 particles per cell, and particle counts match.
- Cyclotron gyration and `E x B` drift: full 640-step runs complete with
  particle datasets bit-identical to CPU output; analytic relative error is
  about `1e-4` to `1e-3`.
- Vacuum electromagnetic wave: GPU and CPU agree at relative `Linf` error near
  `1e-6`.
- Convergence and conservation checks show about second-order convergence and
  conserved charge, energy, momentum, and particle count within the validated
  tolerances.

These checks cover functional agreement and physics-level behavior for the
standard PIC benchmark suite used during the June baseline campaign. See each
report under [`reports/`](reports/) for what later package deltas do and do not
claim.

## Reliability

The backend includes reliability controls for heavy runs:

- Runtime Metal JIT compilation and queue submission are serialized to preserve
  SYCL in-order semantics and bound system compiler-service pressure.
- The system-memory guard uses reclaimable file-cache aware macOS memory
  accounting, so cached file data no longer causes a false abort at the default
  guard threshold.
- Device-to-host readback no longer depends on the completion shared-event
  callback cycle. Producer and blit completion are explicit and bounded; a
  Metal command buffer that does not reach a terminal state returns a timeout
  error instead of hanging indefinitely.
- Any remaining host-side shared-event wait is bounded by the same deadline and
  reports the requested and last observed event values when it expires.
- Long jobs can run as checkpointed process generations with
  `scripts/10-run-warpx-resilient.py`. Each generation creates fresh Metal
  device/queue state, and a completion timeout automatically retries from the
  last checkpoint that was verified after a clean child-process exit. An
  optional SP CPU fallback permanently demotes a wedged run into an isolated
  CPU checkpoint namespace without killing the live Metal process.

The supervisor is candidate recovery tooling for the macOS driver defect: it
never replays uncertain GPU state inside the affected process. CPU checkpoint
continuity, deterministic watchdog paths, failure classification, and negative
controls are verified. A live Metal wedge → demote → complete run remains
outstanding, so production recovery is not claimed. Checkpoint frequency
controls the recovery window and I/O overhead.

Example for a 10,000-step 2D run, using 100-step process generations:

```bash
./scripts/10-run-warpx-resilient.py \
  --max-step 10000 \
  --chunk-steps 100 \
  --startup-grace-seconds 600 \
  --cpu-fallback-executable /path/to/warpx.2d.NOMPI.OMP.SP.PSP.EB \
  --build-identity-manifest /path/to/build-pair.json \
  --cpu-work-dir /path/to/preprovisioned-cpu-run \
  --work-dir /path/to/run \
  extern/warpx/build-acpp/bin/warpx.2d.NOMPI.SYCL.SP.PSP.EB \
  /path/to/inputs
```

See [Metal process-isolated recovery](reports/metal-process-isolated-recovery.md)
for recovery semantics and tuning.

## Handoff Assembly

Deliver the **single** composite ref `handoff-package`. Do not assemble from
two refs and do not cherry-pick `aec5dce`.

That tree already contains the full patch set for this handoff, including
`patches/warpx/0003-correct-macos-memory-guard-available-memory.patch` (same
blob `4a7ff442b6c7a575480f8c9cf91158cc9f607cdb` as the independent
`fix-startup-memory-guard` tip `aec5dce7823dffb4acf9147f993ed72ea332d3e8`).
Cherry-picking `aec5dce` would only re-open a divergent memory-guard *report*
without changing the code patch.

Do not start from `origin/main` / `a4943c2dc7b4d641101cdf8e0cac761e09856ad8`:
that tip is the pre-package base and does not contain the handoff deltas.

```sh
# After fetching the delivered handoff-package ref:
git switch --create warpx-metal-handoff handoff-package
git rev-parse --verify HEAD
# Sanity: HEAD must be a descendant of the pre-doc-fix composite that first
# closed the patch set (still a real object in this repository):
git merge-base --is-ancestor d750544496a7c2ee7fd946eeecc48b3e5c8a4507 HEAD
git cat-file -t d750544496a7c2ee7fd946eeecc48b3e5c8a4507   # commit
# Buildable tree is this checkout. Do not cherry-pick aec5dce.
```

The packaging tip that includes these corrected handoff instructions is the
tip of `handoff-package` at send time (see the review-fix result for the
exact full SHA recorded when this recipe was last verified).

Ancestor notes (for orientation only; not assembly steps):

- `e7498b1` (`uniform-tile-leave`) and `8d0e418` (`jext-batch`) are ancestors.
- `910c3d4` (`parser-device-pointer-fix`) generalized the parser-momentum
  host backfill and is an ancestor of `d750544`. That host backfill was later
  retired in favour of the AMReX `ParserExecutor` SSCP runtime target-dispatch
  fix (`patches/amrex-post/0006-fix-parserexecutor-sscp-device-executor.patch`,
  using `__acpp_if_target_{host,device}` rather than a compile-time device
  preference); WarpX `0002` was removed and the public series keeps the number
  gap rather than renumbering mid-adoption.
- `d750544496a7c2ee7fd946eeecc48b3e5c8a4507` is the pre-doc-fix composite
  that first closed the ten pre-handoff defects; later `handoff-package` tips
  may add documentation-only review fixes on top.
- `aec5dce` is a side branch off `e7498b1` whose **code** patch is already
  present from `d750544` onward; its **report** prose is intentionally not
  preferred.

## Remaining Notes

- GPU atomic deposition is not expected to be run-to-run bitwise deterministic;
  this is standard for GPU PIC. Validated comparisons use physics observables
  and CPU agreement.
- Particle sorting is conservatively forced off on the AdaptiveCpp/HipSYCL
  SYCL GPU path because the packaged WarpX guard treats the Metal sort path as
  particle-attribute-corrupting. The AMReX multipass scan fixes remain in the
  package, but they do not remove that delivered guard.
- On the single-precision Metal path, AdaptiveCpp SSCP leaves
  `__SYCL_DEVICE_ONLY__` unset, so unpatched AMReX `ParserExecutor` evaluates
  its host executor inside GPU kernels and nested particle injectors can read
  zero momentum. The package uses AdaptiveCpp runtime target selectors
  (`__acpp_if_target_device` / `__acpp_if_target_host`) under unified
  host/device passes so host and device each keep their own bytecode — not a
  compile-time device-preferred fallback. The Cartesian call-ordinal asymmetry
  remains an open secondary question; the SSCP fix cures both species
  everywhere without depending on it. Acceptance still covers unboosted
  initial `AddPlasma` only. See
  [the parser-momentum report](reports/addplasma-parser-momentum-device-zero.md).

## Bugfix Reports

Detailed reports describe the failure mechanism, correction, validation scope,
and remaining limitations for the keeper fixes:

- [Metal AddPlasma parser-momentum SSCP device-executor fix](reports/addplasma-parser-momentum-device-zero.md)
- [AdaptiveCpp Metal device-to-host completion hardening](reports/adaptivecpp-d2h-shared-event-completion.md)
- [AdaptiveCpp Metal in-order readback and bounded completion](reports/adaptivecpp-metal-inorder-readback.md)
- [AdaptiveCpp Metal source-identity backend payload](reports/adaptivecpp-metal-source-identity.md)
- [AdaptiveCpp Metal asynchronous error fail-closed state](reports/adaptivecpp-metal-async-error-failclosed.md)
- [AdaptiveCpp Metal timeout fail-closed state](reports/adaptivecpp-metal-timeout-failclosed.md)
- [AMReX Metal PIC RNG and reduction compatibility](reports/amrex-metal-pic-rng-reduction-fixes.md)
- [WarpX Metal particle-sort guard](reports/warpx-metal-particle-sort-guard.md)
- [Cartesian impressed-current coupling](reports/impressed-current-j-ext.md)
- [RZ impressed-current coupling](reports/rz-impressed-current-j-ext.md)
- [Uniform full-tile-leave redistribution](reports/uniform-tile-leave.md)
- [Metal process-isolated checkpoint recovery](reports/metal-process-isolated-recovery.md)
- [macOS system-memory crashguard accounting](reports/macos-memory-crashguard.md)

## Requirements

- Apple Silicon Mac
- macOS 14 or newer
- Xcode 16 or newer with command-line tools
- Homebrew
- Internet access to clone upstream sources and fetch `metal-cpp`

The scripts use Homebrew packages including `llvm@20`, `llvm@18`, `boost`,
`cmake`, `ninja`, and `libomp`.

## Local CI

Run the pinned host-platform CPU build from the repository root:

```bash
nice -n 15 ./ci/run-local-ci.sh
```

The script explicitly applies the packaged AMReX and WarpX portability patches,
then configures, compiles, and link-checks a non-Metal CPU build. See
[`ci/README.md`](ci/README.md) for dependencies and the precise host-platform
and Linux-coverage boundaries.

## Build Flow

From the repository root:

```bash
./scripts/00-install-deps.sh
./scripts/01-build-adaptivecpp.sh
./scripts/02-validate-metal.sh
./scripts/03-build-amrex.sh
./scripts/04-validate-amrex.sh
./scripts/05-build-warpx.sh
./scripts/06-validate-warpx.sh
```

Build products and cloned upstream sources live under `opt/` and `extern/`.
AdaptiveCpp JIT artifacts are cached by the runtime.

## Repository Layout

- `patches/adaptivecpp/` - AdaptiveCpp SSCP/Metal source patch.
- `patches/amrex/` - AMReX source patch and replacement files used by the build
  scripts.
- `patches/warpx/` - WarpX source patch.
- `reports/` - technical bugfix reports and validation boundaries.
- `scripts/` - dependency, build, and validation helpers.
- `tests/` and `benchmarks/` - small validation inputs and benchmark fixtures.
