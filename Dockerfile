# syntax=docker/dockerfile:1
FROM node:22-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npx vite build --outDir /app/static --emptyOutDir

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH \
    BENCHTRACE_HOME=/data
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra postgres --extra benchmarks
COPY src/ src/
COPY --from=web /app/static src/benchtrace/static
RUN uv sync --frozen --no-dev --extra postgres --extra benchmarks
RUN useradd --create-home --uid 1000 benchtrace && mkdir -p /data && chown benchtrace /data
USER benchtrace
VOLUME /data
EXPOSE 8321
CMD ["benchtrace", "serve", "--host", "0.0.0.0", "--workers", "0"]
