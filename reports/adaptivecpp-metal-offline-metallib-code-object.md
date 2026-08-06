# AdaptiveCpp Metal offline metallib code object

Patch:
[`patches/adaptivecpp/0026-metal-offline-metallib-code-object.patch`](../patches/adaptivecpp/0026-metal-offline-metallib-code-object.patch)

## What it does and why

Implements the offline metallib compile path and binary-archive transaction
support in `metal_code_object`, plus kernel_cache feature-flag plumbing and
the offline-compile unit test. Gated by
`ACPP_METAL_ENABLE_OFFLINE_METALLIB` and `ACPP_METAL_ENABLE_BINARY_ARCHIVE`.

## Evidence and limits

Applies cleanly on AdaptiveCpp `4fe52accc3dca8640f2fbcc80dfd260a51fc2fd8`
after the preceding measurement-series patches. Acceptance gate:
`diff -r --exclude=.git` between (pin + full series) and the reconstructed
measurement TARGET is empty.

**Not compile-verified. Not runtime-verified.** The only claim this report
supports is byte-identical source reproduction of the measurement tree.
