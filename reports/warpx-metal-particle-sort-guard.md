# WarpX Metal particle-sort guard

Patch:
[`patches/warpx/0004-metal-disable-particle-sort-override.patch`](../patches/warpx/0004-metal-disable-particle-sort-override.patch)

## What it does and why

For AdaptiveCpp/HipSYCL GPU+SYCL builds, this patch forces
`warpx.sort_intervals=-1` and emits a high-priority warning if the user requests
sorting. It is a conservative particle-attribute-corruption guard; it does not
enable or validate sorting.

Historical symptom notes in
[`docs/known-issues.md`](../docs/known-issues.md) record a three-quarter particle
loss at step 4 with sorting enabled and no loss when sorting was disabled.
Later atomic work supported a short sorting experiment, but the delivered
package retains the guard.

## Evidence and limits

The patch applies in the six-patch WarpX order and is compiled by local CI.
That CPU build does not execute the GPU+SYCL conditional, and no
patch-specific GPU receipt is retained. This remediation therefore verifies
package application and compilation only, not Metal runtime behavior.
