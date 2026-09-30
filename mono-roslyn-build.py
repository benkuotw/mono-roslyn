#!/usr/bin/env python3
"""Build an old-style .NET Framework C# project on Linux: Microsoft's Roslyn on the Mono runtime.

Mono's JIT only runs the compiler. Roslyn compiles against Microsoft's own
reference assemblies (from NuGet), so the IL and metadata match what Visual
Studio would produce, and Windows loads its own WinForms at run time.

Usage: mono-roslyn-build.py <project_dir> [-c Release] [-o out] [-v]
Run setup.sh once first to download the pinned Roslyn and reference assemblies.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import csproj
from csproj import ProjectError

HOME = os.environ.get("MONO_ROSLYN_HOME", os.path.dirname(os.path.abspath(__file__)))
PKGS = os.path.join(HOME, "pkgs")
ROSLYN_VERSION = os.environ.get("ROSLYN_VERSION", "5.9.0")
REFASM_VERSION = os.environ.get("REFASM_VERSION", "1.0.3")


def parse_args():
    ap = argparse.ArgumentParser(description="Build a .NET Framework project with Roslyn on Mono.")
    ap.add_argument("project_dir", help="directory containing exactly one .csproj")
    ap.add_argument("-c", "--configuration", default="Release")
    ap.add_argument("-o", "--out", default="out", help="output directory (default: out)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print tool command lines")
    return ap.parse_args()


def run(cmd, verbose, **kw):
    if verbose:
        print("+ " + " ".join(cmd), file=sys.stderr)
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        raise ProjectError(f"{' '.join(os.path.basename(c) for c in cmd[:2])} failed (exit {r.returncode})")


def roslyn_csc():
    csc = os.path.join(PKGS, f"microsoft.net.compilers.toolset.{ROSLYN_VERSION}", "tasks", "net472", "csc.exe")
    if not os.path.isfile(csc):
        raise ProjectError(f"Roslyn {ROSLYN_VERSION} not found at {csc}; run setup.sh")
    return csc


def reference_dir(tfv):
    """v4.8 -> .../microsoft.netframework.referenceassemblies.net48.1.0.3/build/.NETFramework/v4.8"""
    moniker = "net" + tfv.lstrip("v").replace(".", "")
    d = os.path.join(PKGS, f"microsoft.netframework.referenceassemblies.{moniker}.{REFASM_VERSION}",
                     "build", ".NETFramework", tfv)
    if not os.path.isdir(d):
        raise ProjectError(f"reference assemblies for {tfv} not found at {d}; run: setup.sh {moniker}")
    return d


def framework_display_name(ref_dir, tfv):
    try:
        name = ET.parse(os.path.join(ref_dir, "RedistList", "FrameworkList.xml")).getroot().get("Name")
    except (OSError, ET.ParseError):
        name = None
    return name or f".NET Framework {tfv.lstrip('v')}"


def resolve_references(proj, ref_dir):
    refs = [os.path.join(ref_dir, "mscorlib.dll")]
    for r in proj.references:
        if r.hint_path:
            refs.append(r.hint_path)
            continue
        dll = os.path.join(ref_dir, r.name + ".dll")
        if not os.path.isfile(dll):
            raise ProjectError(f"reference {r.name} is not a {proj.target_framework} framework assembly and has no HintPath")
        refs.append(dll)
    # Facades (System.Runtime etc.) are type forwarders; MSBuild adds them when a
    # reference needs them. Only the ones actually used end up in the output.
    refs += sorted(glob.glob(os.path.join(ref_dir, "Facades", "*.dll")))
    return list(dict.fromkeys(refs))


def prepare_out_dir(path):
    """Create the output directory and fail early with a clear message if it isn't writable."""
    out_dir = os.path.abspath(path)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except PermissionError:
        raise ProjectError(f"cannot create output directory {out_dir}: permission denied")
    if not os.access(out_dir, os.W_OK):
        raise ProjectError(
            f"output directory {out_dir} is not writable by uid {os.getuid()}. In Docker, a bind-mount "
            "source that doesn't exist yet is created by the daemon as root: mount an existing "
            "directory you own (e.g. -v ~/out:/out -o /out/<name>)")
    return out_dir


def compile_resources(proj, obj_dir, verbose):
    """Turn each .resx into a .resources file with Mono's resgen; return (path, manifest_name) pairs."""
    out = []
    for i, res in enumerate(proj.resources):
        if res.is_resx:
            dest = os.path.join(obj_dir, f"{i:03d}.{res.manifest_name}")
            # /useSourcePath resolves ResXFileRef paths (e.g. ..\Resources\x.png) relative to the .resx.
            run(["resgen", "/useSourcePath", "/compile", f"{res.path},{dest}"], verbose,
                stdout=None if verbose else subprocess.DEVNULL)
            out.append((dest, res.manifest_name))
        else:
            out.append((res.path, res.manifest_name))
    return out


def csc_options(proj, output, out_dir, obj, ref_dir, resources, sources):
    """The same switches MSBuild's Csc task passes for a .NET Framework 4.5+ project."""
    opts = ["/nostdlib+", "/nologo", "/utf8output",
            f"/target:{proj.target}", f"/platform:{proj.platform}", f"/out:{output}",
            f"/filealign:{proj.prop('FileAlignment') or '512'}",
            f"/warn:{proj.prop('WarningLevel') or '4'}",
            f"/langversion:{proj.prop('LangVersion') or '7.3'}",  # MSBuild's default for .NET Framework
            "/subsystemversion:6.00", "/highentropyva+",
            "/optimize" + ("+" if proj.flag("Optimize") else "-"),
            "/deterministic" + ("+" if proj.flag("Deterministic", True) else "-"),
            # Windows PDBs need a Windows-only native library; portable PDBs work everywhere.
            "/debug:portable" if proj.prop("DebugType").lower() not in ("", "none") else "/debug-",
            # Hide the build machine's paths so every build of the same source is byte-identical.
            f"/pathmap:{proj.dir}=/_/,{out_dir}=/_out/,{obj}=/_obj/"]
    if proj.defines():
        opts.append("/define:" + ";".join(proj.defines()))
    if proj.flag("AllowUnsafeBlocks"):
        opts.append("/unsafe+")
    if proj.flag("TreatWarningsAsErrors"):
        opts.append("/warnaserror+")
    if proj.prop("NoWarn"):
        opts.append("/nowarn:" + ",".join(w for w in re.split(r"[;,]", proj.prop("NoWarn")) if w))
    if proj.prop("StartupObject"):
        opts.append(f"/main:{proj.prop('StartupObject')}")
    if proj.optional_path("ApplicationIcon"):
        opts.append(f"/win32icon:{proj.optional_path('ApplicationIcon')}")
    if proj.optional_path("ApplicationManifest"):
        opts.append(f"/win32manifest:{proj.optional_path('ApplicationManifest')}")
    opts += [f"/reference:{r}" for r in resolve_references(proj, ref_dir)]
    opts += [f"/resource:{path},{name}" for path, name in resources]
    return opts + sources


def copy_runtime_files(proj, out_dir, output, verbose):
    """Copy App.config and CopyLocal references next to the built assembly."""
    if proj.app_config:
        shutil.copyfile(proj.app_config, output + ".config")
    for ref in proj.references:
        if ref.hint_path and ref.copy_local:
            shutil.copy2(ref.hint_path, out_dir)
            if verbose:
                print(f"copied {ref.hint_path}", file=sys.stderr)


def build():
    args = parse_args()
    proj = csproj.load(args.project_dir, args.configuration)
    tfv = proj.target_framework
    ref_dir = reference_dir(tfv)
    csc = roslyn_csc()
    out_dir = prepare_out_dir(args.out)
    output = os.path.join(out_dir, proj.assembly_name + proj.extension)

    with tempfile.TemporaryDirectory(prefix="mono-roslyn-") as obj:
        # MSBuild generates this attribute; without it the runtime treats the app
        # as targeting 4.0 and applies old compatibility quirks.
        tfm_attr = os.path.join(obj, "TargetFrameworkAttribute.cs")
        with open(tfm_attr, "w") as f:
            f.write("// <autogenerated />\nusing System;\nusing System.Reflection;\n"
                    f'[assembly: global::System.Runtime.Versioning.TargetFrameworkAttribute(".NETFramework,Version={tfv}", '
                    f'FrameworkDisplayName = "{framework_display_name(ref_dir, tfv)}")]\n')

        resources = compile_resources(proj, obj, args.verbose)
        opts = csc_options(proj, output, out_dir, obj, ref_dir, resources, proj.compile + [tfm_attr])

        # Keep the exact compiler input next to the output for inspection.
        rsp = os.path.join(out_dir, "build.rsp")
        with open(rsp, "w") as f:
            f.write("\n".join(f'"{o}"' if " " in o else o for o in opts) + "\n")
        # /noconfig is only honoured on the command line, not in a response file.
        run(["mono", csc, "/noconfig", f"@{rsp}"], args.verbose)

    copy_runtime_files(proj, out_dir, output, args.verbose)

    print(f"built {output}")
    print(f"  toolchain   Roslyn {ROSLYN_VERSION} on Mono, refs {os.path.relpath(ref_dir, PKGS)}")
    print(f"  framework   {tfv}  target {proj.target}  platform {proj.platform}  config {proj.configuration}")
    print(f"  sources     {len(proj.compile)}  resources {len(proj.resources)}  references {len(proj.references)}")
    for path, why in proj.skipped:
        print(f"  skipped     {path}: {why}")


if __name__ == "__main__":
    try:
        build()
    except ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
