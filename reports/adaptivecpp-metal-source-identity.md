# AdaptiveCpp Metal source-identity backend payload

Patch:
[`patches/adaptivecpp/0008-metal-source-identity.patch`](../patches/adaptivecpp/0008-metal-source-identity.patch)

## What it does and why

This is the foundational AdaptiveCpp Metal compiler/runtime payload. Across 17
files it adds the pointer-translation passes, Metal source and metallib input
handling, the device translation table, serialized JIT compilation, and
explicit behavior for operations that would otherwise require unsupported
general 64-bit Metal atomics.

Together these facilities let AdaptiveCpp lower the AMReX/WarpX SSCP workload
to Metal Shading Language while preserving its address-space, pointer, JIT, and
runtime contracts. It is not merely an identity marker.

## Evidence and limits

The five AdaptiveCpp patches apply in lexical order to pinned revision
`3733a5655103a75c09d81e2ba6b519d15896551f`, and the touched applied paths pass
`git diff --check`. The stack-level M4 Pro results in
[`VALIDATION.md`](../VALIDATION.md) were reported by upstream collaborator
`lulzx`; no patch-specific raw receipt is retained here. This remediation did
not independently execute the Metal backend.
