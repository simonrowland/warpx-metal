# AdaptiveCpp Metal emitter atomic codegen

Patch:
[`patches/adaptivecpp/0029-metal-emitter-atomic-codegen.patch`](../patches/adaptivecpp/0029-metal-emitter-atomic-codegen.patch)

## What it does and why

Completes MSL lowering for atomic fetch and compare-exchange patterns used
by SSCP kernels beyond what 0008 alone shipped.

## Evidence and limits

Applies cleanly on AdaptiveCpp `4fe52accc3dca8640f2fbcc80dfd260a51fc2fd8`
after the preceding measurement-series patches. Acceptance gate:
`diff -r --exclude=.git` between (pin + full series) and the reconstructed
measurement TARGET is empty.

**Not compile-verified. Not runtime-verified.** The only claim this report
supports is byte-identical source reproduction of the measurement tree.
