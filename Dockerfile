# Build image: Microsoft's Roslyn compiler on the Mono runtime, plus Microsoft's
# .NET Framework reference assemblies, all pinned. Multi-arch (amd64 and arm64).
#
#   docker build -t mono-roslyn .
#   mkdir -p ~/out    # must exist and be yours: dockerd would create a missing mount source as root
#   docker run --rm --network none -u $(id -u):$(id -g) \
#       -v $PWD/examples/DemoForm:/src:ro -v ~/out:/out mono-roslyn /src -o /out/DemoForm
FROM debian:trixie-slim

# mono-complete brings mono and resgen, plus libgdiplus and Mono's WinForms,
# which resgen needs to read bitmaps stored in .resx files.
RUN apt-get update \
 && apt-get install -y --no-install-recommends mono-complete python3 curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Build projects as an unprivileged user; the toolchain itself stays root-owned.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

ARG ROSLYN_VERSION=5.9.0
ARG REFASM_VERSION=1.0.3
ARG FRAMEWORKS="net46 net48"
ENV MONO_ROSLYN_HOME=/opt/mono-roslyn \
    ROSLYN_VERSION=${ROSLYN_VERSION} \
    REFASM_VERSION=${REFASM_VERSION} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/mono-roslyn
COPY --chown=root:root setup.sh ./
# Everything is downloaded at image build time; building a project needs no network.
RUN ./setup.sh ${FRAMEWORKS}
COPY --chown=root:root csproj.py mono-roslyn-build.py ./

# Mono writes caches under $HOME; /tmp is writable for any --user id.
ENV HOME=/tmp
WORKDIR /out
USER 10001:10001
ENTRYPOINT ["/opt/mono-roslyn/mono-roslyn-build.py"]
