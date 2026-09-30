"""Minimal reader for old-style (non-SDK) .NET Framework .csproj files.

Used by mono-roslyn-build.py. It evaluates just enough MSBuild to build
typical WinForms projects: PropertyGroup conditions on $(Configuration) and
$(Platform), Compile / EmbeddedResource / Reference / None / Content items,
and the manifest resource naming rules of MSBuild's
CreateCSharpManifestResourceName.
"""

import glob
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import cached_property

NS = "{http://schemas.microsoft.com/developer/msbuild/2003}"


class ProjectError(Exception):
    pass


@dataclass
class Resource:
    path: str             # absolute path of the source file
    manifest_name: str    # name embedded in the assembly
    is_resx: bool


@dataclass
class Reference:
    name: str             # simple assembly name, e.g. System.Windows.Forms
    hint_path: str = ""   # absolute path when <HintPath> is given and exists
    copy_local: bool = False


@dataclass
class Project:
    path: str
    dir: str
    configuration: str
    props: dict
    compile: list = field(default_factory=list)
    resources: list = field(default_factory=list)
    references: list = field(default_factory=list)
    app_config: str = ""
    skipped: list = field(default_factory=list)   # (path, reason)

    def prop(self, name, default=""):
        return self.props.get(name.lower(), default)

    def flag(self, name, default=False):
        v = self.prop(name)
        return default if v == "" else v.strip().lower() == "true"

    @property
    def assembly_name(self):
        return self.prop("AssemblyName") or os.path.splitext(os.path.basename(self.path))[0]

    @property
    def target_framework(self):
        v = self.prop("TargetFrameworkVersion")
        if not v:
            raise ProjectError("TargetFrameworkVersion is missing")
        return v  # e.g. "v4.8"

    @property
    def target(self):
        kind = (self.prop("OutputType") or "Library").lower()
        return {"winexe": "winexe", "exe": "exe", "library": "library"}.get(kind) or _fail(
            f"unsupported OutputType {kind!r}")

    @property
    def extension(self):
        return ".dll" if self.target == "library" else ".exe"

    @cached_property
    def platform(self):
        """Return the csc/mcs /platform value. Windows 10/11 only: x64 or arm64 hosts."""
        pt = (self.prop("PlatformTarget") or "AnyCPU").lower()
        if pt == "x86" or pt == "anycpu32bitpreferred":
            raise ProjectError(f"PlatformTarget {pt} is 32-bit; only AnyCPU, x64 and ARM64 are supported")
        if pt == "anycpu" and self.flag("Prefer32Bit"):
            raise ProjectError("Prefer32Bit=true would run 32-bit; set it to false (only 64-bit targets are supported)")
        if pt == "arm64":
            warn("PlatformTarget ARM64 needs .NET Framework 4.8.1 on the target Windows 11 arm64 machine")
        if pt not in ("anycpu", "x64", "arm64"):
            raise ProjectError(f"unsupported PlatformTarget {pt!r}")
        return pt

    def defines(self):
        return [d.strip() for d in re.split(r"[;,]", self.prop("DefineConstants")) if d.strip()]

    def optional_path(self, name):
        v = self.prop(name)
        if not v:
            return ""
        p = _abspath(self.dir, v)
        if not os.path.isfile(p):
            raise ProjectError(f"{name} {v!r} not found")
        return p


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def _fail(msg):
    raise ProjectError(msg)


def _abspath(base, rel):
    return os.path.normpath(os.path.join(base, rel.replace("\\", "/")))


def find_csproj(project_dir):
    found = sorted(glob.glob(os.path.join(project_dir, "*.csproj")))
    if len(found) != 1:
        raise ProjectError(f"expected exactly one .csproj in {project_dir}, found {len(found)}")
    return found[0]


# --- MSBuild condition evaluation (just the common forms) -----------------

def _expand(text, props):
    return re.sub(r"\$\(([^)]+)\)", lambda m: props.get(m.group(1).lower(), ""), text)


def _condition(cond, props):
    if not cond or not cond.strip():
        return True
    expr = _expand(cond, props)
    # Split on and/or (MSBuild conditions in generated projects rarely nest).
    parts = re.split(r"\s+(and|or)\s+", expr, flags=re.I)
    result = _compare(parts[0])
    for op, term in zip(parts[1::2], parts[2::2]):
        result = (result and _compare(term)) if op.lower() == "and" else (result or _compare(term))
    return result


def _compare(term):
    term = term.strip().strip("()").strip()
    m = re.fullmatch(r"'([^']*)'\s*(==|!=)\s*'([^']*)'", term)
    if m:
        eq = m.group(1).strip().lower() == m.group(3).strip().lower()
        return eq if m.group(2) == "==" else not eq
    if re.match(r"!?\s*exists\(", term, re.I):
        return term.startswith("!")  # Exists('...Microsoft.Common.props') etc.: treat as absent
    raise ProjectError(f"cannot evaluate MSBuild condition: {term!r}")


# --- Manifest resource names (MSBuild CreateCSharpManifestResourceName) ---

_CULTURE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


def _culture_of(filename):
    """Form1.zh-TW.resx -> 'zh-TW'; Form1.resx -> ''."""
    stem = os.path.splitext(filename)[0]
    if "." in stem:
        last = stem.rsplit(".", 1)[1]
        if _CULTURE.match(last) and len(last) >= 2:
            return last
    return ""


def _first_class(cs_path):
    """Return 'Namespace.Class' of the first class in a C# file (MSBuild's rule for DependentUpon)."""
    with open(cs_path, encoding="utf-8-sig", errors="replace") as f:
        src = f.read()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    src = re.sub(r'@"(?:[^"]|"")*"|"(?:\\.|[^"\\])*"', '""', src)
    ns_stack, depth = [], 0
    for m in re.finditer(r"\bnamespace\s+([\w.]+)\s*([{;])|\bclass\s+(\w+)|[{}]", src):
        tok = m.group(0)
        if m.group(1):
            ns_stack.append((m.group(1), depth + (1 if m.group(2) == "{" else 0)))
            if m.group(2) == "{":
                depth += 1
        elif m.group(3):
            ns = ".".join(n for n, _ in ns_stack)
            return f"{ns}.{m.group(3)}" if ns else m.group(3)
        elif tok == "{":
            depth += 1
        else:
            depth -= 1
            while ns_stack and ns_stack[-1][1] > depth:
                ns_stack.pop()
    return ""


def _valid_identifier(part):
    """Approximate MSBuild's MakeValidEverettIdentifier for one folder name."""
    part = re.sub(r"[^\w]", "_", part)
    return "_" + part if part and part[0].isdigit() else part


def manifest_name(root_ns, rel_path, dependent_cs, logical_name):
    if logical_name:
        return logical_name
    filename = os.path.basename(rel_path)
    is_resx = filename.lower().endswith(".resx")
    culture = _culture_of(filename)
    if dependent_cs and is_resx:
        cls = _first_class(dependent_cs)
        if cls:
            return f"{cls}.{culture}.resources" if culture else f"{cls}.resources"
    folders = [_valid_identifier(p) for p in os.path.dirname(rel_path).split("/") if p]
    stem = os.path.splitext(filename)[0] if is_resx else filename
    name = ".".join(([root_ns] if root_ns else []) + folders + [stem])
    return name + ".resources" if is_resx else name


# --- Project loading ------------------------------------------------------

def load(project_dir, configuration="Release", platform="AnyCPU"):
    project_dir = os.path.abspath(project_dir)
    path = find_csproj(project_dir)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise ProjectError(f"{path}: {e}")
    if root.get("Sdk"):
        raise ProjectError("SDK-style projects are not supported (use dotnet build)")

    props = {"configuration": configuration, "platform": platform,
             "msbuildprojectdirectory": project_dir}
    for pg in root.iter(NS + "PropertyGroup"):
        if not _condition(pg.get("Condition"), props):
            continue
        for p in pg:
            name = p.tag.replace(NS, "").lower()
            if _condition(p.get("Condition"), props):
                props[name] = _expand((p.text or "").strip(), props)

    proj = Project(path=path, dir=project_dir, configuration=configuration, props=props)
    root_ns = proj.prop("RootNamespace")

    def items(tag):
        for ig in root.iter(NS + "ItemGroup"):
            if not _condition(ig.get("Condition"), props):
                continue
            for it in ig.findall(NS + tag):
                if not _condition(it.get("Condition"), props):
                    continue
                meta = {c.tag.replace(NS, "").lower(): (c.text or "").strip() for c in it}
                yield _expand(it.get("Include", ""), props), meta

    def files(include):
        rel = include.replace("\\", "/")
        if any(c in rel for c in "*?"):
            hits = sorted(glob.glob(os.path.join(project_dir, rel), recursive=True))
            return [(os.path.relpath(h, project_dir), h) for h in hits]
        full = _abspath(project_dir, rel)
        if not os.path.isfile(full):
            raise ProjectError(f"file listed in project not found: {include}")
        return [(rel, full)]

    for include, _ in items("Compile"):
        proj.compile += [full for _, full in files(include)]

    for include, meta in items("EmbeddedResource"):
        for rel, full in files(include):
            dep = meta.get("dependentupon", "")
            dep_path = _abspath(os.path.dirname(full), dep) if dep.lower().endswith(".cs") else ""
            if dep_path and not os.path.isfile(dep_path):
                raise ProjectError(f"{include}: DependentUpon {dep} not found")
            is_resx = rel.lower().endswith(".resx")
            if is_resx and _culture_of(os.path.basename(rel)):
                proj.skipped.append((rel, "culture-specific resx needs a satellite assembly (not supported)"))
                continue
            proj.resources.append(Resource(full, manifest_name(root_ns, rel, dep_path, meta.get("logicalname")), is_resx))

    for include, meta in items("Reference"):
        name = include.split(",")[0].strip()
        hint = meta.get("hintpath", "")
        hint_path = _abspath(project_dir, hint) if hint else ""
        if hint_path and not os.path.isfile(hint_path):
            raise ProjectError(f"reference {name}: HintPath {hint} not found")
        private = meta.get("private", "")
        copy_local = private.lower() == "true" if private else bool(hint_path)
        proj.references.append(Reference(name, hint_path, copy_local))

    for include, meta in items("ProjectReference"):
        raise ProjectError(f"ProjectReference {include} is not supported; build it first and reference the dll")

    for tag in ("None", "Content"):
        for include, _ in items(tag):
            if os.path.basename(include.replace("\\", "/")).lower() == "app.config":
                proj.app_config = _abspath(project_dir, include)

    if not proj.compile:
        raise ProjectError("project has no Compile items")
    return proj
