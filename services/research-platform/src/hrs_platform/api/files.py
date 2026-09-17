"""HTTP download headers; source selection remains in application services."""

from urllib.parse import quote


def download(objects, value):
    title, reference = value
    return object_response(
        objects,
        reference,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(title + ".md", safe=""),
        },
    )

def object_response(objects, reference, range_header=None, *, headers=None):
    """Proxy a verified immutable S3 object, retaining PDF single-range support."""
    import re

    from fastapi.responses import Response, StreamingResponse
    from starlette.background import BackgroundTask

    from hrs_platform.domain.errors import ServiceError

    size = reference["byte_length"]
    response_headers = {
        "ETag": '"' + reference["sha256"] + '"',
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        **(headers or {}),
    }
    options = {}
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if not match or not any(match.groups()) or size == 0:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        a, b = match.groups()
        start = int(a) if a else max(0, size - int(b))
        end = min(int(b), size - 1) if a and b else size - 1
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        options["Range"] = f"bytes={start}-{end}"
        response_headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    response = objects.client.get_object(Bucket=objects.bucket, Key=reference["key"], **options)
    body = response["Body"]
    expected_length = end - start + 1 if range_header else size
    if (
        response["ContentLength"] != expected_length
        or response.get("Metadata", {}).get("sha256") != reference["sha256"]
    ):
        body.close()
        raise ServiceError(502, "原件存储校验失败。")
    response_headers["Content-Length"] = str(expected_length)

    def chunks():
        try:
            yield from body.iter_chunks(1024 * 1024)
        finally:
            body.close()

    return StreamingResponse(
        chunks(),
        status_code=206 if range_header else 200,
        media_type=reference["media_type"],
        headers=response_headers,
        background=BackgroundTask(body.close),
    )
