# AdaptiveCpp Metal timeout fail-closed state

Patch:
[`patches/adaptivecpp/0022-metal-timeout-failclosed.patch`](../patches/adaptivecpp/0022-metal-timeout-failclosed.patch)

## What it does and why

A timed-out command buffer could be removed from the queue's outstanding set
before the next wait. Without a sticky failure, that later wait could declare a
wedged queue healthy.

This patch latches failures at both timeout sites into the asynchronous error
state introduced by patch `0020`. Every later wait therefore continues to fail
instead of losing the timeout.

## Evidence and limits

The patch applies cleanly in the five-patch AdaptiveCpp series. Its retained
patch commentary reports that an upstream collaborator's forced
`ACPP_METAL_COMMAND_TIMEOUT_MS=1` run exited with status 1 at step zero. No raw
run log is retained, and this remediation performed only ordered application
and applied-path whitespace checks, not an independent Metal runtime test.
