#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_PATH = ROOT / "scripts" / "10-run-warpx-resilient.py"


def load_supervisor():
    spec = importlib.util.spec_from_file_location(
        "warpx_metal_supervisor", SUPERVISOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SUPERVISOR = load_supervisor()


class WarpXMetalSupervisorTest(unittest.TestCase):
    def test_default_silence_grace_covers_measured_cold_jit(self) -> None:
        args = SUPERVISOR.build_parser().parse_args(
            ["--max-step", "1", "gpu", "inputs"]
        )
        self.assertGreaterEqual(args.command_timeout_seconds, 600)
        self.assertGreaterEqual(args.startup_grace_seconds, 600)
        self.assertGreaterEqual(args.stall_timeout_seconds, 600)
        self.assertLess(400, args.startup_grace_seconds)

    def test_reads_diagnostics_through_input_include(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "base.inputs").write_text(
                "diagnostics.diags_names = diag1 particles\n",
                encoding="utf-8",
            )
            (root / "main.inputs").write_text(
                "FILE = base.inputs\n",
                encoding="utf-8",
            )
            self.assertEqual(
                SUPERVISOR.read_diagnostic_names(root / "main.inputs"),
                ["diag1", "particles"],
            )

    def test_retries_timeout_and_commits_only_successful_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_file = root / "inputs"
            input_file.write_text(
                "diagnostics.diags_names = diag1\n", encoding="utf-8"
            )
            fake_warpx = root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB"
            fake_warpx.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import os
                    import pathlib
                    import signal
                    import sys

                    assignments = {
                        item.split("=", 1)[0]: item.split("=", 1)[1]
                        for item in sys.argv[1:]
                        if "=" in item
                    }
                    target = int(assignments["max_step"])
                    prefix = pathlib.Path(
                        assignments["metal_recovery_chk.file_prefix"]
                    )
                    digits = int(
                        assignments["metal_recovery_chk.file_min_digits"]
                    )
                    checkpoint = prefix.parent / f"{prefix.name}{target:0{digits}d}"
                    attempt = pathlib.Path.cwd() / f".attempt-{target}"
                    if target == 2 and not attempt.exists():
                        attempt.write_text("failed once")
                        checkpoint.mkdir(parents=True)
                        (checkpoint / "WarpXHeader").write_text("partial")
                        print(
                            "metal_queue: kernel timed out after 10 ms; "
                            "command buffer remained scheduled"
                        )
                        raise SystemExit(70)
                    if target == 4 and not attempt.exists():
                        attempt.write_text("terminated once")
                        os.kill(os.getpid(), signal.SIGTERM)

                    checkpoint.mkdir(parents=True)
                    (checkpoint / "WarpXHeader").write_text("header")
                    (checkpoint / "Level_0").mkdir()
                    print(f"STEP {target}")
                    """
                ),
                encoding="utf-8",
            )
            fake_warpx.chmod(0o755)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SUPERVISOR_PATH),
                    "--max-step",
                    "4",
                    "--chunk-steps",
                    "2",
                    "--max-retries",
                    "1",
                    "--retry-backoff-seconds",
                    "0",
                    "--stall-timeout-seconds",
                    "10",
                    "--work-dir",
                    str(root),
                    "--checkpoint-prefix",
                    "checkpoints/chk",
                    str(fake_warpx),
                    str(input_file),
                ],
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            checkpoint_two = root / "checkpoints" / "chk0000000002"
            checkpoint_four = root / "checkpoints" / "chk0000000004"
            marker_two = json.loads(
                (checkpoint_two / SUPERVISOR.MARKER_NAME).read_text(
                    encoding="utf-8"
                )
            )
            marker_four = json.loads(
                (checkpoint_four / SUPERVISOR.MARKER_NAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(marker_two["step"], 2)
            self.assertEqual(marker_four["step"], 4)
            self.assertEqual(
                Path(marker_four["previous_checkpoint"]), checkpoint_two.resolve()
            )
            self.assertEqual(
                len(list((root / ".warpx-metal-supervisor").glob("*.log"))),
                4,
            )
            self.assertEqual(
                len(
                    list(
                        (
                            root
                            / ".warpx-metal-supervisor"
                            / "quarantine"
                        ).iterdir()
                    )
                ),
                1,
            )

    def test_does_not_retry_non_metal_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_file = root / "inputs"
            input_file.write_text("", encoding="utf-8")
            fake_warpx = root / "fake-failure.py"
            fake_warpx.write_text(
                "#!/usr/bin/env python3\n"
                "print('invalid input deck')\n"
                "raise SystemExit(9)\n",
                encoding="utf-8",
            )
            fake_warpx.chmod(0o755)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SUPERVISOR_PATH),
                    "--max-step",
                    "2",
                    "--chunk-steps",
                    "2",
                    "--max-retries",
                    "3",
                    "--retry-backoff-seconds",
                    "0",
                    "--work-dir",
                    str(root),
                    str(fake_warpx),
                    str(input_file),
                ],
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 9)
            self.assertEqual(
                len(list((root / ".warpx-metal-supervisor").glob("*.log"))),
                1,
            )

    def test_deadline_exit_is_not_misclassified_as_live_watchdog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = {
                "clean": "raise SystemExit(0)",
                "error": "raise SystemExit(9)",
                "sigterm": "import os, signal; os.kill(os.getpid(), signal.SIGTERM)",
                "sigkill": "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
            }
            for name, ending in cases.items():
                with self.subTest(name=name):
                    command = [
                        sys.executable,
                        "-c",
                        f"import time; time.sleep(0.8); {ending}",
                    ]
                    result = SUPERVISOR.run_attempt(
                        command,
                        root / f"{name}.log",
                        os.environ.copy(),
                        1,
                        1,
                        root,
                    )
                    self.assertFalse(result.watchdog_fired)
                    self.assertIsNone(result.live_pid)

    def test_boundary_log_growth_resets_watchdog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log_path = root / "boundary-growth.log"
            original_stat = Path.stat
            stat_calls = 0

            def stale_once(path: Path, *args, **kwargs):
                nonlocal stat_calls
                if path == log_path and stat_calls == 0:
                    stat_calls += 1
                    actual = original_stat(path, *args, **kwargs)
                    return os.stat_result(
                        (
                            actual.st_mode,
                            actual.st_ino,
                            actual.st_dev,
                            actual.st_nlink,
                            actual.st_uid,
                            actual.st_gid,
                            0,
                            actual.st_atime,
                            actual.st_mtime,
                            actual.st_ctime,
                        )
                    )
                return original_stat(path, *args, **kwargs)

            command = [
                sys.executable,
                "-c",
                (
                    "import time; time.sleep(0.8); "
                    "print('progress', flush=True); time.sleep(0.6)"
                ),
            ]
            with mock.patch.object(Path, "stat", new=stale_once):
                result = SUPERVISOR.run_attempt(
                    command,
                    log_path,
                    os.environ.copy(),
                    1,
                    1,
                    root,
                )
            self.assertEqual(result.returncode, 0)
            self.assertFalse(result.watchdog_fired)
            self.assertIsNone(result.live_pid)

    def test_metal_timeout_signature_requires_one_exact_line(self) -> None:
        self.assertTrue(
            SUPERVISOR.has_metal_timeout_signature(
                "metal_queue: producer before device-to-host memcpy timed out "
                "after 120000 ms; command buffer remained committed"
            )
        )
        self.assertTrue(
            SUPERVISOR.has_metal_timeout_signature(
                "metal_node_event: timed out after 600000 ms waiting for "
                "shared-event value 7; current value is 6"
            )
        )
        self.assertFalse(
            SUPERVISOR.has_metal_timeout_signature(
                "metal_queue: ordinary initialization failure\n"
                "remote service timed out"
            )
        )
        self.assertFalse(
            SUPERVISOR.has_metal_timeout_signature(
                "metal_queue: old operation timed out after 600000 ms; "
                "command buffer remained committed\n"
                "fatal: invalid input deck"
            )
        )
        self.assertFalse(
            SUPERVISOR.has_metal_timeout_signature(
                "fatal: quoted 'metal_queue: operation timed out after "
                "600000 ms; command buffer remained committed'"
            )
        )


class CpuDemotionTest(unittest.TestCase):
    """The CPU build has no Metal dependency, so it is the absorbing state the
    run falls back to when the GPU keeps wedging."""

    WEDGING_GPU = textwrap.dedent(
        """\
        #!/usr/bin/env python3
        # Precision:            | SINGLE
        # Particle precision:   | SINGLE
        print(
            "metal_queue: producer before device-to-host memcpy timed out "
            "after 120000 ms; command buffer remained committed"
        )
        raise SystemExit(70)
        """
    )

    WORKING_CPU = textwrap.dedent(
        """\
        #!/usr/bin/env python3
        # Precision:            | SINGLE
        # Particle precision:   | SINGLE
        import pathlib
        import sys

        assignments = {
            item.split("=", 1)[0]: item.split("=", 1)[1]
            for item in sys.argv[1:]
            if "=" in item
        }
        target = int(assignments["max_step"])
        prefix = pathlib.Path(assignments["metal_recovery_chk.file_prefix"])
        digits = int(assignments["metal_recovery_chk.file_min_digits"])
        checkpoint = prefix.parent / f"{prefix.name}{target:0{digits}d}"
        checkpoint.mkdir(parents=True)
        (checkpoint / "WarpXHeader").write_text("header")
        (checkpoint / "Level_0").mkdir()
        pathlib.Path("scientific-output").write_text("cpu")
        print(f"STEP {target}")
        """
    )

    def _write(self, path: Path, body: str) -> Path:
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _write_identity(
        self,
        path: Path,
        primary: Path,
        cpu: Path,
        *,
        primary_backend: str = "SYCL",
        cpu_backend: str = "OMP",
        primary_hash: str | None = None,
        cpu_hash: str | None = None,
    ) -> Path:
        def digest(executable: Path) -> str:
            return hashlib.sha256(executable.read_bytes()).hexdigest()

        path.write_text(
            json.dumps(
                {
                    "schema": "warpx-resilient-build-pair.v1",
                    "primary": {
                        "sha256": primary_hash or digest(primary),
                        "backend": primary_backend,
                        "field_precision": "SINGLE",
                        "particle_precision": "SINGLE",
                    },
                    "cpu_fallback": {
                        "sha256": cpu_hash or digest(cpu),
                        "backend": cpu_backend,
                        "field_precision": "SINGLE",
                        "particle_precision": "SINGLE",
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def _run(
        self,
        root: Path,
        *extra: str,
        max_step: int = 2,
        primary_name: str = "warpx.2d.NOMPI.SYCL.SP.PSP.EB",
        auto_manifest: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        input_file = root / "inputs"
        input_file.write_text(
            "diagnostics.diags_names = diag1\n", encoding="utf-8"
        )
        options = list(extra)
        if (
            auto_manifest
            and "--cpu-fallback-executable" in options
            and "--build-identity-manifest" not in options
        ):
            fallback_index = options.index("--cpu-fallback-executable") + 1
            cpu = Path(options[fallback_index])
            primary = root / primary_name
            if primary.is_file() and cpu.is_file():
                primary_backend = (
                    "SYCL"
                    if "SYCL" in primary_name.upper()
                    else "NOACC"
                    if "NOACC" in primary_name.upper()
                    else "OMP"
                )
                manifest = self._write_identity(
                    root / "build-identity.json",
                    primary,
                    cpu,
                    primary_backend=primary_backend,
                    cpu_backend=(
                        "NOACC" if "NOACC" in cpu.name.upper() else "OMP"
                    ),
                )
                options.extend(["--build-identity-manifest", str(manifest)])
        if (
            auto_manifest
            and "--cpu-fallback-executable" in options
            and "--cpu-work-dir" not in options
        ):
            cpu_work_dir = root / "cpu-work"
            cpu_work_dir.mkdir(exist_ok=True)
            options.extend(["--cpu-work-dir", str(cpu_work_dir)])
        return subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR_PATH),
                "--max-step", str(max_step),
                "--chunk-steps", "2",
                "--max-retries", "1",
                "--retry-backoff-seconds", "0",
                "--stall-timeout-seconds", "10",
                "--work-dir", str(root),
                "--checkpoint-prefix", "checkpoints/chk",
                *options,
                str(root / primary_name),
                str(input_file),
            ],
            text=True,
            capture_output=True,
            timeout=60,
        )

    def test_demotes_to_cpu_after_persistent_metal_wedge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB", self.WEDGING_GPU
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )

            result = self._run(
                root,
                "--cpu-fallback-executable", str(cpu),
                "--cpu-fallback-retries", "0",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("[demoted]", result.stderr)

            checkpoint = root / "checkpoints" / "chk-cpu0000000002"
            marker = json.loads(
                (checkpoint / SUPERVISOR.MARKER_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(marker["step"], 2)
            # The committed checkpoint must have been produced by the CPU
            # binary, not merely attributed to it.
            self.assertEqual(Path(marker["command"][0]).resolve(), cpu.resolve())

            logs = sorted(
                p.name
                for p in (root / ".warpx-metal-supervisor").glob("*.log")
            )
            # Two wedged GPU attempts, then one CPU attempt.
            self.assertEqual(len(logs), 3, logs)
            self.assertEqual(sum("-gpu-" in name for name in logs), 2, logs)
            self.assertEqual(sum("-cpu-" in name for name in logs), 1, logs)

    def test_live_silent_wedge_is_left_running_and_demotes_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB",
                "#!/usr/bin/env python3\n"
                "# Precision:            | SINGLE\n"
                "# Particle precision:   | SINGLE\n"
                "import os, pathlib, time\n"
                "pathlib.Path('live-gpu.pid').write_text(str(os.getpid()))\n"
                "time.sleep(60)\n",
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )

            result = self._run(
                root,
                "--startup-grace-seconds", "1",
                "--cpu-fallback-executable", str(cpu),
                "--cpu-fallback-retries", "0",
            )
            pid = int((root / "live-gpu.pid").read_text(encoding="utf-8"))
            try:
                self.assertEqual(
                    result.returncode, 0, result.stderr + result.stdout
                )
                self.assertIn("[demoted] GPU wedged 1x", result.stderr)
                self.assertIn(f"pid {pid} left running", result.stderr)
                os.kill(pid, 0)
                logs = sorted(
                    p.name
                    for p in (root / ".warpx-metal-supervisor").glob("*.log")
                )
                self.assertEqual(sum("-gpu-" in name for name in logs), 1, logs)
                self.assertEqual(sum("-cpu-" in name for name in logs), 1, logs)
            finally:
                os.kill(pid, signal.SIGTERM)

    def test_abandoned_gpu_cannot_collide_with_cpu_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB",
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    # Precision:            | SINGLE
                    # Particle precision:   | SINGLE
                    import pathlib
                    import sys
                    import time

                    assignments = {
                        item.split("=", 1)[0]: item.split("=", 1)[1]
                        for item in sys.argv[1:]
                        if "=" in item
                    }
                    target = int(assignments["max_step"])
                    prefix = pathlib.Path(
                        assignments["metal_recovery_chk.file_prefix"]
                    )
                    digits = int(
                        assignments["metal_recovery_chk.file_min_digits"]
                    )
                    time.sleep(2)
                    checkpoint = (
                        prefix.parent / f"{prefix.name}{target:0{digits}d}"
                    )
                    checkpoint.mkdir(parents=True)
                    (checkpoint / "WarpXHeader").write_text("late gpu header")
                    (checkpoint / "Level_0").mkdir()
                    pathlib.Path("scientific-output").write_text("gpu")
                    pathlib.Path("late-gpu-finished").write_text("finished")
                    """
                ),
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )

            result = self._run(
                root,
                "--startup-grace-seconds", "1",
                "--cpu-fallback-executable", str(cpu),
                "--cpu-fallback-retries", "0",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            deadline = time.monotonic() + 5
            while (
                not (root / "late-gpu-finished").exists()
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)

            late_gpu = root / "checkpoints" / "chk0000000002"
            cpu_checkpoint = root / "checkpoints" / "chk-cpu0000000002"
            self.assertTrue(late_gpu.is_dir())
            self.assertFalse((late_gpu / SUPERVISOR.MARKER_NAME).exists())
            self.assertTrue(
                (cpu_checkpoint / SUPERVISOR.MARKER_NAME).is_file()
            )
            self.assertEqual(
                (cpu_checkpoint / "WarpXHeader").read_text(encoding="utf-8"),
                "header",
            )
            self.assertEqual(
                (root / "scientific-output").read_text(encoding="utf-8"),
                "gpu",
            )
            self.assertEqual(
                (
                    root / "cpu-work" / "scientific-output"
                ).read_text(encoding="utf-8"),
                "cpu",
            )
            marker = json.loads(
                (cpu_checkpoint / SUPERVISOR.MARKER_NAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn(
                f"diag1.file_prefix="
                f"{(root / 'cpu-work').resolve() / 'diagnostics' / 'diag1'}",
                marker["command"],
            )

    def test_restart_from_cpu_checkpoint_remains_demoted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB", self.WEDGING_GPU
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )
            options = (
                "--cpu-fallback-executable", str(cpu),
                "--cpu-fallback-retries", "0",
            )

            first = self._run(root, *options)
            second = self._run(root, *options, max_step=4)

            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            self.assertIn(
                "latest verified checkpoint belongs to the CPU namespace",
                second.stdout,
            )
            marker = json.loads(
                (
                    root
                    / "checkpoints"
                    / "chk-cpu0000000004"
                    / SUPERVISOR.MARKER_NAME
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(marker["backend"], "cpu")
            self.assertEqual(Path(marker["command"][0]).resolve(), cpu.resolve())
            logs = sorted(
                p.name
                for p in (root / ".warpx-metal-supervisor").glob("*.log")
            )
            self.assertEqual(sum("-gpu-" in name for name in logs), 2, logs)
            self.assertEqual(sum("-cpu-" in name for name in logs), 2, logs)

    def test_does_not_demote_when_failure_is_not_a_gpu_wedge(self) -> None:
        """An external SIGTERM is retryable but says nothing about the GPU.
        Demoting on it would hide real failures behind a silent slowdown."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB",
                "#!/usr/bin/env python3\n"
                "# Precision:            | SINGLE\n"
                "# Particle precision:   | SINGLE\n"
                "import os, signal\n"
                "os.kill(os.getpid(), signal.SIGTERM)\n",
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )

            result = self._run(
                root, "--cpu-fallback-executable", str(cpu)
            )
            self.assertEqual(result.returncode, 75)
            self.assertNotIn("[demoted]", result.stderr)

    def test_metal_text_from_non_metal_primary_does_not_demote(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary_name = "warpx.2d.NOMPI.NOACC.SP.PSP.EB"
            self._write(root / primary_name, self.WEDGING_GPU)
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )

            result = self._run(
                root,
                "--cpu-fallback-executable", str(cpu),
                primary_name=primary_name,
            )
            self.assertEqual(result.returncode, 70)
            self.assertNotIn("[demoted]", result.stderr)

    def test_rejects_dp_cpu_fallback_before_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB", self.WORKING_CPU
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.DP.PDP.EB", self.WORKING_CPU
            )

            result = self._run(
                root, "--cpu-fallback-executable", str(cpu)
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cpu fallback must be an SP build", result.stderr)
            self.assertFalse((root / ".warpx-metal-supervisor").exists())

    def test_rejects_dp_primary_with_sp_cpu_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary_name = "warpx.2d.NOMPI.SYCL.DP.PDP.EB"
            self._write(root / primary_name, self.WORKING_CPU)
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )

            result = self._run(
                root,
                "--cpu-fallback-executable", str(cpu),
                primary_name=primary_name,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("primary executable must be an SP build", result.stderr)
            self.assertFalse((root / ".warpx-metal-supervisor").exists())

    def test_rejects_sp_name_without_sp_build_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB", self.WORKING_CPU
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB",
                "#!/usr/bin/env python3\nraise SystemExit(0)\n",
            )

            result = self._run(
                root, "--cpu-fallback-executable", str(cpu)
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not embed WarpX SINGLE", result.stderr)
            self.assertFalse((root / ".warpx-metal-supervisor").exists())

    def test_rejects_sp_gpu_binary_as_cpu_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB", self.WORKING_CPU
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB.alt",
                self.WORKING_CPU,
            )

            result = self._run(
                root, "--cpu-fallback-executable", str(cpu)
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("OMP or NOACC CPU backend", result.stderr)
            self.assertFalse((root / ".warpx-metal-supervisor").exists())

    def test_rejects_unusable_cpu_fallback_before_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB", self.WORKING_CPU
            )

            result = self._run(
                root,
                "--cpu-fallback-executable", str(root / "missing.py"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cpu fallback is missing", result.stderr)
            # Must fail before any child runs, not at demotion time.
            self.assertFalse((root / ".warpx-metal-supervisor").exists())

    def test_requires_hash_bound_build_identity_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB", self.WEDGING_GPU
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )

            result = self._run(
                root,
                "--cpu-fallback-executable", str(cpu),
                auto_manifest=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--build-identity-manifest is required", result.stderr)
            self.assertFalse((root / ".warpx-metal-supervisor").exists())

    def test_rejects_build_identity_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB", self.WEDGING_GPU
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )
            manifest = self._write_identity(
                root / "build-identity.json",
                primary,
                cpu,
                cpu_hash="0" * 64,
            )

            result = self._run(
                root,
                "--cpu-fallback-executable", str(cpu),
                "--build-identity-manifest", str(manifest),
                auto_manifest=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cpu_fallback executable hash mismatch", result.stderr)
            self.assertFalse((root / ".warpx-metal-supervisor").exists())

    def test_requires_distinct_preprovisioned_cpu_work_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = self._write(
                root / "warpx.2d.NOMPI.SYCL.SP.PSP.EB", self.WEDGING_GPU
            )
            cpu = self._write(
                root / "warpx.2d.NOMPI.OMP.SP.PSP.EB", self.WORKING_CPU
            )
            manifest = self._write_identity(
                root / "build-identity.json", primary, cpu
            )

            missing = self._run(
                root,
                "--cpu-fallback-executable", str(cpu),
                "--build-identity-manifest", str(manifest),
                auto_manifest=False,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("--cpu-work-dir is required", missing.stderr)

            same = self._run(
                root,
                "--cpu-fallback-executable", str(cpu),
                "--build-identity-manifest", str(manifest),
                "--cpu-work-dir", str(root),
                auto_manifest=False,
            )
            self.assertNotEqual(same.returncode, 0)
            self.assertIn(
                "must differ from the primary work directory", same.stderr
            )
            self.assertFalse((root / ".warpx-metal-supervisor").exists())


if __name__ == "__main__":
    unittest.main()
