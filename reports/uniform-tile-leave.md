# Uniform full-tile leave for particle redistribution

## Summary

Some SYCL/Metal particle workloads can stall when the ordinary mixed
leave/stay redistribution path performs a device-to-host stay-count readback.
This patch adds an opt-in workaround that gives redistribution a uniform shape:
every particle in a tile is treated as a leaver and then reconstructed at its
assigned destination.

The runtime parameter is
`particles.redistribute_uniform_tile_leave`. It is `false` by default.

Patch:
[`patches/amrex-post/0005-metal-uniform-tile-leave.patch`](../patches/amrex-post/0005-metal-uniform-tile-leave.patch)

## Runtime behavior

Flag-ON retains the normal particle normalization, location, periodic fallback,
invalid-particle handling, and destination assignment. It changes the
partition result to zero stayers, so the existing relocation machinery packs
and reconstructs the full tile. This removes the stay-count reduction/readback
from the relocation decision.

Flag-OFF uses the ordinary mixed leave/stay path. The full-leave branch returns
its zero-stayer invariant directly instead of instantiating an otherwise
no-op partition call. A production binary comparison found a 244 KB reduction
and reduced matching kernel-symbol hits from 106 to 66, confirming removal of
the no-op kernel emission. Default-absent and explicit-OFF CPU validation also
matched the unpatched baseline.

Full leave and re-append can change in-tile ordering. Flag-ON comparisons must
therefore use order-insensitive particle identity and destination checks plus
tolerance-based physics observables, not bitwise particle-order hashes.

## Validation

The SYCL/Metal flag-ON path completed a 2,000-of-2,000-iteration soak. A paired
flag-OFF drift guard reproduced the characteristic stall at iteration 29.
Measured throughput was 1.597 times the flag-OFF path.

Post-patch system-level validation outside the packaged AMReX test harness
compared per-component field max-absolute and relative-L2 values. Flag-ON
deviations were approximately the same scale as the flag-OFF platform residual.
The comparison did not isolate a feature-caused field regression, but it is not
a clean parity result and does not establish bitwise equivalence. The packaged
CPU tests separately covered default-absent, explicit-OFF, flag-ON destination
equivalence, invalid-particle behavior, and the rank guard; they do not
establish field-level flag-ON parity.

The multi-rank enablement guard was exercised and aborted with return code 6.

## Cost profile

The workaround relocates every particle in a tile. If the ordinary path moves
a crossing fraction `f_crossing`, packed volume increases from approximately
`f_crossing*N` particles to `N`, a `1/f_crossing` multiplier. The measured
1,048,576-particle case had 8,192 crossers, so the relocation-volume multiplier
was 128.

Let `S` be packed bytes per particle and let `D` be dimension. Move-scaled
metadata is `M = 20 + 4*D` bytes per particle. The source payload, send buffer,
and metadata give a `2*N*S + N*M` pack peak. In the validated non-MPI
configuration, a pinned staging copy can coexist with both send buffers while
destination tiles grow, making `3*N*S + N*M` a conservative
capacity-and-lifetime upper bound, not a predicted measurement. Allocator reuse
and effective capacities can reduce the observed high-water mark. The complete
derivation is retained in the
[packaged implementation](../patches/amrex-post/0005-metal-uniform-tile-leave.patch).

For the validated 2D double-precision case, the derived upper bounds are
188 MiB at pack and 268 MiB at unpack. The measured host-visible peak at about
one million particles was approximately 204 MiB: 152 MiB arena plus 52 MiB
pinned. Conservative 3D sizing gives upper bounds of 208 MiB at pack and
296 MiB at unpack. No out-of-memory failure occurred at the measured size;
larger cases retain memory risk.

## Rank and backend scope

Flag-ON requires exactly one MPI rank and aborts otherwise because multi-rank
cost and correctness are not validated. Flag-OFF does not impose this guard.

Non-GPU builds execute the same host-side full-leave semantics for validation
and emit a warning when enabled. CUDA and HIP builds execute the GPU partition
mechanism but warn that those backends are unvalidated. The validated GPU
backend is SYCL/Metal.

## What is not validated

- Multi-rank GPU execution, correctness, communication cost, and memory use.
- Stability beyond the completed 2,000-iteration horizon.
- CUDA or HIP flag-ON correctness and performance.
- Memory behavior above the measured approximately one-million-particle case.
- Multi-level or positive-`nGrow` GPU redistribution.
