# AdaptiveCpp Metal asynchronous error fail-closed state

Patch:
[`patches/adaptivecpp/0020-metal-async-error-failclosed.patch`](../patches/adaptivecpp/0020-metal-async-error-failclosed.patch)

## What it does and why

An earlier completion change removed the only writer of the queue's
`_async_error_state`. A command-buffer completion handler could therefore
observe a real failure while a later queue wait still reported success.

This patch adds a shared `metal_async_error_state` and records the first
asynchronous failure both there and in AdaptiveCpp's global async-error list.
The sticky state is threaded through kernel, pointer-translation, host-to-device
copy, and memset completion handlers so a later wait fails closed.

## Evidence and limits

The patch applies cleanly in the five-patch AdaptiveCpp series. Upstream
collaborator `lulzx` reported in commit
`62013310b1442bc871fd55feb4038afb49f9bdef` that pristine-stack inspection, a
forced one-millisecond timeout, a real wedge, and a 500/500 normal run behaved
as intended. This package retains no raw log for those runs, and this
remediation did not reproduce them independently.
