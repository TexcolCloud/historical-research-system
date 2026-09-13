FROM ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d AS uv
FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/document-retrieval
COPY services/runtime-support /app/runtime-support
COPY services/document-retrieval/pyproject.toml services/document-retrieval/uv.lock ./
COPY services/document-retrieval/src ./src
COPY services/document-retrieval/config/linux.example.toml ./config/linux.example.toml
RUN uv sync --locked --python /usr/local/bin/python --no-dev
ENV PATH="/app/document-retrieval/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONUTF8=1 OMP_NUM_THREADS=8 MKL_NUM_THREADS=8
RUN mkdir -p /data
ENTRYPOINT ["document-retrieval"]
CMD ["--config", "config/linux.example.toml", "serve"]
