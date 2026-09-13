"""Explicit live tusd/S3 acceptance; creates one labelled technical book, no OCR."""

import base64
import hashlib
import json
import subprocess
import time

import httpx
from hrs_runtime.object_storage import S3Objects
from sqlalchemy import select

from hrs_platform import schema
from hrs_platform.database import engine_for
from hrs_platform.settings import Settings


def main():
    settings = Settings.load()
    content = b"%PDF-1.4\n% transport-only acceptance, not an OCR sample\n" + b"x" * (1024 * 1024)
    with httpx.Client(timeout=30) as client:
        response = client.post(
            "http://127.0.0.1:18170/api/v2/uploads",
            json={"filename": "基础设施续传验收.pdf", "byte_length": len(content)},
        )
        response.raise_for_status()
        session = response.json()
        headers = {"Tus-Resumable": "1.0.0", "Authorization": "Bearer " + session["token"]}
        metadata = base64.b64encode(session["session_id"].encode()).decode()
        response = client.post(
            "http://127.0.0.1:18171/uploads/",
            headers={
                **headers,
                "Upload-Length": str(len(content)),
                "Upload-Metadata": "session_id " + metadata,
            },
        )
        response.raise_for_status()
        location = response.headers["Location"]
        half = len(content) // 2
        response = client.patch(
            location,
            content=content[:half],
            headers={**headers, "Upload-Offset": "0", "Content-Type": "application/offset+octet-stream"},
        )
        response.raise_for_status()
        subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "deploy/platform/compose.yml",
                "restart",
                "tusd",
            ],
            cwd=settings.project_root,
            check=True,
            capture_output=True,
        )
        for attempt in range(30):
            try:
                response = client.head(location, headers=headers)
                response.raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        response.raise_for_status()
        assert int(response.headers["Upload-Offset"]) == half
        response = client.patch(
            location,
            content=content[half:],
            headers={
                **headers,
                "Upload-Offset": str(half),
                "Content-Type": "application/offset+octet-stream",
            },
        )
        response.raise_for_status()
        engine = engine_for(settings)
        try:
            for attempt in range(30):
                with engine.connect() as connection:
                    run = (
                        connection.execute(
                            select(schema.runs).where(schema.runs.c.book_id == session["book_id"])
                        )
                        .mappings()
                        .one_or_none()
                    )
                if run:
                    break
                time.sleep(0.5)
            assert run, "No durable completion receipt after tus upload"
            objects = S3Objects(
                endpoint=settings.s3_endpoint,
                bucket=settings.s3_bucket,
                access_key=settings.s3_access_key.get_secret_value(),
                secret_key=settings.s3_secret_key.get_secret_value(),
            )
            response = objects.client.get_object(Bucket=objects.bucket, Key=run["source"]["upload_key"])
            try:
                actual = response["Body"].read()
            finally:
                response["Body"].close()
            assert actual == content
            result = {
                "test": "tusd restart and S3 resume",
                "status": "passed",
                "book_id": session["book_id"],
                "run_id": run["id"],
                "byte_length": len(content),
                "recovered_offset": half,
                "sha256": hashlib.sha256(actual).hexdigest(),
                "scope": "transport only; not content approval",
            }
            output = settings.project_root / "output/refactor-v2/upload-resume.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result))
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
