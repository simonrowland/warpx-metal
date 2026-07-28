# RZ (cylindrical) impressed-current coupling

## Summary

Patch `0005` added parser-defined `J_ext` on the Cartesian explicit Yee path and
fail-closed every non-Cartesian geometry, including RZ. This follow-on patch
opens RZ while keeping the same parser surface, the same half-step injection
point, and the same single post-injection `ApplyJfieldBoundary` pass.

Patch:
[`patches/warpx/0006-add-rz-impressed-current-j-ext.patch`](../patches/warpx/0006-add-rz-impressed-current-j-ext.patch)

## Coupling scheme

The public parser remains Cartesian:

```text
warpx.Jx/Jy/Jz_external_grid_function(x,y,z,t)
```

At every live RZ current stagger and angular quadrature sample:

```text
x = r cos(theta),  y = r sin(theta)
Jr     =  Jx cos(theta) + Jy sin(theta)
Jtheta = -Jx sin(theta) + Jy cos(theta)
Jz     =  Jz
```

Coordinates come from each destination `current_fp` MultiFab's live
`ixType()`. Coefficients are deposited into a source-only MultiFab first, then
added once to particle current before the existing single boundary pass. The
divergence, axis-regularity, retained-mode, and bandwidth gates all read the
deposited source MultiFab rather than re-parsing.

WarpX stores `M = n_rz_azimuthal_modes` as `2*M-1` real components (mode 0,
then cosine/sine pairs). Decks must set `n_rz_azimuthal_modes >= 2` so the
flagship Cartesian rotating-magnetic-field source (exactly `m=1`) is retained.
`J_ext_rz_n_theta=0` selects `4*M` samples; an explicit value must be even and
at least `4*M`. `J_ext_rz_max_source_mode` declares the parser bandwidth so
discarded modes can be fail-closed.

## Supported envelope (delta relative to 0005)

| Area | Contract |
|---|---|
| Geometry | RZ enabled; one-dimensional cylindrical/spherical remain abort |
| Modes | `n_rz_azimuthal_modes >= 2` required when RZ `J_ext` is enabled |
| Angular quadrature | `J_ext_rz_n_theta` even and `>= 4*M` (or 0 for the default) |
| Declared bandwidth | `J_ext_rz_max_source_mode` required; discarded modes fail-closed |
| Div-check cadence | `J_ext_div_check_interval` must be positive whenever RZ `J_ext` is on |
| Finite samples | Parser and post-deposit non-finite values abort before the solve mutates |
| Axis regularity | Near-axis deposited residual (m-dependent scaling), not only `r=0` identity |
| Everything else | Same fail-closed envelope as 0005 (AMR, Vay, collocated, hybrid, EB, implicit, moving window, in-domain PML) |

## Validation

CPU-only evidence (no GPU run of this patch was performed for packaging):

| Suite | Result | Coverage |
|---|---:|---|
| RZ CPU-DP green | 7/7 | analytic, solenoidal, tracka, energy pair, monopole, bandwidth, mode-0-only |
| RZ CPU-SP green | 7/7 | same |
| RZ CPU-DP red | 12/12 | bandwidth, interval, NaN/Inf, axis, deposited-contract, sign-flip |
| RZ CPU-SP red | 12/12 | same (injection red patches exercised on DP) |

Energy magnitude on the RZ driven/undriven pair:

| Case | Measured |
|---|---|
| RZ DP driven | `3.4942596e-6` J |
| RZ DP undriven | exactly `0` J |
| RZ SP driven | `3.4942591e-6` J |
| RZ SP undriven | exactly `0` J |

Cartesian regressions remain covered by the 0005 suite and were re-checked
against the public Cartesian SP J_ext binaries (29/29) after the RZ runner
geometry and precision contracts landed.

## Tests shipped with the patch

Consistent with 0005 (source and tests in one patch):

- Green RZ decks under `tests/jext/inputs/*_rz`
- Red suite: `tests/jext/run_jext_rz_red_tests.sh`, `tests/jext/inputs/red/*`,
  `tests/jext/red-patches/*`, shared `tests/jext/jext_contracts.sh`
- Runner updates in `tests/jext/run_jext_regression.sh` (basename geometry,
  DP/SP ulp budgets, deposited-contract hooks)

## Validation boundary

- **CPU-verified only.** There is no GPU evidence for the RZ path in this
  package.
- Decks must emit `warpx.n_rz_azimuthal_modes = 2` (or higher). A legacy `M=1`
  configuration is deliberately abort-closed rather than silently zeroing the
  drive.
- Diagnostics still report combined `J_particle + J_ext`; source-work
  accounting must use the known analytic source.
- Boundary PML remains a deck-level antenna-to-PML standoff contract.
