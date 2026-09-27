# AgentDiff with both vendor CLIs, for running `agentdiff duel` somewhere
# that is not your laptop: a CI runner, a scheduled job, a sandbox host.
#
#   docker build -t agentdiff .
#   docker run --rm -e OPENAI_API_KEY -e ANTHROPIC_API_KEY \
#     -v "$PWD/task:/work/task:ro" -v "$PWD/out:/work/out" \
#     agentdiff duel --task task/task.json -o out
#
# Why a container matters here and not only for convenience: Claude Code
# runs its commands with no OS sandbox of its own, so the container is the
# sandbox. The agents work on copies of the workspace inside it, as an
# unprivileged user; keys arrive as environment variables at run time and
# are never baked into a layer.
#
# Build arguments (docs/PRODUCTION.md):
#   PYTHON_BASE, NODE_IMAGE  the two official images this is assembled from;
#                            override PYTHON_BASE to start from one that
#                            trusts a corporate proxy's certificate
#   APT_PACKAGES             what the agents get from Debian (git, for the
#                            diffs they read); empty skips apt entirely
#   CODEX_VERSION, CLAUDE_CODE_VERSION  pin the vendor CLIs
ARG PYTHON_BASE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_BASE}
ARG APT_PACKAGES="git procps"
ARG CODEX_VERSION=latest
ARG CLAUDE_CODE_VERSION=latest

# Node from the official image (same Debian release, so the same libc)
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN if [ -n "$APT_PACKAGES" ]; then \
      apt-get update && apt-get install -y --no-install-recommends $APT_PACKAGES \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# the vendor CLIs, pinned by build argument for reproducible images
RUN npm install -g "@openai/codex@${CODEX_VERSION}" "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

# AgentDiff itself, from this checkout, into its own environment
COPY . /src
RUN python -m venv /opt/agentdiff \
 && /opt/agentdiff/bin/python /src/web/build_blocks.py >/dev/null \
 && /opt/agentdiff/bin/pip install --no-cache-dir /src \
 && rm -rf /src
ENV PATH="/opt/agentdiff/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# an unprivileged user owns the work directory; nothing runs as root
RUN useradd --create-home --uid 10001 agent \
 && mkdir -p /work && chown agent:agent /work
USER agent
WORKDIR /work

# `duel --live` inside a container binds 0.0.0.0 so a port can be
# published; that needs --allow-remote, and every request then carries
# the token the command prints
EXPOSE 8765
ENTRYPOINT ["agentdiff"]
CMD ["--help"]
