"""S3 content-addressed business objects; local paths are temporary compute caches."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


class ObjectStorageError(RuntimeError):
    pass


def file_digest(path):
    digest = hashlib.sha256()
    length = 0
    with Path(path).open("rb") as stream:
        while part := stream.read(1024 * 1024):
            digest.update(part)
            length += len(part)
    return digest.hexdigest(), length


def object_reference(digest, length, media_type):
    return {
        "key": f"hrs/v2/objects/{digest[:2]}/{digest}",
        "sha256": digest,
        "byte_length": length,
        "media_type": media_type,
    }


@contextmanager
def file_snapshot(path, media_type=None):
    """Read a producer path once; the bounded snapshot owns hash and transfer bytes."""
    digest, length = hashlib.sha256(), 0
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as snapshot:
        with Path(path).open("rb") as source:
            while part := source.read(1024 * 1024):
                snapshot.write(part)
                digest.update(part)
                length += len(part)
        snapshot.seek(0)
        yield (
            snapshot,
            object_reference(
                digest.hexdigest(),
                length,
                media_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream",
            ),
        )


class S3Objects:
    """The official boto3 SDK owns transport, multipart transfer and network retries."""

    def __init__(self, *, endpoint, bucket, access_key, secret_key, region="us-east-1"):
        import boto3
        from botocore.config import Config

        if not all((endpoint, bucket, access_key, secret_key)):
            raise ObjectStorageError(
                "S3 configuration is incomplete; no local fallback is allowed."
            )
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 3, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )

    def check(self):
        self.client.head_bucket(Bucket=self.bucket)

    def put_file(self, path, media_type=None):
        with file_snapshot(path, media_type) as (snapshot, reference):
            return self._upload_snapshot(snapshot, reference)

    def _upload_snapshot(self, snapshot, reference):
        from boto3.s3.transfer import TransferConfig

        self.client.upload_fileobj(
            snapshot,
            self.bucket,
            reference["key"],
            ExtraArgs={
                "ContentType": reference["media_type"],
                "Metadata": {"sha256": reference["sha256"]},
            },
            Config=TransferConfig(multipart_threshold=8 * 1024 * 1024, max_concurrency=2),
        )
        self.verify(reference)
        return reference

    def put_bytes(self, content, media_type="application/json"):
        content = bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        reference = object_reference(digest, len(content), media_type)
        self.client.put_object(
            Bucket=self.bucket,
            Key=reference["key"],
            Body=content,
            ContentType=media_type,
            Metadata={"sha256": digest},
        )
        self.verify(reference)
        return reference

    def _read(self, reference, consume):
        digest = reference["sha256"]
        if reference["key"] != f"hrs/v2/objects/{digest[:2]}/{digest}" or len(digest) != 64:
            raise ObjectStorageError("Invalid business object reference.")
        response = self.client.get_object(Bucket=self.bucket, Key=reference["key"])
        measured = hashlib.sha256()
        length = 0
        try:
            while part := response["Body"].read(1024 * 1024):
                measured.update(part)
                length += len(part)
                consume(part)
        finally:
            response["Body"].close()
        if measured.hexdigest() != digest or length != reference["byte_length"]:
            raise ObjectStorageError("S3 object failed the source hash or length check.")

    def verify(self, reference):
        self._read(reference, lambda part: None)

    def read_bytes(self, reference):
        if reference["byte_length"] > 128 * 1024 * 1024:
            raise ObjectStorageError("Large objects must be streamed to a compute cache.")
        parts = []
        self._read(reference, parts.append)
        return b"".join(parts)

    def materialize(self, reference, destination):
        destination = Path(destination)
        if destination.is_file() and file_digest(destination) == (
            reference["sha256"],
            reference["byte_length"],
        ):
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".s3-", dir=destination.parent)
        temporary = Path(name)
        try:
            with os.fdopen(handle, "wb") as stream:
                self._read(reference, stream.write)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
