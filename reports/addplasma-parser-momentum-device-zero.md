# Metal AddPlasma parser-momentum device-zero workaround

## Summary

On the single-precision Metal/SYCL path, device evaluation of
`parse_momentum_function` through `InjectorMomentumParser` can write zero
momentum during `AddPlasma`. Particle count, position, per-particle weight, and
total weight remain correct. The defect is visible in the initial particle dump
before deposition.

The measured failure is dimension-dependent on the single-precision Metal path:

- **1D and 3D:** call ordinal, not species identity — the first parsed
  `AddPlasma` call is zero and the second is analytically correct, including
  when species order is reversed. This is a measured call-order observation,
  not a fully explained mechanism: a minimal nested-host reproducer zeros
  *every* call and does not yet account for why the second Cartesian call
  survives.
- **RZ:** all tested parsed species are zero (not first-only).
- **XZ:** clean because this port already carried an XZ-only host re-evaluation
  path that masks the device defect.

No out-of-bounds access is established by the current evidence. The former
patch and report names preserved an earlier, unproven diagnosis and have been
retired.

Patch:
[`patches/warpx/0002-work-around-metal-parser-momentum-device-zero.patch`](../patches/warpx/0002-work-around-metal-parser-momentum-device-zero.patch)

The companion coordinate-aware host backfill remains in
[`patches/warpx/0001-metal-source-identity.patch`](../patches/warpx/0001-metal-source-identity.patch).

## Observed mechanism

The failing device path reaches parser executors through a copied
`InjectorMomentum` object and its nested union member. Promoting only the raw
device bytecode pointer to a top-level kernel capture did not repair the path;
it produced non-finite momentum instead.

Since package assembly, the zero *class* has been localized on the pinned
AdaptiveCpp SSCP/Metal stack used by this port:

1. `__SYCL_DEVICE_ONLY__` is unset in SSCP device kernels on that toolchain.
2. Unpatched AMReX `ParserExecutor` therefore elides `AMREX_IF_ON_DEVICE` and
   evaluates its host-executor expression inside the kernel.
3. A minimal Metal reproducer zeros nested host-executor evaluation; by-value
   and device-executor paths return the expected bytecode answer.

That explains the zero class and is valuable upstream information. It does
**not** explain the Cartesian first-call-only asymmetry (the nested-stock
reproducer zeros every call). The preferred AMReX SSCP repair (force
`m_device_executor` under AdaptiveCpp SSCP, mirroring the existing GpuAtomic
fast path) has passed a single no-backfill 1D analytic smoke; the full
no-backfill 31-case/RZ/control matrix is still pending. The root fix is
**not** in this package; `0002` remains the host-backfill workaround.

## Workaround

The two existing patches continue to act as one mechanism:

1. For parser momentum under GPU+SYCL, the `AddPlasma` device kernel writes
   temporary zero momentum instead of traversing the suspect nested parser
   state.
2. After final particle positions are available, the host re-evaluates the
   parsed momentum and copies `ux`, `uy`, and `uz` back to the device.
3. The host path reconstructs physical Cartesian coordinates for 1D/Z, XZ,
   3D, RZ, cylindrical, and spherical geometries. RZ uses
   `(r cos(theta), r sin(theta), z)`.
4. At the initial `t=0` identity-boost state, ballistic correction returns the
   input longitudinal coordinate before any unnecessary device parser
   dereference. Later times retain the full ballistic formula.

The momentum bypass and host backfill are parser-only and SYCL-only. The
`t=0` shortcut is backend-independent but algebraically exact. CPU builds,
CUDA/HIP builds, and constant, Gaussian, Gaussian-flux, vacuum, and grid
impressed-current paths retain their physical behavior.

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
all-parsed-species zero in RZ. XZ and every nonparsed control remained clean.

The generalized host workaround then passed the same matrix 31/31 with zero
case failures and zero control failures. Representative step-1 GPU/CPU
amplitude ratios were:

- 1D: `Ex=0.9999999168`, `jx=0.9999999187`, `rho=1.0`
- RZ: `Ez=1.0000000593`, `jz=1.0000000548`,
  `rho=1.0000115596`

Every parsed PPC/order/restricted arm passed. Constant momentum, Gaussian
momentum, source-free vacuum, and grid impressed-current controls remained
clean. All accepted Metal runs used the uniform full-tile-leave redistribution
workaround (`particles.redistribute_uniform_tile_leave=1`) and at least a
600-second initialization grace; shader validation was not enabled.

## Limitations

This is containment via host backfill, not a general device-runtime fix. It
adds a device-to-host and host-to-device particle-momentum round trip during
affected `AddPlasma` injection.

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
  coordinate branches (compile-time extensions of the same reconstruction
  logic, not in the 31-case matrix).

A recipient can apply the workaround successfully and still run an unvalidated
parsed-injection path — check dump-0 momentum for every parsed species.
