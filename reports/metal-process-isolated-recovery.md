# Metal process-isolated checkpoint recovery

## Why recovery needs a process boundary

On affected macOS 26.5.x/M3 Ultra systems, Metal can leave a committed command
buffer in `scheduled` state indefinitely while `error == nil`. AdaptiveCpp now
bounds every relevant host wait, but a timeout cannot establish which GPU writes
ran. Replaying the operation or replacing its command queue inside the same
process could therefore duplicate partial particle or field updates.

`scripts/10-run-warpx-resilient.py` uses the last boundary at which correctness
is knowable: a WarpX process that exited successfully after finishing a
checkpoint.

## Recovery protocol

The supervisor divides the requested run into process generations:

1. Start WarpX with a generation-specific `max_step`.
2. Add a dedicated Full diagnostic in checkpoint format while preserving the
   input deck's existing diagnostics.
3. Require WarpX to exit successfully at the generation boundary.
4. Verify that `WarpXHeader` and `Level_0` exist.
5. Atomically write `.warpx-metal-complete.json` inside the checkpoint.
6. Start the next generation with `amr.restart` pointing to that verified
   checkpoint.

Only the external marker makes a checkpoint eligible for automatic resume.
Directories left by a failed or killed child are never promoted. Before retrying
the same generation, they are moved under the supervisor's `quarantine/`
directory for postmortem inspection.

On an AdaptiveCpp Metal completion timeout, the retry starts from the last
marked checkpoint in a new process. This recreates the Metal device, command
queues, shared events, allocations, and AdaptiveCpp runtime state. An external
`SIGTERM`/`SIGKILL` is also retryable, but does not justify CPU demotion.

When a live SYCL/Metal child stops producing log progress past the watchdog
deadline, the supervisor leaves it running and demotes immediately when a CPU
fallback is configured. This matters on systems where terminating or rebooting
after a wedge is itself unsafe. The abandoned child cannot corrupt a CPU
checkpoint: demoted generations write to a separate `chk-cpu*` namespace.

Ordinary nonzero exits that do not look like a Metal timeout are not retried.
This prevents an invalid input deck or deterministic physics failure from
becoming a restart loop; externally terminated children remain recoverable
within the configured retry bound.

## Usage

```bash
./scripts/10-run-warpx-resilient.py \
  --max-step 10000 \
  --chunk-steps 100 \
  --max-retries 3 \
  --command-timeout-seconds 600 \
  --startup-grace-seconds 600 \
  --stall-timeout-seconds 600 \
  --cpu-fallback-executable /path/to/warpx.3d.NOMPI.OMP.SP.PSP.EB \
  --build-identity-manifest /path/to/build-pair.json \
  --cpu-work-dir /path/to/preprovisioned-cpu-run \
  --work-dir /path/to/run \
  --checkpoint-prefix .warpx-metal-checkpoints/chk \
  /path/to/warpx.3d.NOMPI.SYCL.SP.PSP.EB \
  /path/to/inputs \
  -- \
  my_constants.PPC=4
```

The pair manifest is explicit and intended to be generated from reviewed build
artifacts:

```json
{
  "schema": "warpx-resilient-build-pair.v1",
  "primary": {
    "sha256": "<64 lowercase hex digits>",
    "backend": "SYCL",
    "field_precision": "SINGLE",
    "particle_precision": "SINGLE"
  },
  "cpu_fallback": {
    "sha256": "<64 lowercase hex digits>",
    "backend": "OMP",
    "field_precision": "SINGLE",
    "particle_precision": "SINGLE"
  }
}
```

The supervisor owns its checkpoint prefix. Use a distinct prefix if the input
deck already writes checkpoints. Existing diagnostic names are read from the
input file, including `FILE = ...` includes, and retained alongside the recovery
checkpoint diagnostic.

The CPU working directory must exist before launch, differ from the primary
working directory, and contain any relative input resources the deck needs.
Demoted Full diagnostics and reduced diagnostics are forced under that
directory even when the input deck supplied another path. This prevents a
late-progressing abandoned Metal process from overwriting the CPU run's
scientific outputs.

The supervisor automatically resumes the highest checkpoint carrying a valid
completion marker. If the latest marker is in the CPU namespace, a later
invocation remains demoted and resumes with the CPU binary. Logs for every
process generation and retry are stored under `.warpx-metal-supervisor/` in the
work directory.

Important controls:

- `--chunk-steps`: smaller values recreate Metal state more frequently and
  reduce lost work, but increase checkpoint and startup overhead.
- `--command-timeout-seconds`: sets `ACPP_METAL_COMMAND_TIMEOUT_MS` for each
  child. The default is 600 seconds.
- `--startup-grace-seconds`: permits 600 seconds of initial log silence by
  default. Keep this above the platform's measured cold-JIT envelope.
- `--stall-timeout-seconds`: detects a live child after this much time without
  log growth. The supervisor deliberately does not signal that child.
- `--max-retries`: bounds retries for one generation. Exhaustion exits with
  status 75.
- `--cpu-fallback-executable`: enables permanent demotion after every observed
  failure for a chunk has a GPU-wedge signature. Both primary and fallback
  executables must identify and embed matching WarpX SINGLE field and particle
  precision; the fallback must identify an OMP or NOACC backend.
- `--build-identity-manifest`: required with a fallback. A
  `warpx-resilient-build-pair.v1` JSON document records `sha256`, `backend`,
  `field_precision`, and `particle_precision` for `primary` and
  `cpu_fallback`. Both executable hashes are verified before any child starts.
- `--cpu-work-dir`: required with a fallback. CPU generations run there, Full
  diagnostics are redirected under `diagnostics/`, and reduced diagnostics
  under `reduced_diags/`. Pre-provision relative deck resources before launch.
- `--initial-restart`: imports a trusted pre-existing checkpoint; its directory
  name must end with the checkpoint step.
- `--no-resume`: ignores prior completion markers; use it with a fresh
  checkpoint prefix.

Relative paths used by primary generations retain their normal meaning
relative to `--work-dir`; after demotion they resolve relative to
`--cpu-work-dir`.

## Validation

The deterministic supervisor tests cover:

- following an included input file to preserve diagnostic names;
- a simulated scheduled-command-buffer timeout on the first attempt;
- retry from the last committed generation;
- atomic promotion of only successful checkpoints; and
- immediate failure without retry for a non-Metal error;
- live-SYCL stall demotion without signalling the abandoned child;
- checkpoint and scientific-output isolation when that child later writes;
- process-exit and log-growth races at the watchdog boundary;
- rejection of stale, quoted, and cross-line timeout text;
- durable CPU-demotion discovery on a second invocation; and
- fail-closed primary/fallback backend and precision identity checks.

No retained artifact demonstrates an end-to-end Metal checkpoint/restart run.
Phase-1 verification performed no GPU execution and used CPU backends only. It
proved backend-agnostic checkpoint continuity across the NOACC-SP → OMP-SP seam:
exact time and step continuity; all 2,048 particles with zero count or weight
delta; and `Ex`, `Ey`, `Ez`, `Bx`, `By`, and `Bz` agreement at zero absolute
and relative tolerance. Negative controls also held, including immediate
failure without demotion for an ordinary non-Metal error, completion of a
401-second silent healthy startup without demotion, and loud rejection of a
precision-mismatched checkpoint. The live wedge → demote → complete path is not
yet demonstrated.

This architecture does not repair Apple's driver. It converts the driver defect
into bounded lost work with a correctness-preserving restart, which is the
strongest recovery guarantee available to client code.

## CPU demotion: making the driver defect a performance event

Process-isolated restart assumes a new process escapes whatever wedged the old
one. That does not hold on every Metal system, and some systems cannot safely
reboot after a wedge. `--cpu-fallback-executable` supplies a terminating rung:
the CPU build has no Metal dependency, so it is an absorbing state.

AMReX checkpoints are backend independent when precision is unchanged. In an
independent SP validation, a NOACC generation wrote step 4 and an OMP build
resumed it with exact time (`1.17932719118736e-14`, delta 0), step number,
particle count (2048), field energy (`0.0509880520403385 J`), and field data
(`amrex_fcompare`: exact agreement) across the seam. An SP checkpoint presented
to a DP-particle build failed loudly before restart with AMReX's particle
precision mismatch error. The supervisor therefore requires SP build identity
on both binaries rather than permitting a precision change mid-run. The pair
manifest also prevents a renamed or drifted executable from silently changing
that reviewed identity.

### Demotion policy

1. Retry an exited primary generation only when it has a Metal timeout
   signature or was externally terminated.
2. Demote only when every observed failure for the chunk has a wedge signature:
   a Metal completion-timeout signature, or no log progress from a live
   executable identified as SYCL/Metal.
3. A live Metal stall demotes immediately. The process is left alone; the CPU
   writes to the isolated `chk-cpu*` prefix, runs in a distinct working
   directory, and receives forced diagnostic output paths inside that
   directory.
4. Ordinary nonzero exits fail immediately. External signals may retry but
   never cause demotion.
5. Demotion persists for the run and across supervisor invocations.

Log names carry the backend (`...-gpu-attempt-1.log`,
`...-cpu-attempt-1.log`), and the completion marker records the backend and
exact command that produced each checkpoint.

With a fallback configured, a wedge costs the current chunk and the GPU
speedup. It no longer requires GPU recovery, process termination, or reboot to
finish the run.
