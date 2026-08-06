# AdaptiveCpp Metal process helpers

Patch:
[`patches/adaptivecpp/0023-metal-process-utils.patch`](../patches/adaptivecpp/0023-metal-process-utils.patch)

## What it does and why

Adds `metal_process_utils.hpp`, which was present in the measurement tree
as an untracked header but never packaged. Offline metallib compilation and
the expanded JIT guard call these helpers for process-group and command-capture
work when spawning `metal`/`metallib`.

## Evidence and limits

Applies cleanly on AdaptiveCpp `4fe52accc3dca8640f2fbcc80dfd260a51fc2fd8`
after the preceding measurement-series patches. Acceptance gate:
`diff -r --exclude=.git` between (pin + full series) and the reconstructed
measurement TARGET is empty.

**Not compile-verified. Not runtime-verified.** The only claim this report
supports is byte-identical source reproduction of the measurement tree.
