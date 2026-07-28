# AMReX Metal PIC RNG and reduction compatibility

Patch:
[`patches/amrex-post/0004-metal-pic-rng-reduction-fixes.patch`](../patches/amrex-post/0004-metal-pic-rng-reduction-fixes.patch)

## What it does and why

This 13-file compatibility payload selects AdaptiveCpp SYCL atomic paths,
routes RNG calls through compatible device engines, uses a
single-precision parser executor, and adjusts dense bins, no-filter particle
copies, and particle-locator storage for the Metal port. It also selects the
two-word scan status on Metal because the packed form requires general 64-bit
atomics that Metal does not provide.

These changes supply the AMReX primitives needed by WarpX PIC workloads without
silently relying on unsupported atomic, parser, RNG, or particle-container
behavior.

## Evidence and limits

The replacement files followed by AMReX-post patches `0004` and `0005` apply
cleanly to pinned AMReX revision
`fa795322b44fff24fef3a795c3b00d24e015ee42`; the applied source passes
`git diff --check`. Local CI compiles the combined host CPU path but does not
execute the Metal-only branches. M4 Pro stack results were reported by upstream
collaborator `lulzx`; no patch-specific GPU receipt is retained and no new GPU
verification was performed here.
