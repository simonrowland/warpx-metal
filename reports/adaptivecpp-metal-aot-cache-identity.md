# AdaptiveCpp Metal AOT cache identity

Patch:
[`patches/adaptivecpp/0025-metal-aot-cache-identity.patch`](../patches/adaptivecpp/0025-metal-aot-cache-identity.patch)

## What it does and why

Adds `metal_aot_cache_identity.hpp` and its unit test so offline metallib
and binary-archive cache keys include toolchain and feature-flag identity, not
only the SSCP bitcode hash.

## Evidence and limits

Applies cleanly on AdaptiveCpp `4fe52accc3dca8640f2fbcc80dfd260a51fc2fd8`
after the preceding measurement-series patches. Acceptance gate:
`diff -r --exclude=.git` between (pin + full series) and the reconstructed
measurement TARGET is empty.

**Not compile-verified. Not runtime-verified.** The only claim this report
supports is byte-identical source reproduction of the measurement tree.
