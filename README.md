# mono-roslyn

[![ci](https://github.com/benkuotw/mono-roslyn/actions/workflows/ci.yml/badge.svg)](https://github.com/benkuotw/mono-roslyn/actions/workflows/ci.yml)

Build .NET Framework WinForms projects on Linux, in a container: Microsoft's **Roslyn** compiler running on the **Mono** runtime, compiling against Microsoft's own reference assemblies. The `.exe` runs on Windows 10/11 (x64 and arm64).

## Why it works

- **Compiling doesn't need Windows.** A compiler reads only the *metadata* in reference assemblies, and Microsoft publishes those on NuGet (`Microsoft.NETFramework.ReferenceAssemblies.net48`).
- **Mono only runs the compiler.** Mono's JIT executes `csc.exe`, which is itself IL. The output is IL too. On Windows, the .NET Framework CLR loads Windows' own `System.Windows.Forms`, never Mono's.
- **So the old problem was Mono's toolchain, not Linux.** Replace Mono's `mcs` with Roslyn and pass the same switches MSBuild's `Csc` task would, and the Linux build uses the same compiler and settings as a Visual Studio build.

## Quick start

```bash
docker build -t mono-roslyn .
mkdir -p ~/out
docker run --rm --network none -u $(id -u):$(id -g) \
    -v $PWD/examples/DemoForm:/src:ro -v ~/out:/out \
    mono-roslyn /src -o /out/DemoForm
```

`~/out/DemoForm/` then holds `DemoForm.exe`, `DemoForm.exe.config`, a portable `DemoForm.pdb`, and `build.rsp` (the exact compiler input).

- `--network none`: everything was downloaded when the image was built.
- `:ro`: the project is mounted read-only; nothing is written next to your sources.
- `-u $(id -u):$(id -g)`: output files are owned by you. Mount a directory that already exists: Docker creates a missing one as root.

The `DemoForm.exe` artifacts from CI (built on amd64 and arm64 runners) have been run on Windows x64 and Windows arm64.

## Options

```
mono-roslyn <project_dir> [-c Release] [-o out] [-v]
```

| Option | Meaning |
|---|---|
| `-c`, `--configuration` | `Release` (default) or `Debug` |
| `-o`, `--out` | Output directory |
| `-v`, `--verbose` | Print the `resgen` and `csc` command lines |

## What it does

1. Reads the old-style `.csproj` ([csproj.py](csproj.py)): configuration conditions, `Compile`, `EmbeddedResource`, `Reference`, `App.config`.
2. Compiles each `.resx` with `resgen` and embeds it under the name MSBuild would give it (e.g. `DemoForm.MainForm.resources`). A wrong name makes WinForms silently find no resources.
3. Generates the `TargetFrameworkAttribute` MSBuild adds, so the runtime doesn't apply .NET 4.0 compatibility quirks.
4. Runs Roslyn with `/nostdlib` and only Microsoft's reference assemblies, plus MSBuild's defaults (`/subsystemversion:6.00`, `/highentropyva+`, `/langversion:7.3`).
5. Builds deterministically: `/deterministic` and `/pathmap` make the same source give a byte-identical `.exe` on any machine. CI checks this on amd64 and arm64.

## Platforms

`AnyCPU` (with `Prefer32Bit` off), `x64` and `ARM64`. `ARM64` needs .NET Framework 4.8.1. `x86` and `Prefer32Bit=true` are rejected, because the targets are 64-bit Windows 10/11.

Reference assemblies for `v4.6` and `v4.8` are in the image. Others can be added with `--build-arg FRAMEWORKS="net46 net48 net472"`.

## Limits

- Old-style (non-SDK) projects only. SDK-style projects can use `dotnet build`.
- No `ProjectReference`: build the library first and reference the dll with a `HintPath`.
- Culture-specific `.resx` files (satellite assemblies) are skipped with a warning.

## Background

This is a rebuild of the hardest stage of a release pipeline I ran for a manufacturing ERP: building its WinForms client on a Linux server, so a release was one command (Git pull → build on Linux → publish).
