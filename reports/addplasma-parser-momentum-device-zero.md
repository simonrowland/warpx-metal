# Metal AddPlasma parser-momentum SSCP device-executor fix

## Summary

On the single-precision Metal/SYCL path, device evaluation of
`parse_momentum_function` through `InjectorMomentumParser` could write zero
momentum during `AddPlasma`. Particle count, position, per-particle weight, and
total weight remained correct. The defect was visible in the initial particle
dump before deposition.

The measured failure was dimension-dependent on the single-precision Metal path:

- **1D and 3D:** call ordinal, not species identity — the first parsed
  `AddPlasma` call was zero and the second was analytically correct, including
  when species order was reversed. This remains a measured call-order
  observation, not a fully explained mechanism: a minimal nested-host
  reproducer zeros *every* call and does not yet account for why the second
  Cartesian call survived.
- **RZ:** all tested parsed species were zero (not first-only).
- **XZ:** was previously clean only because an XZ-only host re-evaluation path
  masked the device defect (later generalized, then retired).

No out-of-bounds access is established by the current evidence. Earlier patch
and report names that preserved an unproven diagnosis have been retired.

## Mechanism

AdaptiveCpp SSCP never defines `__SYCL_DEVICE_ONLY__` in device kernels on the
toolchain used by this port. Unpatched AMReX therefore treats
`AMREX_IF_ON_DEVICE` as a no-op and always takes the host branch of
`ParserExecutor`, including inside GPU kernels. That is the same macro-family
defect as the existing GpuAtomic SSCP bypass.

The particle path reaches the host executor through a copied
`InjectorMomentum` object and its nested union member. Minimal Metal
reproducers show:

1. Nested host-executor evaluation returns zero.
2. By-value and device-executor paths return the expected bytecode answer.
3. Promoting only the raw device bytecode pointer to a top-level kernel capture
   did not repair the WarpX path (non-finite momentum instead).

That explains the zero *class*. It does **not** explain the Cartesian
first-call-only asymmetry (the nested-stock reproducer zeros every call).

## Fix

Patch:
[`patches/amrex-post/0006-fix-parserexecutor-sscp-device-executor.patch`](../patches/amrex-post/0006-fix-parserexecutor-sscp-device-executor.patch)

Under AdaptiveCpp unified host/device passes
(`AMREX_USE_SYCL` with `__ACPP_ENABLE_LLVM_SSCP_TARGET__` or
`ACPP_LIBKERNEL_CUDA_NVCXX`), `ParserExecutor` uses AdaptiveCpp runtime target
selectors — `__acpp_if_target_device` / `__acpp_if_target_host` — so each
entry point evaluates the matching executor. There is no compile-time
device-preferred fallback: host calls keep host bytecode (device arena is not
host-accessible), and device calls keep device bytecode (host-executor-in-kernel
is the original defect). The original `AMREX_IF_ON_DEVICE` /
`AMREX_IF_ON_HOST` body remains in the `#else` for non-unified paths.

An earlier compile-time “prefer `m_device_executor` under the SSCP macro” form
was wrong for SSCP’s single host/device pass: host calls in the same binary
would take the device executor. That form was replaced by runtime target
dispatch before retirement of the host backfill.

The host-side `AddPlasma` backfill and device zero-bypass in the former WarpX
`0001` / `0002` patches are **retired**; WarpX `0002` is removed and the
public series keeps the number gap rather than renumbering mid-adoption.

```cpp
#if defined(AMREX_USE_SYCL) && (defined(__ACPP_ENABLE_LLVM_SSCP_TARGET__) || defined(ACPP_LIBKERNEL_CUDA_NVCXX))
        __acpp_if_target_device(return parser_exe_eval(m_device_executor, l_var.data());)
        __acpp_if_target_host(return parser_exe_eval(m_host_executor, l_var.data());)
#else
        AMREX_IF_ON_DEVICE((return parser_exe_eval(m_device_executor, l_var.data());))
        AMREX_IF_ON_HOST((return parser_exe_eval(m_host_executor, l_var.data());))
#endif
```

The SSCP path is applied after the existing Metal PIC/parser reductions in
`patches/amrex-post/0004-metal-pic-rng-reduction-fixes.patch` (which introduce
`ParserExeReal` for single precision).

CPU builds, CUDA/HIP builds, and constant, Gaussian, Gaussian-flux, vacuum, and
grid impressed-current paths retain their physical behavior.

## Validation

The unfixed binary was first shown red with a 31-case, fail-closed matrix:

- 1D/Z, XZ, 3D, and coordinate-aware RZ;
- PPC 1 and PPC 2;
- normal and reversed species order;
- restricted-cell analytic-maximum arms;
- dump-0 checks of every particle's position, count, weight, total weight, and
  all momentum components; and
- step-1 primary-field amplitude and GPU/CPU comparisons.

The unfixed result reproduced the call-ordinal zero in 1D/3D and the
all-parsed-species zero in RZ. XZ (with the old host mask) and every nonparsed
control remained clean.

With the SSCP device-executor fix and **no host backfill**, the same matrix
passed 31/31 on particle oracles and field compares for all dimensions,
including every RZ parsed arm and both species. Controls
(constant/Gaussian/vacuum/J_ext where applicable) remained clean. All accepted
Metal runs used the uniform full-tile-leave redistribution workaround
(`particles.redistribute_uniform_tile_leave=1`) and at least a 600-second
initialization grace; shader validation was not enabled.

## Limitations

This is a real fix for the SSCP `AMREX_IF_ON_*` / host-executor class, not a
host-side containment path. It removes the previous device→host→device
momentum round trip at injection.

**Call-ordinal asymmetry remains unexplained** at the minimal-repro level. The
SSCP fix cures both species everywhere (including reversed order and RZ), so
retirement does not depend on explaining it. The Cartesian second-species-OK
path remains a secondary WarpX/Metal-shaped puzzle (lazy translation? arena
pointer caching?) and is left open rather than hand-waved away because a
matrix went green.

**Acceptance boundary (what the 31/31 matrix actually covers):** unboosted
initial `AddPlasma` only, on the single-precision Metal path, with
`particles.redistribute_uniform_tile_leave=1` and ≥600 s init grace.

**Explicitly unvalidated (do not inherit the 31/31 acceptance):**

- boosted injection;
- continuous injection;
- momentum-dependent user attributes;
- parsed `NFluxPerCell` / `AddPlasmaFlux` (still evaluates its momentum
  injector in a separate device kernel);
- runtime validation of the `WARPX_DIM_RCYLINDER` and `WARPX_DIM_RSPHERE`
  coordinate branches (not in the 31-case matrix).

A recipient should still check dump-0 momentum for every parsed species when
exercising unvalidated injection paths.
