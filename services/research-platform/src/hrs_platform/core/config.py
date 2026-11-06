import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def load_environment(root: Path) -> dict[str, str]:
    """Resolve local defaults and derived URLs without changing files or process state."""
    env = {
        key: value
        for key, value in {**dotenv_values(Path(root) / ".env"), **os.environ}.items()
        if value is not None
    }
    for key, value in {
        "INGEST_S3_ENDPOINT_URL": "http://127.0.0.1:58333",
        "INGEST_S3_BUCKET": "ingestion-acceptance",
        "INGEST_S3_REGION": "us-east-1",
    }.items():
        env.setdefault(key, value)
    if env.get("INGEST_DATABASE_URL") or env.get("INGEST_DEV_DB_PASSWORD"):
        try:
            ingest = (
                make_url(env["INGEST_DATABASE_URL"])
                if env.get("INGEST_DATABASE_URL")
                else URL.create(
                    "postgresql+psycopg",
                    username="ingestion_owner",
                    password=env["INGEST_DEV_DB_PASSWORD"],
                    host=env.get("INGEST_DATABASE_HOST", "127.0.0.1"),
                    port=int(env.get("INGEST_DEV_DB_PORT", "55436")),
                    database="postgres",
                )
            )
        except (ArgumentError, ValueError):
            raise ValueError("Invalid PostgreSQL connection configuration.") from None
        env["INGEST_DATABASE_URL"] = ingest.render_as_string(hide_password=False)
        for key, database, host in (
            ("PLATFORM_DATABASE_URL", "historical_research_v2", ingest.host),
            ("PLATFORM_TEST_DATABASE_URL", "historical_research_v2_test", ingest.host),
            ("PLATFORM_DOCKER_DATABASE_URL", "historical_research_v2", "host.docker.internal"),
        ):
            if not env.get(key):
                env[key] = ingest.set(database=database, host=host).render_as_string(hide_password=False)
    return env


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    database_url: SecretStr
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: SecretStr
    s3_secret_key: SecretStr
    s3_region: str = "us-east-1"
    temporal_address: str = "127.0.0.1:17233"
    temporal_namespace: str = "historical-research"
    task_queue: str = "hrs-books-v2"
    gpu_queue: str = "hrs-gpu-v2"
    upload_endpoint: str = "/uploads/"
    tus_control_url: str = "http://127.0.0.1:18171/uploads/"
    upload_prefix: str = "hrs/v2/uploads/"
    upload_max_bytes: int = 256 * 1024 * 1024
    project_root: Path
    cache_root: Path
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    reasoning_model: str = "deepseek-flash"
    reading_model: str = "deepseek-flash"
    model_timeout_seconds: int = 600
    model_max_calls: int = Field(default=4096, ge=1)
    vision_max_calls: int = Field(default=4096, ge=1)
    reading_max_output: int = Field(default=16000, ge=1, le=384000)
    reasoning_max_output: int = Field(default=32000, ge=1, le=384000)
    model_max_output_ceiling: int = Field(default=64000, ge=1, le=384000)
    opensearch_url: str = "http://127.0.0.1:19260"
    opensearch_index: str = "hrs-platform-v2-chunks"
    retrieval_device: Literal["cpu", "cuda"] = "cuda"
    retrieval_endpoint: str = "http://127.0.0.1:18160/retrieval"
    auto_cards_enabled: bool = False
    processing_backend: Literal["codex", "legacy"] = "codex"
    codex_root: Path | None = None

    @model_validator(mode="after")
    def output_budgets(self):
        if max(self.reading_max_output, self.reasoning_max_output) > self.model_max_output_ceiling:
            raise ValueError("Initial output budgets must not exceed model_max_output_ceiling.")
        return self

    @classmethod
    def load(cls, root=None):
        root = Path(root or os.environ.get("HRS_PROJECT_ROOT", Path(__file__).resolve().parents[5])).resolve()
        env = load_environment(root)
        values = {
            name: env["PLATFORM_" + name.upper()]
            for name in cls.model_fields
            if "PLATFORM_" + name.upper() in env
        }
        for name, source in {
            "s3_endpoint": "INGEST_S3_ENDPOINT_URL",
            "s3_bucket": "INGEST_S3_BUCKET",
            "s3_access_key": "INGEST_S3_ACCESS_KEY",
            "s3_secret_key": "INGEST_S3_SECRET_KEY",
            "s3_region": "INGEST_S3_REGION",
            "deepseek_api_key": "DEEPSEEK_API_KEY",
            "deepseek_base_url": "DEEPSEEK_BASE_URL",
            "reasoning_model": "CARDS_REASONING_MODEL",
            "reading_model": "CARDS_READING_MODEL",
        }.items():
            if name not in values and env.get(source):
                values[name] = env[source]
        values.setdefault("project_root", root)
        values.setdefault("cache_root", root / ".cache" / "platform-compute")
        return cls.model_validate(values)
