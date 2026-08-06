# AdaptiveCpp durable filesystem atomic write

Patch:
[`patches/adaptivecpp/0030-filesystem-durable-atomic-write.patch`](../patches/adaptivecpp/0030-filesystem-durable-atomic-write.patch)

## What it does and why

Hardens atomic cache writes with process-id temp names, fsync of the temp
file, and fsync of the parent directory before rename so offline cache entries
cannot appear as truncated false hits after a crash.

## Evidence and limits

Applies cleanly on AdaptiveCpp `4fe52accc3dca8640f2fbcc80dfd260a51fc2fd8`
after the preceding measurement-series patches. Acceptance gate:
`diff -r --exclude=.git` between (pin + full series) and the reconstructed
measurement TARGET is empty.

**Not compile-verified. Not runtime-verified.** The only claim this report
supports is byte-identical source reproduction of the measurement tree.
