# AdaptiveCpp Metal JIT guard RSS limits

Patch:
[`patches/adaptivecpp/0024-metal-jit-guard-rss-limits.patch`](../patches/adaptivecpp/0024-metal-jit-guard-rss-limits.patch)

## What it does and why

Expands the thin 0008 JIT compile mutex into the measurement tree's
process-aware guard: attribute MTLCompilerService RSS, poll metal/metallib
children, enforce a compiler memory ceiling, and fail closed when the offline
toolchain footprint exceeds the limit.

## Evidence and limits

Applies cleanly on AdaptiveCpp `4fe52accc3dca8640f2fbcc80dfd260a51fc2fd8`
after the preceding measurement-series patches. Acceptance gate:
`diff -r --exclude=.git` between (pin + full series) and the reconstructed
measurement TARGET is empty.

**Not compile-verified. Not runtime-verified.** The only claim this report
supports is byte-identical source reproduction of the measurement tree.
