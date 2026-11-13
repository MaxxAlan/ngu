# syntax=docker/dockerfile:1.7
# local-agents - image chay local agent (CLI + web board + MCP)
#
# Build:
#   docker build -t ghcr.io/maxxalan/ngu:latest .
#   docker build --build-arg LLAMA_BACKEND=vulkan -t ngu:vulkan .
#   docker build --build-arg LLAMA_BACKEND=cuda-12.8 -t ngu:cuda .
#
# Luu y: image KHONG chua model .gguf (vai GB).
# Tai model vao volume /app/models bang:
#   docker run --rm -v ngu-models:/app/models ghcr.io/maxxalan/ngu:latest \
#       python scripts/fetch_models.py --set all

ARG PYTHON_VERSION=3.11
ARG UV_VERSION=0.11.28
ARG LLAMA_TAG=b12329
ARG LLAMA_BACKEND=cpu

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS runtime

ARG LLAMA_TAG
ARG LLAMA_BACKEND

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    LA_HOST=0.0.0.0 \
    LA_PORT=8765 \
    LA_DATA_DIR=/app/data \
    LA_MODELS_DIR=/app/models \
    LA_LLAMA_DIR=/app/runtime/llama.cpp

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tar tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

# --- python deps (cache theo lock file) ---
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- app code ---
COPY app ./app
COPY static ./static
COPY scripts ./scripts
COPY .config ./.config

# --- llama.cpp runtime ---
RUN set -eux; \
    case "${LLAMA_BACKEND}" in \
      cpu)    asset="llama-${LLAMA_TAG}-bin-ubuntu-x64.tar.gz" ;; \
      vulkan) asset="llama-${LLAMA_TAG}-bin-ubuntu-vulkan-x64.tar.gz" ;; \
      cuda*)  asset="llama-${LLAMA_TAG}-bin-ubuntu-${LLAMA_BACKEND}-x64.tar.gz" ;; \
      *) echo "LLAMA_BACKEND khong hop le: ${LLAMA_BACKEND}" >&2; exit 1 ;; \
    esac; \
    mkdir -p /app/runtime/llama.cpp; \
    curl -fsSL -o /tmp/llama.tar.gz \
      "https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_TAG}/${asset}"; \
    tar -xzf /tmp/llama.tar.gz -C /app/runtime/llama.cpp --strip-components=1; \
    if echo "${LLAMA_BACKEND}" | grep -q '^cuda'; then \
      curl -fsSL -o /tmp/cudart.tar.gz \
        "https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_TAG}/cudart-llama-${LLAMA_TAG}-bin-ubuntu-${LLAMA_BACKEND}-x64.tar.gz"; \
      tar -xzf /tmp/cudart.tar.gz -C /app/runtime/llama.cpp --strip-components=1; \
      rm -f /tmp/cudart.tar.gz; \
    fi; \
    rm -f /tmp/llama.tar.gz; \
    /app/runtime/llama.cpp/llama-server --version

RUN mkdir -p /app/models /app/data \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin ngu \
    && chown -R ngu:ngu /app

USER ngu

EXPOSE 8765
VOLUME ["/app/models", "/app/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${LA_PORT}/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/.venv/bin/python", "-m", "app.cli", "serve"]
