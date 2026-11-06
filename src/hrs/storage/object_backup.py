"""Portable S3 snapshots, verified independently of the original bucket."""

import hashlib
import json
import tempfile
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from botocore.exceptions import ClientError

from hrs.storage.objects import file_digest


def inventory(storage, prefix="hrs/v2/"):
    rows = {}
    for page in storage.client.get_paginator("list_objects_v2").paginate(
        Bucket=storage.bucket, Prefix=prefix
    ):
        for item in page.get("Contents", []):
            rows[item["Key"]] = {"size": item["Size"], "etag": item.get("ETag")}
    return rows


def require_empty_target(storage, prefix="hrs/v2/", *, create=False):
    try:
        storage.check()
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code"))
        if not create or code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        options = {"Bucket": storage.bucket}
        region = storage.client.meta.region_name
        if region != "us-east-1":
            options["CreateBucketConfiguration"] = {"LocationConstraint": region}
        storage.client.create_bucket(**options)
    if inventory(storage, prefix):
        raise ValueError(
            "Restore requires an empty target bucket prefix; existing objects are untouched."
        )
    for page in storage.client.get_paginator("list_multipart_uploads").paginate(
        Bucket=storage.bucket, Prefix=prefix
    ):
        if page.get("Uploads"):
            raise ValueError("Restore target has incomplete uploads.")


def write_snapshot(storage, destination, *, prefix="hrs/v2/"):
    """Reject unfinished multipart uploads instead of silently omitting their bytes."""
    for page in storage.client.get_paginator("list_multipart_uploads").paginate(
        Bucket=storage.bucket, Prefix=prefix
    ):
        if page.get("Uploads"):
            raise ValueError(
                "Finish or explicitly cancel incomplete uploads before a complete backup."
            )
    before = inventory(storage, prefix)
    entries = []
    with ZipFile(destination, "x", compression=ZIP_STORED, allowZip64=True) as archive:
        for number, (key, expected) in enumerate(sorted(before.items())):
            response = storage.client.get_object(Bucket=storage.bucket, Key=key)
            body, member = response["Body"], f"objects/{number:012d}"
            digest, size = hashlib.sha256(), 0
            try:
                with archive.open(member, "w", force_zip64=True) as target:
                    while chunk := body.read(1024 * 1024):
                        target.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            finally:
                body.close()
            if size != expected["size"] or size != response["ContentLength"]:
                raise ValueError("S3 object changed during backup; stop all writers and retry.")
            entries.append(
                {
                    "key": key,
                    "member": member,
                    "size": size,
                    "sha256": digest.hexdigest(),
                    "content_type": response.get("ContentType", "application/octet-stream"),
                    "metadata": response.get("Metadata", {}),
                }
            )
        if inventory(storage, prefix) != before:
            raise ValueError(
                "S3 inventory changed during backup; no complete receipt was committed."
            )
        manifest = {
            "schema_version": 1,
            "kind": "hrs-object-store",
            "prefix": prefix,
            "objects": entries,
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return {"objects": len(entries), "bytes": sum(row["size"] for row in entries)}


def verify_snapshot(source):
    with ZipFile(source) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != 1 or manifest.get("kind") != "hrs-object-store":
            raise ValueError("Unsupported object-store backup.")
        prefix = manifest["prefix"]
        entries = manifest["objects"]
        members, keys = {"manifest.json"}, set()
        for number, row in enumerate(entries):
            if row["member"] != f"objects/{number:012d}" or not row["key"].startswith(prefix):
                raise ValueError("Invalid object backup member.")
            if row["key"] in keys:
                raise ValueError("Duplicate object in backup.")
            keys.add(row["key"])
            members.add(row["member"])
            digest, size = hashlib.sha256(), 0
            with archive.open(row["member"]) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != row["size"] or digest.hexdigest() != row["sha256"]:
                raise ValueError("Object backup hash or length mismatch.")
        if len(archive.namelist()) != len(members) or set(archive.namelist()) != members:
            raise ValueError("Object backup members differ from the manifest.")
        return manifest


def restore_snapshot(storage, source, *, temporary_root):
    """Restore only to an empty target prefix and verify every uploaded object's bytes."""
    manifest = verify_snapshot(source)
    require_empty_target(storage, manifest["prefix"])
    with ZipFile(source) as archive, tempfile.TemporaryDirectory(dir=temporary_root) as folder:
        for row in manifest["objects"]:
            path = Path(folder) / "object"
            with archive.open(row["member"]) as stream, path.open("wb") as target:
                while chunk := stream.read(1024 * 1024):
                    target.write(chunk)
            if file_digest(path) != (row["sha256"], row["size"]):
                raise ValueError("Object backup changed during restoration.")
            storage.client.upload_file(
                str(path),
                storage.bucket,
                row["key"],
                ExtraArgs={"ContentType": row["content_type"], "Metadata": row["metadata"]},
            )
            response = storage.client.get_object(Bucket=storage.bucket, Key=row["key"])
            digest, size = hashlib.sha256(), 0
            try:
                while chunk := response["Body"].read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            finally:
                response["Body"].close()
            if size != row["size"] or digest.hexdigest() != row["sha256"]:
                raise ValueError("Restored object did not retain its source bytes.")
    return {"bucket": storage.bucket, "objects": len(manifest["objects"]), "verified": True}
