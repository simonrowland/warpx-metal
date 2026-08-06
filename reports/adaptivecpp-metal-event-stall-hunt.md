# AdaptiveCpp Metal event stall-hunt wait

Patch:
[`patches/adaptivecpp/0028-metal-event-stall-hunt.patch`](../patches/adaptivecpp/0028-metal-event-stall-hunt.patch)

## What it does and why

Matches the measurement tree's `metal_node_event::wait` path: a stall-hunt
trampoline added inside the existing wait loop, in addition to the
fixed-timeout fail-closed path from `0019`, which it leaves in place.

## Evidence and limits

Applies cleanly on AdaptiveCpp `4fe52accc3dca8640f2fbcc80dfd260a51fc2fd8`
after the preceding measurement-series patches. Acceptance gate:
`diff -r --exclude=.git` between (pin + full series) and the reconstructed
measurement TARGET is empty.

**Not compile-verified. Not runtime-verified.** The only claim this report
supports is byte-identical source reproduction of the measurement tree.
