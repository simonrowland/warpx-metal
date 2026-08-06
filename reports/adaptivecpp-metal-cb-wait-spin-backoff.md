# Metal: command-buffer wait granularity

Patch: `patches/adaptivecpp/0031-metal-cb-wait-spin-backoff.patch`

## What was wrong

`0019` gave `wait_for_metal_command_buffer()` a deadline, which it needed — without one a queue
that never retires hangs the process indefinitely. But it implemented the wait as a poll loop that
sleeps a flat 1 ms per iteration, which puts a **1 ms floor under every wait** whose command buffer
is not already complete.

That cost is invisible until the runtime is actually measured. On a 1024² two-species
single-precision deck the launch rate is about 205 kernels per step with roughly 3.1 waits per
kernel — about 640 waits per step reaching that loop. Measured against the same deck, host and
cache with only the runtime differing:

| runtime | ms/step |
|---|---|
| without the deadline machinery | 36.09 |
| with the flat 1 ms poll | ~328 |

A 9× wall regression. The arithmetic closes if a little under half of those waits sleep once:
640 × 0.46 × 1 ms ≈ 292 ms, against a measured difference of 292 ms.

## The fix

Match the wait granularity to how long Metal command buffers actually take to retire — tens of
microseconds on this hardware. The loop now yields up to 256 times, then falls back to exponential
backoff from 2 µs to a 1 ms cap so a genuinely stalled queue does not burn a core.

The deadline, the loop-exit conditions, the error-status branch, and both async-failure latch call
sites are unchanged. This patch changes only how the loop waits between status checks.

| runtime | ms/step |
|---|---|
| flat 1 ms poll | ~328 |
| backoff only (2 µs → 1 ms) | ~55 |
| **spin then backoff** | **~48** |
| without the deadline machinery | 36.09 |

Particle counts were exact on every run in every configuration.

## What remains

The residual against a runtime with no deadline machinery is wait granularity: about 12 ms per step
over ~640 waits is ~19 µs per wait. Removing it entirely needs an event-driven wait, and Metal
requires completion handlers to be registered **before** `commit()` while this function runs after
commit — so that is a change to the submit path, not to this function.

Raising the spin count to 1024 was measured at ~45.7 ms/step with a noticeably tighter spread. It
is not adopted here: more spinning is less friendly to a runtime that may be called from arbitrary
user threads, and the gain is small relative to that.

## Verification

- The deadline still fires correctly on a real stall: on a deck that reliably stalls around step
  46–51, the run aborted at a tuned 30 s deadline with `queue completion timed out … command
  buffer remained scheduled`, propagated as an async SYCL exception, exit status 1 — no hang.
- Reviewed independently against the null hypothesis that the change is wrong. Deadline integrity
  holds on all exit paths, with worst-case overshoot of one wait quantum — finer during the spin
  phase than the 1 ms poll it replaces. Both latch call sites are behaviourally identical.
- Applying the full `patches/adaptivecpp/` series to the pinned source reproduces the exact tree
  these measurements were taken on.
