"""Let tusd release its upload lock before removing any remaining S3 bytes."""

from urllib.parse import quote
from uuid import UUID

import httpx


def terminate_upload(settings, upload_id, session_id):
    prefix = str(UUID(session_id)) + "/"
    if not upload_id.startswith(prefix):
        raise ValueError("Upload does not belong to this session")
    UUID(upload_id.removeprefix(prefix).split("+", 1)[0])
    response = httpx.delete(
        settings.tus_control_url.rstrip("/") + "/" + quote(upload_id, safe="/"),
        headers={"Tus-Resumable": "1.0.0"},
        timeout=30,
    )
    if response.status_code not in {204, 404, 410}:
        response.raise_for_status()
        raise RuntimeError("Upload termination was not acknowledged")
