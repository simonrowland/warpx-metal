# Metal: fold signal-only command buffers, with a dual-wait fence

**Status: research. Not packaged as a patch — see "Why this is not yet a patch" below.**

## What it does

Two changes that only make sense together:

1. **Fold signal-only command buffers (F→A).** `insert_event` no longer allocates a dedicated
   command buffer whose only job is to signal a shared event. Instead the returned event is bound
   to the most recently committed *work* command buffer, and a host `wait()` becomes
   `waitUntilCompleted()` on that buffer.
2. **A dual-wait fence,** which repairs a defect the first change introduces.

The motivation is that the runtime issues roughly 4.3 `insert_event` calls per kernel launch, and
each one previously cost a command buffer of its own.

## The defect in the folded form, and the fix

With signal-only buffers folded, `insert_event` bound the host event to the blit command buffer
alone. Device-to-host host staging, however, completes *later* — it publishes its own shared-event
value once the host-side copy is done. A host `wait()`, `is_complete()` or `query_status()` could
therefore return **true before the staging it was meant to cover had happened**.

The symptom was a loss of exactly one `max_grid_size` tile of particles
(983 040 of 1 048 576 on a 1024² two-species deck), intermittently, in runs that crossed tile
boundaries. Field diagnostics and the primary physics suite passed; only exact particle counts
caught it.

The fix keeps both conditions instead of either one:

```cpp
bool metal_node_event::is_complete() const {
  // When both a command buffer and a shared-event value are bound (the
  // pending-host-staging fence after D2H/H2H), BOTH must be complete.
  // Command-buffer-only early-true after a D2H blit was the tile-loss race.
  if (_handle.command_buffer) {
    auto st = _handle.command_buffer->status();
    bool cb_done = (st == MTL::CommandBufferStatusCompleted ||
                    st == MTL::CommandBufferStatusError);
    if (!cb_done) return false;
    if (!_handle.event) return true;
    return _handle.event->signaledValue() >= _handle.value;
  }
  if (!_handle.event) return true;
  return _handle.event->signaledValue() >= _handle.value;
}
```

`wait()` follows the same shape: `waitUntilCompleted()` on the bound buffer, then the shared-event
value when one is present. The constructor and destructor were also made null-safe and now retain
and release the command buffer, since it is held across the deferred-commit boundary.

The fence adds **no additional command buffers**, and the encoded wait on the host-published
device-to-host event is retained unchanged — removing that wait was a separate earlier experiment
that lost particles far more aggressively (5 of 6 trials).

## Validation

Base failure rate was measured **before** testing the fix, so that a clean post-fix run would mean
something:

| | |
|---|---|
| base rate, unfixed | **5/20 = 0.25**, Clopper–Pearson 95% CI [0.112, 0.469] |
| required clean runs | ⌈log 0.01 / log 0.75⌉ = **17** |
| post-fix | **20/20 exact** particle counts; P(passing by luck) = 0.75²⁰ ≈ 3.2 × 10⁻³ |

Timing, interleaved against the unmodified build, 1024² PPC1:

| arm | ms/step |
|---|---|
| unmodified | 56.63 |
| folded + fence | **50.47** (−6.16) |

The pre-fence measurement showed −10.7 ms/step. The fence giving roughly 3 ms of that back is
expected and was predicted in advance: part of the apparent gain was waits that returned early
without covering their work. **A wait removed is not evidence that the wait was unnecessary**, and
the timing delta alone would have flattered the change.

## Limitations

- Validated at **PPC1 only**. The PPC2 arm was voided when the *unmodified* build hit an unrelated
  stochastic stall mid-run, so the fence has not been exercised at higher particle density.
- **Not exercised in 3D at all.**
- A stochastic stall in the same command-buffer/event machinery is under separate investigation. It
  reproduces on the unmodified build, so it is not caused by these changes, but until it is
  understood these changes should not be assumed independent of it.

## ★ Why this is not yet a patch

The source this work was developed and validated against **is not the source this repository
builds.** Reconstructing the pinned AdaptiveCpp tree and applying every patch in
`patches/adaptivecpp/` yields a `metal_queue.cpp` that differs substantially from the tree the
measurements were taken on:

| symbol | pinned + this repo's patches | measured tree |
|---|---:|---:|
| `enable_offline_metallib` | **0** | 9 |
| `transaction_archive` | **0** | 6 |
| `selected_kernel_names` | **0** | 8 |
| `binary_configuration_id` | 7 | 18 |
| `archive_path` | 13 | 19 |
| `extend_hash` | 2 | 10 |
| `metal_queue.cpp` total lines | 1389 | **1557** |

The measured tree carries an **offline-metallib / binary-archive** implementation that this
repository does not package. Note also that no build script or install instruction here sets
`ACPP_METAL_ENABLE_OFFLINE_METALLIB` or `ACPP_METAL_ENABLE_BINARY_ARCHIVE`, so a build produced by
following the install instructions would not have that code and those variables would be inert.

Two consequences worth stating plainly:

1. The change described above cannot be packaged as a patch in its current form — it is expressed
   against a base this repository cannot produce, so it would not apply, and forcing it would
   produce something other than what was validated.
2. More generally, **performance figures measured on that tree should not be attributed to a build
   produced from these instructions** until the gap is closed. The patch-payload checksum in the
   install instructions verifies the patch set, not that the resulting source matches what was
   tested.

## What would close it

Land (or explicitly discard) the offline-metallib / binary-archive work so that the pinned source
plus this repository's patches reproduces the tree used for measurement; then rebase this change
onto that base and re-run the validation above, extended to PPC2 and to 3D.
