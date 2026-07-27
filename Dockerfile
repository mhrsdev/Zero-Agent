# syntax=docker/dockerfile:1.7
#
# Zero runtime image.
#
# Two stages: dependencies are resolved from a pinned lock file in the builder,
# then copied into a slim runtime that carries no build toolchain. The image
# contains application code only - no credentials, no runtime state, no
# database. Everything mutable lives on a mounted volume.

FROM python:3.11.15-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
 && apt-get install --no-install-recommends -y build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes -r requirements.lock


FROM python:3.11.15-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    ZERO_HOME=/data \
    ZERO_CANONICAL_CONFIG=/data/config/zero.json

# Unprivileged runtime user. The image never runs as root.
RUN groupadd --gid 10001 zero \
 && useradd --uid 10001 --gid zero --home-dir /app --no-create-home --shell /usr/sbin/nologin zero

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=root:root zero/ ./zero/
COPY --chown=root:root scripts/ ./scripts/
COPY --chown=root:root panel/ ./panel/
COPY --chown=root:root config/ ./config/
COPY --chown=root:root pyproject.toml LICENSE NOTICE THIRD_PARTY_NOTICES.md ./

# Application code is read-only to the runtime user; only /data is writable.
RUN mkdir -p /data && chown zero:zero /data && chmod 700 /data

USER zero
VOLUME ["/data"]
EXPOSE 8787

# Fails the container when the panel stops answering, so the orchestrator can
# restart it instead of leaving a hung process in service.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=4).status==200 else 1)"

# exec form: PID 1 is python, so SIGTERM reaches it and shutdown is graceful.
ENTRYPOINT ["python", "-m", "zero"]
CMD ["status"]
