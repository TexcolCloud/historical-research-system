FROM ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d AS uv
FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/research-cards
COPY services/runtime-support /app/runtime-support
COPY services/research-cards/pyproject.toml services/research-cards/uv.lock ./
COPY services/research-cards/src ./src
RUN uv sync --locked --python /usr/local/bin/python --no-dev
ENV PATH="/app/research-cards/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONUTF8=1
RUN mkdir -p /data
ENTRYPOINT ["research-cards"]
CMD ["serve"]
