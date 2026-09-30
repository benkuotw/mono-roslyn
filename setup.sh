#!/usr/bin/env bash
# Download the pinned Roslyn compiler and .NET Framework reference assemblies from NuGet.
# Usage: setup.sh [moniker ...]   (default: net46 net48; e.g. setup.sh net472 net481)
# Needs: mono-complete (mono, resgen), curl, python3.
set -euo pipefail

HOME_DIR=${MONO_ROSLYN_HOME:-$(cd "$(dirname "$0")" && pwd)}
PKGS=$HOME_DIR/pkgs
ROSLYN_VERSION=${ROSLYN_VERSION:-5.9.0}
REFASM_VERSION=${REFASM_VERSION:-1.0.3}
if [ $# -gt 0 ]; then MONIKERS=("$@"); else MONIKERS=(net46 net48); fi

for tool in mono resgen curl python3; do
    command -v "$tool" >/dev/null || { echo "error: $tool not found (apt install mono-complete curl python3)" >&2; exit 1; }
done

# fetch <package id> <version>: download a .nupkg and unzip it to $PKGS/<id>.<version>/
fetch() {
    local id=$1 ver=$2 dest=$PKGS/$1.$2
    [ -d "$dest" ] && { echo "have  $id $ver"; return; }
    mkdir -p "$PKGS"
    curl -sSfL -o "$dest.nupkg" "https://api.nuget.org/v3-flatcontainer/$id/$ver/$id.$ver.nupkg"
    python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$dest.nupkg" "$dest.tmp"
    mv "$dest.tmp" "$dest" && rm "$dest.nupkg"
    echo "got   $id $ver"
}

fetch microsoft.net.compilers.toolset "$ROSLYN_VERSION"
for m in "${MONIKERS[@]}"; do
    fetch "microsoft.netframework.referenceassemblies.$m" "$REFASM_VERSION"
done

# Smoke test: Mono's JIT runs Roslyn.
mono "$PKGS/microsoft.net.compilers.toolset.$ROSLYN_VERSION/tasks/net472/csc.exe" -version >/dev/null
echo "ok: Roslyn $(mono "$PKGS/microsoft.net.compilers.toolset.$ROSLYN_VERSION/tasks/net472/csc.exe" -version) runs on $(mono --version | head -1)"
