FROM ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d AS uv
FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/services/research-platform
COPY services/runtime-support /app/services/runtime-support
COPY services/research-platform/pyproject.toml services/research-platform/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --python /usr/local/bin/python --no-dev --extra retrieval --no-install-project
COPY services/research-platform/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --python /usr/local/bin/python --no-dev --extra retrieval
ENV PATH="/app/services/research-platform/.venv/bin:$PATH" HRS_PROJECT_ROOT=/app PLATFORM_CACHE_ROOT=/tmp/hrs-compute PYTHONUNBUFFERED=1 PYTHONUTF8=1 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
RUN groupadd --gid 10001 hrs && useradd --uid 10001 --gid hrs --no-create-home hrs
USER 10001:10001
ENTRYPOINT ["hrs-platform"]
CMD ["api", "--host", "0.0.0.0"]
