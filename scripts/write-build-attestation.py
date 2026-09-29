#!/usr/bin/env python3
"""Write a derived build identity beside an installed binary set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


EXTERNALS = {
    "AdaptiveCpp": ("extern/AdaptiveCpp", ("patches/adaptivecpp",)),
    "AMReX": ("extern/amrex", ("patches/amrex", "patches/amrex-post")),
    "WarpX": ("extern/warpx", ("patches/warpx",)),
}

CONFIGURATION_KEYS = (
    "WARPX_METAL_DIMS",
    "WARPX_OPENPMD_BP",
    "WARPX_CPU_MPI",
    "WARPX_CPU_PRECISION",
    "WARPX_CPU_PARTICLE_PRECISION",
    "WARPX_CPU_DIMS",
    "WARPX_CPU_CXX",
    "WARPX_CPU_CC",
    "NPROC",
    "OMP_NUM_THREADS",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def configuration_record(root: Path) -> dict[str, object] | None:
    configured_path = os.environ.get("WARPX_BUILD_CONFIG")
    if not configured_path:
        return None
    path = Path(configured_path).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"build configuration file not found: {path}")
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise SystemExit(
            f"build configuration file must be inside the checkout: {path}"
        ) from error
    return {
        "path": relative.as_posix(),
        "sha256": sha256_file(path),
        "resolved_values": {
            name: os.environ[name]
            for name in CONFIGURATION_KEYS
            if name in os.environ
        },
    }


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def patch_inventory(
    root: Path,
    directories: tuple[str, ...],
    excluded: tuple[str, ...],
) -> tuple[list[dict[str, str]], str]:
    entries: list[dict[str, str]] = []
    for directory in directories:
        if directory in excluded:
            continue
        patch_dir = root / directory
        if not patch_dir.is_dir():
            continue
        for patch in sorted(path for path in patch_dir.iterdir() if path.is_file()):
            entries.append({
                "path": patch.relative_to(root).as_posix(),
                "sha256": sha256_file(patch),
            })
    serialized = "".join(f"{item['path']}\t{item['sha256']}\n" for item in entries).encode()
    return entries, sha256_bytes(serialized)


def source_diff(root: Path) -> bytes:
    tracked = subprocess.run(
        ("git", "-C", str(root), "diff", "HEAD", "--binary"),
        check=True,
        capture_output=True,
    ).stdout
    untracked = subprocess.run(
        ("git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"),
        check=True,
        capture_output=True,
    ).stdout
    digest = hashlib.sha256()
    digest.update(b"tracked-diff\0")
    digest.update(tracked)
    for relative in sorted(name for name in untracked.split(b"\0") if name):
        path = root / os.fsdecode(relative)
        digest.update(b"untracked\0")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.digest()


def source_record(
    root: Path,
    relative_source: str,
    patch_dirs: tuple[str, ...],
    excluded: tuple[str, ...],
) -> dict[str, object]:
    source = root / relative_source
    if not (source / ".git").exists():
        raise SystemExit(f"missing git source tree: {source}")
    patches, patch_digest = patch_inventory(root, patch_dirs, excluded)
    status = git_output(source, "status", "--porcelain=v1", "--untracked-files=all")
    return {
        "source_path": relative_source,
        "source_commit": git_output(source, "rev-parse", "HEAD"),
        "source_status": status.splitlines(),
        "source_diff_sha256": source_diff(source).hex(),
        "applied_inputs": patches,
        "patch_digest_sha256": patch_digest,
    }


def source_integrity(
    root: Path,
    names: list[str],
    excluded: tuple[str, ...],
) -> dict[str, object]:
    records = {
        name: source_record(root, *EXTERNALS[name], excluded)
        for name in names
    }
    return {
        "schema": "warpx-metal-source-integrity.v1",
        "sources": {
            name: {
                "source_commit": records[name]["source_commit"],
                "source_diff_sha256": records[name]["source_diff_sha256"],
                "applied_inputs": records[name]["applied_inputs"],
                "patch_digest_sha256": records[name]["patch_digest_sha256"],
            }
            for name in names
        },
    }


def cmake_cache_record(build_dir: Path) -> dict[str, object]:
    cache = build_dir / "CMakeCache.txt"
    if not cache.is_file():
        raise SystemExit(f"missing CMakeCache.txt: {cache}")
    definitions: dict[str, str] = {}
    for line in cache.read_text().splitlines():
        if not line or line.startswith("#") or ":" not in line or "=" not in line:
            continue
        key_type, value = line.split("=", 1)
        key, _ = key_type.split(":", 1)
        key_upper = key.upper()
        if (
            key_upper.startswith(("WARPX_", "AMREX_", "ACPP_", "HIPSYCL_"))
            or key_upper in {
                "CMAKE_BUILD_TYPE",
                "CMAKE_C_COMPILER",
                "CMAKE_CXX_COMPILER",
                "CMAKE_CXX_FLAGS",
                "CMAKE_EXE_LINKER_FLAGS",
                "CMAKE_GENERATOR",
                "CMAKE_OSX_SYSROOT",
            }
        ):
            definitions[key] = value
    forbidden = b"AMREX_SYCL_NO_MULTIPASS_SCAN"
    metadata_names = {"CMakeCache.txt", "build.ninja", "compile_commands.json", "flags.make"}
    for path in build_dir.rglob("*"):
        if path.is_file() and (path.name in metadata_names or path.suffix == ".ninja"):
            try:
                contents = path.read_bytes()
            except OSError as error:
                raise SystemExit(f"cannot read build metadata: {path}: {error}") from error
            if forbidden in contents:
                raise SystemExit(
                    "AMREX_SYCL_NO_MULTIPASS_SCAN must be absent from effective build flags: "
                    f"{path}"
                )
    compile_commands = build_dir / "compile_commands.json"
    if not compile_commands.is_file():
        raise SystemExit(f"missing compile_commands.json: {compile_commands}")
    try:
        commands = json.loads(compile_commands.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot parse compile_commands.json: {compile_commands}: {error}") from error
    effective: dict[str, set[str]] = {}
    definition_pattern = re.compile(r"(?:^|\s)-D([A-Za-z_][A-Za-z0-9_]*)(?:=([^\s]+))?")
    for command in commands:
        text = command.get("command")
        if text is None:
            text = " ".join(command.get("arguments", []))
        for name, value in definition_pattern.findall(text):
            effective.setdefault(name, set()).add(value or "1")
    if not effective:
        raise SystemExit(f"no effective compile definitions found: {compile_commands}")
    return {
        "sha256": sha256_file(cache),
        "definitions": definitions,
        "effective_compile_definitions": {
            name: sorted(values) for name, values in sorted(effective.items())
        },
    }


def macos_sdk_record(cmake: dict[str, object]) -> dict[str, str]:
    definitions = cmake.get("definitions", {})
    cached = definitions.get("CMAKE_OSX_SYSROOT") if isinstance(definitions, dict) else None
    if not cached:
        raise SystemExit("missing macOS SDK path in CMake cache")

    path = Path(str(cached)).expanduser().resolve()
    if not path.is_dir():
        raise SystemExit(f"resolved macOS SDK does not exist: {path}")
    configured = os.environ.get("WARPX_MACOS_SDK_RESOLVED")
    if configured and Path(configured).expanduser().resolve() != path:
        raise SystemExit(
            "macOS SDK environment and CMake cache disagree: "
            f"{Path(configured).expanduser().resolve()} != {path}"
        )
    settings_path = path / "SDKSettings.plist"
    if not settings_path.is_file():
        raise SystemExit(f"macOS SDK metadata not found: {settings_path}")
    try:
        with settings_path.open("rb") as stream:
            settings = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        raise SystemExit(f"cannot read macOS SDK metadata: {settings_path}: {error}") from error
    canonical_name = settings.get("CanonicalName")
    if not isinstance(canonical_name, str) or not canonical_name.startswith("macosx"):
        raise SystemExit(
            f"macOS SDK metadata is not for macOS (CanonicalName={canonical_name!r}): {path}"
        )
    version = settings.get("Version") or settings.get("ProductBuildVersion")
    if not version:
        raise SystemExit(f"macOS SDK metadata has no version: {settings_path}")
    return {"path": str(path), "version": str(version)}


def binary_inventory(binary_dir: Path, artifact_dirs: list[Path]) -> list[dict[str, str]]:
    binaries = []
    if not binary_dir.is_dir():
        raise SystemExit(f"missing artifact directory: {binary_dir}")
    for path in sorted(binary_dir.iterdir()):
        if path.name == "BUILD-ATTESTATION.json" or not path.is_file():
            continue
        if os.access(path, os.X_OK) or path.name.startswith("lib"):
            binaries.append({
                "name": f"{binary_dir.name}/{path.name}",
                "sha256": sha256_file(path),
            })
    for directory in artifact_dirs:
        if not directory.is_dir():
            raise SystemExit(f"missing artifact directory: {directory}")
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name == "BUILD-ATTESTATION.json":
                continue
            if path.name.startswith("lib") or path.suffix in {".a", ".dylib", ".so"}:
                binaries.append({
                    "name": f"{directory.name}/{path.relative_to(directory).as_posix()}",
                    "sha256": sha256_file(path),
                })
    return binaries


def require_artifacts(kind: str, artifacts: list[dict[str, str]]) -> None:
    names = [item["name"].split("/", 1)[-1] for item in artifacts]
    if not artifacts:
        raise SystemExit(f"no installed artifacts found for build kind: {kind}")
    if kind == "adaptivecpp" and "acpp" not in names:
        raise SystemExit("AdaptiveCpp attestation missing acpp")
    if kind == "adaptivecpp" and not any(
        item["name"].split("/")[-1].startswith("libllvm-to-metal")
        for item in artifacts
    ):
        raise SystemExit("AdaptiveCpp attestation missing Metal backend library artifacts")
    if kind == "amrex" and not any(name.startswith("libamrex") for name in names):
        raise SystemExit("AMReX attestation missing libamrex artifact")
    if kind.startswith("warpx-") and not any(name.startswith("warpx.") for name in names):
        raise SystemExit(f"WarpX attestation missing warpx executable for build kind: {kind}")


def verify_installed_attestation(
    root: Path,
    attestation_path: Path,
    names: list[str],
    excluded: tuple[str, ...],
    binary_dir: Path,
    artifact_dirs: list[Path],
) -> None:
    try:
        attestation = json.loads(attestation_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read installed attestation: {attestation_path}: {error}") from error
    if attestation.get("schema") != "warpx-metal-build-attestation.v1":
        raise SystemExit(f"unsupported installed attestation: {attestation_path}")
    current = {
        name: source_record(root, *EXTERNALS[name], excluded)
        for name in names
    }
    expected_externals = attestation.get("externals", {})
    for name, record in current.items():
        expected = expected_externals.get(name)
        if expected is None:
            raise SystemExit(f"installed attestation lacks external: {name}")
        keys = ("source_commit", "source_diff_sha256", "applied_inputs", "patch_digest_sha256")
        if {key: expected.get(key) for key in keys} != {key: record.get(key) for key in keys}:
            raise SystemExit(
                f"installed attestation provenance mismatch for {name}: {attestation_path}"
            )
    expected_configuration = attestation.get("configuration")
    if expected_configuration is not None:
        current_configuration = configuration_record(root)
        if current_configuration != expected_configuration:
            raise SystemExit(
                f"installed attestation configuration mismatch: {attestation_path}"
            )
    actual_artifacts = binary_inventory(binary_dir, artifact_dirs)
    require_artifacts("adaptivecpp", actual_artifacts)
    if actual_artifacts != attestation.get("artifacts"):
        raise SystemExit(f"installed attestation artifact mismatch: {attestation_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--binary-dir", type=Path)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--external", action="append", choices=tuple(EXTERNALS))
    parser.add_argument("--skip-input-dir", action="append", default=[])
    parser.add_argument("--artifact-dir", action="append", type=Path, default=[])
    parser.add_argument("--verify-attestation", type=Path)
    parser.add_argument("--source-integrity-file", type=Path)
    parser.add_argument("--record-source-integrity", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    destination = args.destination.resolve() if args.destination else None
    binary_dir = args.binary_dir.resolve() if args.binary_dir else None

    tools_dir = root / "studio" / "tools"
    if tools_dir.is_dir() and any(tools_dir.iterdir()):
        raise SystemExit(f"studio/tools must be empty in a build checkout: {tools_dir}")

    names = args.external or list(EXTERNALS)
    excluded = tuple(args.skip_input_dir)
    if args.record_source_integrity:
        if not args.source_integrity_file:
            raise SystemExit("--source-integrity-file is required with --record-source-integrity")
        integrity = source_integrity(root, names, excluded)
        destination = args.source_integrity_file.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(integrity, indent=2, sort_keys=True) + "\n")
        temporary.replace(destination)
        print(f"[OK] Source integrity baseline: {destination}")
        return
    if args.verify_attestation:
        if binary_dir is None:
            raise SystemExit("--binary-dir is required with --verify-attestation")
        verify_installed_attestation(
            root,
            args.verify_attestation.resolve(),
            names,
            excluded,
            binary_dir,
            [path.resolve() for path in args.artifact_dir],
        )
        print(f"[OK] Installed attestation verified: {args.verify_attestation}")
        return
    if binary_dir is None:
        raise SystemExit("--binary-dir is required when writing a build attestation")
    if destination is None:
        raise SystemExit("--destination is required when writing a build attestation")

    externals = {
        name: source_record(root, source, patch_dirs, excluded)
        for name in names
        for source, patch_dirs in (EXTERNALS[name],)
    }
    if args.source_integrity_file:
        expected = json.loads(args.source_integrity_file.read_text())
        actual = {
            name: {
                "source_commit": record["source_commit"],
                "source_diff_sha256": record["source_diff_sha256"],
                "applied_inputs": record["applied_inputs"],
                "patch_digest_sha256": record["patch_digest_sha256"],
            }
            for name, record in externals.items()
        }
        if expected.get("sources") != actual:
            raise SystemExit(
                f"external source integrity changed since patch application: {args.source_integrity_file}"
            )
    build_dir = args.build_dir.resolve() if args.build_dir else None
    if build_dir is None:
        raise SystemExit("--build-dir is required when writing a build attestation")
    artifacts = binary_inventory(binary_dir, [path.resolve() for path in args.artifact_dir])
    require_artifacts(args.kind, artifacts)
    cmake = cmake_cache_record(build_dir)
    attestation = {
        "schema": "warpx-metal-build-attestation.v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "build_kind": args.kind,
        "fork": {
            "commit": git_output(root, "rev-parse", "HEAD"),
            "branch": git_output(root, "rev-parse", "--abbrev-ref", "HEAD"),
            "status": git_output(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines(),
        },
        "binary_dir": str(binary_dir),
        "artifacts": artifacts,
        "cmake": cmake,
        "macos_sdk": macos_sdk_record(cmake),
        "externals": externals,
    }
    configuration = configuration_record(root)
    if configuration is not None:
        attestation["configuration"] = configuration
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(attestation, indent=2, sort_keys=True) + "\n")
    temporary.replace(destination)
    print(f"[OK] Build attestation: {destination}")


if __name__ == "__main__":
    main()
