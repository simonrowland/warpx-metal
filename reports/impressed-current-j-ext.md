# Impressed-current coupling for full-Maxwell drives

## Summary

A prescribed magnetic field cannot inject energy into charged particles:
the magnetic part of the Lorentz force is perpendicular to particle velocity.
Time-dependent antenna drives therefore enter the field solve as an impressed
current, so Ampère's law produces the electric field that performs work.

This patch adds parser-defined `J_ext` components to WarpX's ordinary explicit
Yee path while preserving the existing particle-current synchronization and
boundary operator.

Patch:
[`patches/warpx/0005-add-impressed-current-j-ext.patch`](../patches/warpx/0005-add-impressed-current-j-ext.patch)

## Coupling scheme

Each impressed-current component is evaluated at the electromagnetic
half-step, `t + dt/2`, directly on that component's native Yee staggering.
Coordinates are derived from the live `ixType()` of the current MultiFab, so
1D, 2D XZ, and 3D use the same allocation truth as the field solver.

The source is added inside `SyncCurrentAndRho`, after particle-current
filtering and `SumBoundary`, and before the existing single
`ApplyJfieldBoundary` pass:

```text
particle deposition
  -> filter and SumBoundary
  -> add J_ext at t + dt/2
  -> FillBoundaryAndSync
  -> one ApplyJfieldBoundary on J_particle + J_ext
```

Keeping one boundary application is load-bearing. The operator is not
idempotent near reflective boundaries, so applying it once to particle current
and again after source injection can double near-boundary current.

## Supported envelope

| Area | Contract |
|---|---|
| Geometry | Cartesian 1D, 2D XZ, and 3D |
| Field solve | Ordinary explicit Yee solver only |
| Mesh hierarchy | Level 0 only; AMR aborts |
| Grid | Staggered current only; collocated and hybrid grids abort |
| Deposition and centering | Vay deposition and current centering abort |
| Geometry extensions | RZ and embedded boundaries abort |
| Time integration | Implicit solvers abort |
| Domain motion | Moving windows abort |
| PML | In-domain PML aborts; boundary PML is allowed only with an antenna-to-PML standoff in the deck |
| Diagnostics | Existing `jx`, `jy`, and `jz` diagnostics contain the combined `J_particle + J_ext`; no separate source channel is added |
| Charge continuity | Decks must provide discrete-solenoidal `J_ext`; a sampled runtime Yee-divergence gate aborts when the normalized residual exceeds its limit |

The parser interface is
`warpx.Jx/Jy/Jz_external_grid_function(x,y,z,t)`. Reduced dimensions keep four
arguments: 2D XZ evaluates `(x,0,z,t)`, and 1D Z evaluates `(0,0,z,t)`.

## Validation

The CPU regression runner exercises the complete supported dimensional matrix:

| Configuration | Result | Coverage |
|---|---:|---|
| 1D CPU | 5 passed, 0 failed | injection plumbing, analytic oracle, divergence gate, energy pair, monopole abort |
| 2D CPU | 8 passed, 0 failed | curl-pair gate, feature-off identity, reflective-boundary cases |
| 3D CPU | 16 passed, 0 failed | both curl planes, periodic seam, energy pair, unsupported-mode aborts |

The divergence test is discriminating rather than self-consistent. Reintroducing
the previous inverted stagger produced a normalized residual of `0.03843` and
aborted; the corrected live staggering produced `5.99e-8` against a `1e-4`
runtime limit. The independent analytic oracle uses fixed half-offset tables
and hard assertions, rather than deriving its expected positions from the
injection kernel.

Feature-off reflective-boundary runs with no source keys and with
`J_ext = 0` produced content-identical plotfile field payloads. The driven
energy tests use a two-sided band around the analytic `0.127062 J` result and
require the undriven result to remain below `1e-12 J`; a doubled current, which
would produce four times the field energy, fails this gate.

Short-run GPU-to-CPU comparisons used different backend binaries and separate
run directories in every dimension. Field payload SHA-256 values were
identical at both early and late samples for 1D, 2D, and 3D. Additional GPU
self-check smokes passed the 2D and 3D curl-pair cases, including the 3D
`Jz`-collocation case.

## Review lineage

Six review rounds combined independent code and physics review, with at least
two independent passes at each implementation gate. Early rounds rejected
boundary ordering, divergence sampling, synchronization, and weak-test
assumptions. Later refutation rounds found the non-idempotent double-boundary
application and tests that could pass without proving their claims. The final
rounds verified the single-boundary hook, inversion sensitivity, independent
staggering oracle, feature-off bit identity, two-sided energy bound, and GPU
self-check coverage before the patch was packaged.

## Validation boundary

The GPU evidence covers short correctness smokes, not long-duration production
stability. Boundary PML remains a deck-level standoff contract, source-work
accounting must use the known analytic source because diagnostics report the
combined current, and every mode outside the table above remains fail-closed.
