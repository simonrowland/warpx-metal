#!/usr/bin/env python3
"""Run WarpX in checkpointed process generations for Metal fault recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


MARKER_NAME = ".warpx-metal-complete.json"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 600
DEFAULT_STARTUP_GRACE_SECONDS = 600
DEFAULT_STALL_TIMEOUT_SECONDS = 600
SP_BUILD_MARKERS = (
    b"Precision:            | SINGLE",
    b"Particle precision:   | SINGLE",
)
METAL_TIMEOUT_LINE_PATTERNS = (
    re.compile(
        r"^metal_queue: [^\r\n]+ timed out after \d+ ms; "
        r"command buffer remained "
        r"(?:not enqueued|enqueued|committed|scheduled|completed|error|unknown)"
        r"$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^metal_node_event: timed out after \d+ ms waiting for "
        r"shared-event value \d+; current value is \d+$",
        re.IGNORECASE,
    ),
)
RESERVED_ARGUMENTS = (
    "max_step",
    "amr.restart",
    "diagnostics.diags_names",
)
RECOVERABLE_RETURN_CODES = (-signal.SIGTERM, -signal.SIGKILL)


@dataclass(frozen=True)
class Checkpoint:
    step: int
    path: Path
    backend: str


@dataclass(frozen=True)
class AttemptResult:
    returncode: int | None
    watchdog_fired: bool
    metal_timeout: bool
    live_pid: int | None


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def assignment_key(argument: str) -> str | None:
    if "=" not in argument:
        return None
    return argument.split("=", 1)[0].strip()


def has_single_precision_tag(path: Path) -> bool:
    return re.search(r"(^|[._-])SP([._-]|$)", path.name, re.IGNORECASE) is not None


def has_cpu_backend_tag(path: Path) -> bool:
    return (
        re.search(
            r"(^|[._-])(OMP|NOACC)([._-]|$)", path.name, re.IGNORECASE
        )
        is not None
    )


def has_metal_backend_tag(path: Path) -> bool:
    return (
        re.search(r"(^|[._-])(SYCL|METAL)([._-]|$)", path.name, re.IGNORECASE)
        is not None
    )


def has_single_precision_build_identity(path: Path) -> bool:
    binary = path.read_bytes()
    return all(marker in binary for marker in SP_BUILD_MARKERS)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_build_pair_identity(
    path: Path, primary: Path, cpu_fallback: Path
) -> tuple[str, str]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"cannot read build identity manifest {path}: {error}"
        ) from error
    if not isinstance(manifest, dict):
        raise ValueError("build identity manifest root must be a JSON object")
    if manifest.get("schema") != "warpx-resilient-build-pair.v1":
        raise ValueError(
            "build identity manifest schema must be "
            "warpx-resilient-build-pair.v1"
        )

    identities = (
        ("primary", primary, {"SYCL", "METAL", "OMP", "NOACC"}),
        ("cpu_fallback", cpu_fallback, {"OMP", "NOACC"}),
    )
    backends: list[str] = []
    for name, executable, allowed_backends in identities:
        identity = manifest.get(name)
        if not isinstance(identity, dict):
            raise ValueError(f"build identity manifest is missing {name}")
        backend = str(identity.get("backend", "")).upper()
        if backend not in allowed_backends:
            allowed = "/".join(sorted(allowed_backends))
            raise ValueError(f"{name} backend must be {allowed}, got {backend!r}")
        if (
            identity.get("field_precision") != "SINGLE"
            or identity.get("particle_precision") != "SINGLE"
        ):
            raise ValueError(
                f"{name} field_precision and particle_precision must both "
                "be SINGLE"
            )
        expected_hash = str(identity.get("sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(f"{name} sha256 must contain 64 hexadecimal digits")
        observed_hash = sha256_file(executable)
        if observed_hash != expected_hash:
            raise ValueError(
                f"{name} executable hash mismatch: expected {expected_hash}, "
                f"observed {observed_hash}"
            )
        backends.append(backend)
    return backends[0], backends[1]


def has_metal_timeout_signature(output: str) -> bool:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return False
    # Fail closed: only a canonical AdaptiveCpp timeout as the terminal log
    # record is demotion evidence. Quoted or stale timeout text cannot combine
    # with a later genuine deck error.
    return any(pattern.fullmatch(lines[-1]) for pattern in METAL_TIMEOUT_LINE_PATTERNS)


def strip_reserved_arguments(arguments: Iterable[str]) -> list[str]:
    return [
        argument
        for argument in arguments
        if argument != "--" and assignment_key(argument) not in RESERVED_ARGUMENTS
    ]


def _logical_lines(path: Path) -> Iterable[str]:
    pending = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        pending += (" " if pending else "") + line.rstrip("\\").strip()
        if line.endswith("\\"):
            continue
        yield pending
        pending = ""
    if pending:
        yield pending


def read_diagnostic_names(path: Path, visited: set[Path] | None = None) -> list[str]:
    """Read the last effective diagnostics.diags_names assignment.

    AMReX input decks can include other files with ``FILE = path``. This small
    parser follows those includes in encounter order, which is sufficient for
    preserving existing diagnostics when the supervisor adds its checkpoint.
    """

    resolved = path.resolve()
    visited = visited or set()
    if resolved in visited:
        return []
    visited.add(resolved)

    names: list[str] = []
    for line in _logical_lines(resolved):
        include_match = re.fullmatch(r"FILE\s*=\s*[\"']?([^\"']+?)[\"']?", line)
        if include_match:
            include_path = Path(include_match.group(1).strip())
            if not include_path.is_absolute():
                include_path = resolved.parent / include_path
            if include_path.is_file():
                included_names = read_diagnostic_names(include_path, visited)
                if included_names:
                    names = included_names
            continue

        assignment = re.fullmatch(
            r"diagnostics\.diags_names\s*=\s*(.*)", line
        )
        if assignment:
            names = assignment.group(1).split()
    return names


def read_parameter(
    path: Path,
    key: str,
    visited: set[Path] | None = None,
) -> str | None:
    resolved = path.resolve()
    visited = visited or set()
    if resolved in visited:
        return None
    visited.add(resolved)

    value: str | None = None
    for line in _logical_lines(resolved):
        include_match = re.fullmatch(r"FILE\s*=\s*[\"']?([^\"']+?)[\"']?", line)
        if include_match:
            include_path = Path(include_match.group(1).strip())
            if not include_path.is_absolute():
                include_path = resolved.parent / include_path
            if include_path.is_file():
                included_value = read_parameter(include_path, key, visited)
                if included_value is not None:
                    value = included_value
            continue

        assignment = re.fullmatch(rf"{re.escape(key)}\s*=\s*(.*)", line)
        if assignment:
            value = assignment.group(1).strip()
    return value


def checkpoint_path(prefix: Path, step: int, digits: int) -> Path:
    return prefix.parent / f"{prefix.name}{step:0{digits}d}"


def read_verified_checkpoints(
    prefix: Path, max_step: int, backend: str
) -> list[Checkpoint]:
    checkpoints: list[Checkpoint] = []
    if not prefix.parent.is_dir():
        return checkpoints

    pattern = re.compile(rf"^{re.escape(prefix.name)}(\d+)$")
    for candidate in prefix.parent.iterdir():
        match = pattern.match(candidate.name)
        marker = candidate / MARKER_NAME
        if not match or not marker.is_file():
            continue
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            step = int(metadata["step"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if step == int(match.group(1)) and step <= max_step:
            checkpoints.append(
                Checkpoint(
                    step=step,
                    path=candidate.resolve(),
                    backend=backend,
                )
            )
    return sorted(checkpoints, key=lambda checkpoint: checkpoint.step)


def verify_checkpoint(path: Path) -> None:
    required = (path / "WarpXHeader", path / "Level_0")
    missing = [str(item) for item in required if not item.exists()]
    if missing:
        raise RuntimeError(
            f"checkpoint {path} is incomplete; missing: {', '.join(missing)}"
        )


def mark_checkpoint(
    path: Path,
    step: int,
    previous: Path | None,
    command: list[str],
    backend: str,
) -> None:
    verify_checkpoint(path)
    metadata = {
        "step": step,
        "backend": backend,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "previous_checkpoint": str(previous) if previous else None,
        "command": command,
    }
    temporary = path / f"{MARKER_NAME}.tmp"
    temporary.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path / MARKER_NAME)


def quarantine_checkpoint(path: Path, state_dir: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"refusing to move checkpoint symlink: {path}")
    quarantine_dir = state_dir / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    destination = quarantine_dir / f"{path.name}.{time.time_ns()}"
    try:
        return Path(shutil.move(str(path), str(destination)))
    except OSError as error:
        raise RuntimeError(
            f"could not quarantine incomplete checkpoint {path}: {error}"
        ) from error


def terminate_process(process: subprocess.Popen[bytes], grace_seconds: int = 10) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_attempt(
    command: list[str],
    log_path: Path,
    environment: dict[str, str],
    startup_grace_seconds: int,
    stall_timeout_seconds: int,
    work_dir: Path,
) -> AttemptResult:
    watchdog_fired = False
    last_size = -1
    process_started = time.monotonic()
    last_progress = process_started
    saw_log_progress = False

    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            cwd=work_dir,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                time.sleep(1)
                # The process may have exited during the sleep. Check before
                # interpreting a quiet log as evidence of a live wedge.
                if process.poll() is not None:
                    break
                try:
                    size = log_path.stat().st_size
                except OSError:
                    size = last_size
                now = time.monotonic()
                if size > last_size:
                    last_size = size
                    if size > 0:
                        saw_log_progress = True
                        last_progress = now
                silence_limit = (
                    stall_timeout_seconds
                    if saw_log_progress
                    else startup_grace_seconds
                )
                silence_started = last_progress if saw_log_progress else process_started
                if (
                    silence_limit > 0
                    and now - silence_started >= silence_limit
                ):
                    if process.poll() is not None:
                        break
                    # Log growth may race the first stat at the deadline. A
                    # final stat turns that growth into a new progress epoch
                    # instead of abandoning a healthy process.
                    try:
                        boundary_size = log_path.stat().st_size
                    except OSError:
                        boundary_size = last_size
                    if boundary_size > last_size:
                        last_size = boundary_size
                        if boundary_size > 0:
                            saw_log_progress = True
                            last_progress = time.monotonic()
                        continue
                    # Close the process-exit race immediately before committing
                    # to the live-process classification.
                    if process.poll() is not None:
                        break
                    # A live, silent Metal process is the wedge evidence. Do not
                    # signal it: on the affected Macs it keeps the GPU wedged,
                    # but CPU work remains usable and rebooting can panic during
                    # Halt. The caller permanently demotes to CPU instead.
                    watchdog_fired = True
                    break
        except KeyboardInterrupt:
            os.killpg(process.pid, signal.SIGTERM)
            terminate_process(process)
            raise

    output = log_path.read_text(encoding="utf-8", errors="replace")
    metal_timeout = has_metal_timeout_signature(output)
    return AttemptResult(
        returncode=process.poll(),
        watchdog_fired=watchdog_fired,
        metal_timeout=metal_timeout,
        live_pid=process.pid if process.poll() is None else None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run WarpX in short process generations. Each successful generation "
            "commits a verified checkpoint; Metal timeouts restart from the last "
            "committed checkpoint in a fresh process."
        )
    )
    parser.add_argument("--max-step", required=True, type=positive_int)
    parser.add_argument("--chunk-steps", type=positive_int, default=100)
    parser.add_argument("--max-retries", type=nonnegative_int, default=3)
    parser.add_argument("--retry-backoff-seconds", type=nonnegative_int, default=5)
    parser.add_argument(
        "--command-timeout-seconds",
        type=positive_int,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--startup-grace-seconds",
        type=nonnegative_int,
        default=DEFAULT_STARTUP_GRACE_SECONDS,
        help=(
            "allow this many seconds before classifying a live child with no "
            "log output as wedged; 0 disables (default: 600)"
        ),
    )
    parser.add_argument(
        "--stall-timeout-seconds",
        type=nonnegative_int,
        default=DEFAULT_STALL_TIMEOUT_SECONDS,
        help=(
            "classify a live child as wedged after this many seconds without "
            "further log progress; the child is left running; 0 disables "
            "(default: 600)"
        ),
    )
    parser.add_argument("--work-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--checkpoint-prefix",
        type=Path,
        default=Path(".warpx-metal-checkpoints/chk"),
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(".warpx-metal-supervisor"),
    )
    parser.add_argument("--diagnostic-name", default="metal_recovery_chk")
    parser.add_argument("--checkpoint-digits", type=positive_int, default=10)
    parser.add_argument(
        "--initial-restart",
        type=Path,
        help="trusted WarpX checkpoint to use before supervisor-owned checkpoints",
    )
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--cpu-fallback-executable",
        type=Path,
        help=(
            "non-Metal WarpX binary to demote to when the GPU binary keeps "
            "wedging. Its filename must identify an SP build. AMReX "
            "checkpoints are backend independent, so the CPU-SP build resumes "
            "a GPU-written checkpoint directly without changing precision. "
            "Demotion is permanent for the remainder of the run."
        ),
    )
    parser.add_argument(
        "--build-identity-manifest",
        type=Path,
        help=(
            "required with --cpu-fallback-executable; hash-binds the primary "
            "and CPU binaries and records each backend plus SINGLE field and "
            "particle precision"
        ),
    )
    parser.add_argument(
        "--cpu-work-dir",
        type=Path,
        help=(
            "required with --cpu-fallback-executable; pre-provisioned working "
            "directory for demoted CPU generations, isolating their scientific "
            "outputs from the abandoned Metal process"
        ),
    )
    parser.add_argument(
        "--cpu-fallback-retries",
        type=nonnegative_int,
        default=1,
        help=(
            "extra attempts granted to the chunk that triggered demotion, "
            "using the CPU binary (default: 1)"
        ),
    )
    parser.add_argument("executable", type=Path)
    parser.add_argument("input_file", type=Path)
    parser.add_argument(
        "warpx_arguments",
        nargs=argparse.REMAINDER,
        help="additional WarpX assignments after an optional -- separator",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    work_dir = args.work_dir.resolve()
    executable = args.executable.resolve()
    input_file = args.input_file.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SystemExit(f"executable is missing or not executable: {executable}")
    if not input_file.is_file():
        raise SystemExit(f"input file does not exist: {input_file}")

    cpu_fallback = args.cpu_fallback_executable
    cpu_work_dir: Path | None = None
    primary_backend = "SYCL" if has_metal_backend_tag(executable) else "UNKNOWN"
    if cpu_fallback is not None:
        cpu_fallback = cpu_fallback.resolve()
        # Validate now rather than at demotion time: discovering a bad fallback
        # path only once the GPU has already wedged defeats its purpose.
        if not cpu_fallback.is_file() or not os.access(cpu_fallback, os.X_OK):
            raise SystemExit(
                f"cpu fallback is missing or not executable: {cpu_fallback}"
            )
        if cpu_fallback == executable:
            raise SystemExit(
                "cpu fallback must differ from the primary executable"
            )
        if not has_single_precision_tag(executable):
            raise SystemExit(
                "primary executable must be an SP build when CPU fallback is "
                f"enabled; filename needs an SP precision tag: {executable}"
            )
        if not has_single_precision_build_identity(executable):
            raise SystemExit(
                "primary executable does not embed WarpX SINGLE field and "
                f"particle precision build markers: {executable}"
            )
        if not has_single_precision_tag(cpu_fallback):
            raise SystemExit(
                "cpu fallback must be an SP build; executable filename needs "
                f"an SP precision tag: {cpu_fallback}"
            )
        if not has_cpu_backend_tag(cpu_fallback):
            raise SystemExit(
                "cpu fallback filename must identify an OMP or NOACC CPU "
                f"backend: {cpu_fallback}"
            )
        if not has_single_precision_build_identity(cpu_fallback):
            raise SystemExit(
                "cpu fallback does not embed WarpX SINGLE field and particle "
                f"precision build markers: {cpu_fallback}"
            )
        if args.build_identity_manifest is None:
            raise SystemExit(
                "--build-identity-manifest is required with "
                "--cpu-fallback-executable"
            )
        identity_manifest = args.build_identity_manifest
        if not identity_manifest.is_absolute():
            identity_manifest = work_dir / identity_manifest
        identity_manifest = identity_manifest.resolve()
        try:
            primary_backend, _ = load_build_pair_identity(
                identity_manifest, executable, cpu_fallback
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        if args.cpu_work_dir is None:
            raise SystemExit(
                "--cpu-work-dir is required with --cpu-fallback-executable"
            )
        cpu_work_dir = args.cpu_work_dir
        if not cpu_work_dir.is_absolute():
            cpu_work_dir = work_dir / cpu_work_dir
        cpu_work_dir = cpu_work_dir.resolve()
        if not cpu_work_dir.is_dir():
            raise SystemExit(
                f"cpu work directory does not exist: {cpu_work_dir}"
            )
        if cpu_work_dir == work_dir:
            raise SystemExit(
                "cpu work directory must differ from the primary work directory"
            )
    elif args.build_identity_manifest is not None:
        raise SystemExit(
            "--build-identity-manifest requires --cpu-fallback-executable"
        )
    elif args.cpu_work_dir is not None:
        raise SystemExit(
            "--cpu-work-dir requires --cpu-fallback-executable"
        )

    checkpoint_prefix = args.checkpoint_prefix
    if not checkpoint_prefix.is_absolute():
        checkpoint_prefix = work_dir / checkpoint_prefix
    checkpoint_prefix = checkpoint_prefix.resolve()
    cpu_checkpoint_prefix = checkpoint_prefix.parent / (
        f"{checkpoint_prefix.name}-cpu"
    )

    state_dir = args.state_dir
    if not state_dir.is_absolute():
        state_dir = work_dir / state_dir
    state_dir = state_dir.resolve()
    checkpoint_prefix.parent.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    diagnostic_names = read_diagnostic_names(input_file)
    reduced_diagnostic_names = (
        read_parameter(input_file, "warpx.reduced_diags_names") or ""
    ).split()
    for argument in args.warpx_arguments:
        if assignment_key(argument) == "diagnostics.diags_names":
            diagnostic_names = argument.split("=", 1)[1].split()
        if assignment_key(argument) == "warpx.reduced_diags_names":
            reduced_diagnostic_names = argument.split("=", 1)[1].split()
    if args.diagnostic_name not in diagnostic_names:
        diagnostic_names.append(args.diagnostic_name)

    forwarded_arguments = strip_reserved_arguments(args.warpx_arguments)
    verified: list[Checkpoint] = []
    if not args.no_resume:
        verified.extend(
            read_verified_checkpoints(
                checkpoint_prefix, args.max_step, "gpu"
            )
        )
        if cpu_fallback is not None:
            verified.extend(
                read_verified_checkpoints(
                    cpu_checkpoint_prefix, args.max_step, "cpu"
                )
            )
        # A CPU checkpoint at the same step wins: it proves a prior invocation
        # had already entered the absorbing demoted state.
        verified.sort(
            key=lambda checkpoint: (
                checkpoint.step,
                checkpoint.backend == "cpu",
            )
        )
    current = verified[-1].step if verified else 0
    previous = verified[-1].path if verified else None
    demoted = bool(verified and verified[-1].backend == "cpu")
    if not previous and args.initial_restart:
        initial_restart = args.initial_restart
        if not initial_restart.is_absolute():
            initial_restart = work_dir / initial_restart
        initial_restart = initial_restart.resolve()
        try:
            verify_checkpoint(initial_restart)
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
        step_match = re.search(r"(\d+)$", initial_restart.name)
        if not step_match:
            raise SystemExit(
                "cannot infer the step from --initial-restart; "
                "the checkpoint directory must end in digits"
            )
        current = int(step_match.group(1))
        previous = initial_restart
    elif not previous:
        input_restart = read_parameter(input_file, "amr.restart")
        if input_restart:
            raise SystemExit(
                "the input deck sets amr.restart; pass that checkpoint through "
                "--initial-restart so the supervisor can track its step"
            )

    print("=== WarpX Metal resilient runner ===")
    print(f"Executable:        {executable}")
    print(f"Input:             {input_file}")
    print(f"Work directory:    {work_dir}")
    print(f"Target step:       {args.max_step}")
    print(f"Process chunk:     {args.chunk_steps} steps")
    print(f"Checkpoint prefix: {checkpoint_prefix}")
    if cpu_fallback is not None:
        print(f"CPU checkpoint prefix: {cpu_checkpoint_prefix}")
        print(f"CPU work directory: {cpu_work_dir}")
    if previous:
        print(f"Resuming verified checkpoint: {previous} (step {current})")

    environment = os.environ.copy()
    environment["ACPP_METAL_COMMAND_TIMEOUT_MS"] = str(
        args.command_timeout_seconds * 1000
    )

    active_executable = cpu_fallback if demoted else executable
    active_work_dir = cpu_work_dir if demoted else work_dir
    active_checkpoint_prefix = (
        cpu_checkpoint_prefix if demoted else checkpoint_prefix
    )
    if demoted:
        print(
            "[demoted] latest verified checkpoint belongs to the CPU namespace; "
            "remaining on CPU"
        )

    while current < args.max_step:
        target = min(current + args.chunk_steps, args.max_step)

        # A chunk gets at most two passes: one on the primary binary and, if
        # that pass wedged the GPU, one more after demoting to the CPU binary.
        while True:
            expected_checkpoint = checkpoint_path(
                active_checkpoint_prefix, target, args.checkpoint_digits
            )
            attempts = (
                args.cpu_fallback_retries + 1
                if demoted
                else args.max_retries + 1
            )
            gpu_wedge_count = 0
            attempts_run = 0
            abandoned_live_process = False
            committed = False

            for attempt in range(1, attempts + 1):
                attempts_run = attempt
                if expected_checkpoint.exists():
                    # This path is dedicated to the supervisor. Without our
                    # marker, it is either stale or was left by an interrupted
                    # checkpoint.
                    if (expected_checkpoint / MARKER_NAME).exists():
                        print(
                            f"[fatal] verified checkpoint collision at "
                            f"{expected_checkpoint}; resume it or use a fresh "
                            f"prefix",
                            file=sys.stderr,
                        )
                        return 2
                    try:
                        quarantined = quarantine_checkpoint(
                            expected_checkpoint, state_dir
                        )
                    except RuntimeError as error:
                        print(f"[fatal] {error}", file=sys.stderr)
                        return 2
                    print(f"[quarantined] incomplete checkpoint: {quarantined}")

                command = [
                    str(active_executable),
                    str(input_file),
                    *forwarded_arguments,
                    f"max_step={target}",
                    f"diagnostics.diags_names={' '.join(diagnostic_names)}",
                    f"{args.diagnostic_name}.diag_type=Full",
                    f"{args.diagnostic_name}.format=checkpoint",
                    f"{args.diagnostic_name}.intervals={target}",
                    f"{args.diagnostic_name}.dump_last_timestep=1",
                    f"{args.diagnostic_name}.file_prefix="
                    f"{active_checkpoint_prefix}",
                    f"{args.diagnostic_name}.file_min_digits="
                    f"{args.checkpoint_digits}",
                    "warpx.verbose=1",
                ]
                if demoted:
                    assert cpu_work_dir is not None
                    for name in diagnostic_names:
                        command.append(
                            f"{name}.file_prefix="
                            f"{cpu_work_dir / 'diagnostics' / name}"
                        )
                    for name in reduced_diagnostic_names:
                        command.append(
                            f"{name}.path={cpu_work_dir / 'reduced_diags'}"
                        )
                    # The dedicated recovery checkpoint remains in its
                    # separately verified chk-cpu namespace.
                    command.append(
                        f"{args.diagnostic_name}.file_prefix="
                        f"{active_checkpoint_prefix}"
                    )
                if previous:
                    command.append(f"amr.restart={previous}")

                backend = "cpu" if demoted else "gpu"
                log_path = state_dir / (
                    f"step-{current:010d}-to-{target:010d}-"
                    f"{backend}-attempt-{attempt}.log"
                )
                print(
                    f"[chunk {current}->{target}] {backend} attempt "
                    f"{attempt}/{attempts}; log: {log_path}"
                )
                result = run_attempt(
                    command,
                    log_path,
                    environment,
                    args.startup_grace_seconds,
                    args.stall_timeout_seconds,
                    active_work_dir,
                )

                running_metal_primary = (
                    not demoted and primary_backend in {"SYCL", "METAL"}
                )
                metal_timeout_wedge = (
                    result.metal_timeout and running_metal_primary
                )
                metal_live_stall = (
                    result.watchdog_fired and running_metal_primary
                )
                if (
                    result.returncode == 0
                    and not result.watchdog_fired
                    and not metal_timeout_wedge
                ):
                    try:
                        mark_checkpoint(
                            expected_checkpoint,
                            target,
                            previous,
                            command,
                            backend,
                        )
                    except RuntimeError as error:
                        print(f"[fatal] {error}", file=sys.stderr)
                        return 2
                    previous = expected_checkpoint.resolve()
                    current = target
                    committed = True
                    print(f"[committed] verified checkpoint: {previous}")
                    break

                recoverable_signal = (
                    result.returncode in RECOVERABLE_RETURN_CODES
                )
                recoverable = (
                    result.watchdog_fired
                    or metal_timeout_wedge
                    or recoverable_signal
                )
                # Only a wedged GPU justifies demotion. An external kill or a
                # deterministic WarpX failure would fail identically on the CPU
                # binary, and demoting on those would hide real bugs behind a
                # silent slowdown.
                if metal_timeout_wedge or metal_live_stall:
                    gpu_wedge_count += 1
                reason = (
                    f"live-process no-log-progress wedge; pid "
                    f"{result.live_pid} left running"
                    if metal_live_stall
                    else f"live-process stall on non-Metal executable; pid "
                    f"{result.live_pid} left running"
                    if result.watchdog_fired
                    else "Metal completion timeout"
                    if metal_timeout_wedge
                    else f"child terminated by signal {-result.returncode}"
                    if recoverable_signal
                    else f"child exit {result.returncode}"
                )
                print(f"[failed] {reason}", file=sys.stderr)
                if result.live_pid is not None:
                    # Retrying Metal while the first process still owns the
                    # wedged GPU cannot recover this hardware. Demote now.
                    abandoned_live_process = True
                    break
                if not recoverable:
                    print(
                        f"[fatal] non-recoverable WarpX failure; inspect "
                        f"{log_path}",
                        file=sys.stderr,
                    )
                    return result.returncode or 2
                if attempt == attempts:
                    break
                if args.retry_backoff_seconds:
                    time.sleep(args.retry_backoff_seconds)

            if committed:
                break

            all_observed_attempts_wedged = (
                attempts_run > 0 and gpu_wedge_count == attempts_run
            )
            if (
                cpu_fallback is not None
                and not demoted
                and all_observed_attempts_wedged
                and (attempts_run == attempts or abandoned_live_process)
            ):
                # The GPU wedged repeatedly on this chunk. The CPU build has no
                # Metal dependency, so it is an absorbing state: progress can
                # continue from the same verified checkpoint regardless of what
                # the driver is doing. Demotion is permanent for this run.
                demoted = True
                active_executable = cpu_fallback
                active_work_dir = cpu_work_dir
                active_checkpoint_prefix = cpu_checkpoint_prefix
                print(
                    f"[demoted] GPU wedged {attempts_run}x on chunk "
                    f"{current}->{target}; continuing on CPU binary "
                    f"{cpu_fallback}; checkpoints: {cpu_checkpoint_prefix}",
                    file=sys.stderr,
                )
                continue

            if abandoned_live_process:
                live_backend = "CPU" if demoted else primary_backend
                print(
                    f"[fatal] live {live_backend} "
                    "process cannot be retried safely",
                    file=sys.stderr,
                )
                return 75

            print(
                f"[fatal] exhausted {attempts} attempts for "
                f"chunk {current}->{target}"
                + ("" if cpu_fallback is None else " (already on CPU binary)"),
                file=sys.stderr,
            )
            return 75

    print(f"[complete] reached step {current}" + (" (demoted to CPU)" if demoted else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
