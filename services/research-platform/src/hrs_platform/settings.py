import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, SecretStr


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
    reasoning_model: str = "deepseek-v4-pro"
    reading_model: str = "deepseek-flash"
    model_timeout_seconds: int = 600
    model_max_calls: int = 512
    reading_max_output: int = 16000
    reasoning_max_output: int = 32000
    opensearch_url: str = "http://127.0.0.1:19260"
    opensearch_index: str = "hrs-platform-v2-chunks"
    retrieval_device: Literal["cpu", "cuda"] = "cuda"
    retrieval_endpoint: str = "http://127.0.0.1:18160/retrieval"
    auto_cards_enabled: bool = True

    @classmethod
    def load(cls, root=None):
        root = Path(root or os.environ.get("HRS_PROJECT_ROOT", Path(__file__).resolve().parents[4])).resolve()
        env = {**dotenv_values(root / ".env"), **os.environ}
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
