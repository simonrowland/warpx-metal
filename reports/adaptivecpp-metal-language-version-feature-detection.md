# AdaptiveCpp Metal language-version feature detection

Patch:
[`patches/adaptivecpp/0032-metal-language-version-feature-detection.patch`](../patches/adaptivecpp/0032-metal-language-version-feature-detection.patch)

## What it does and why

The package installs Apple's macOS 15 metal-cpp headers, whose newest declared
language enum is `MTL::LanguageVersion3_2`. Some build hosts have newer headers
that also declare `MTL::LanguageVersion4_0`. Patch `0026` unconditionally
referenced the newer declaration, breaking clean package builds.

Patch `0032` compiles a CMake probe against the exact `METAL_INCLUDE_DIR` chosen
for `rt-backend-metal`. A successful reference to the 4.0 enum creates the
target-private `ACPP_METAL_HAS_LANGUAGEVERSION_4_0` definition. The runtime JIT
path requests 4.0 only with that definition and otherwise requests 3.2.

The probe cache entry is cleared before each check, preventing a reused build
directory from retaining a result from different headers. The ahead-of-time
`.metallib` path is unchanged because it invokes Apple's `metal` compiler and
does not use this `MTL::CompileOptions` call.

## Evidence and limits

On an Apple Silicon build host, a clean public-main clone first installed Apple's
package-provided metal-cpp bundle. Its maximum declared enum was 3.2; the probe
failed as intended, printed `Metal runtime JIT language version: 3.2`, and the
AdaptiveCpp, standalone AMReX, and WarpX 3D builds all completed.

A separate configure of the same patched source against the host's newer
metal-cpp headers found `LanguageVersion4_0`, passed the probe, and printed
`Metal runtime JIT language version: 4.0`.

The clean-built WarpX 3D binary then completed a locked 1000-step
96×96×320/PPC1 run with exact 2,949,120-particle counts for both species. Its
log contained 42 `ACPP_METAL_AOT ... metallib-cache-load=hit` lines, confirming
the ahead-of-time fast path was compiled and used. Its measured 79.285 ms/step
median did not reproduce the package's 24.47 ms/step AOT reference; performance
root cause remains outside this compatibility patch.
