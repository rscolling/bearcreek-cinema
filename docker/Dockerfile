# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base

# UID/GID match the `blueridge` host user on don-quixote (1000:1000, verified
# 2026-04-18) so bind mounts under /media/ are writable without permission
# gymnastics. Override at build time if the target host uses different ids:
#   docker build --build-arg AGENT_UID=1001 ...
ARG AGENT_UID=1000
ARG AGENT_GID=1000

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid ${AGENT_GID} agent \
 && useradd --uid ${AGENT_UID} --gid ${AGENT_GID} --shell /bin/bash --create-home agent \
 && mkdir -p /etc/archive-agent /var/lib/archive-agent \
 && chown -R agent:agent /var/lib/archive-agent

WORKDIR /opt/archive-agent

COPY --chown=agent:agent pyproject.toml README.md ./
COPY --chown=agent:agent src/ ./src/

RUN pip install --no-cache-dir -e .

USER agent
ENTRYPOINT ["archive-agent"]
CMD ["--help"]
