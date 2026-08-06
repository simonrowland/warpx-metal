# AdaptiveCpp Metal queue offline path and diagnostics

Patch:
[`patches/adaptivecpp/0027-metal-queue-offline-metallib-diagnostics.patch`](../patches/adaptivecpp/0027-metal-queue-offline-metallib-diagnostics.patch)

## What it does and why

Brings `metal_queue.cpp` to the measurement tree: offline-metallib kernel
selection and load path, binary-archive use, prefer-offline fallback, and
command-buffer stall-hunt / watchdog diagnostics used during long campaigns.

## Evidence and limits

Applies cleanly on AdaptiveCpp `4fe52accc3dca8640f2fbcc80dfd260a51fc2fd8`
after the preceding measurement-series patches. Acceptance gate:
`diff -r --exclude=.git` between (pin + full series) and the reconstructed
measurement TARGET is empty.

**Not compile-verified. Not runtime-verified.** The only claim this report
supports is byte-identical source reproduction of the measurement tree.
