import hashlib
from io import BytesIO
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hrs.api.files import download


def test_mutable_export_url_does_not_cache_an_older_publication():
    versions = [b"first publication", b"corrected publication"]
    current = [0]

    class Body(BytesIO):
        def iter_chunks(self, size):
            while chunk := self.read(size):
                yield chunk

    def get_object(**options):
        content = versions[int(options["Key"])]
        return {
            "Body": Body(content),
            "ContentLength": len(content),
            "Metadata": {"sha256": hashlib.sha256(content).hexdigest()},
        }

    objects = SimpleNamespace(client=SimpleNamespace(get_object=get_object), bucket="test")
    app = FastAPI()

    @app.get("/export")
    def export():
        content = versions[current[0]]
        return download(
            objects,
            (
                "book",
                {
                    "key": str(current[0]),
                    "byte_length": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "media_type": "text/markdown",
                },
            ),
        )

    with TestClient(app) as client:
        first = client.get("/export")
        assert first.headers["cache-control"] == "private, no-store"
        current[0] = 1
        second = client.get("/export")
        assert second.content == versions[1] and first.content == versions[0]
        assert second.headers["cache-control"] == "private, no-store"
        assert first.headers["etag"] != second.headers["etag"]
