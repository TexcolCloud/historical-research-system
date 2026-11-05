"""Import source evidence and freeze policy before queueing model work."""

from pathlib import Path

from hrs_codex.render import render_document
from hrs_codex.store import Store


def prepare(store: Store, source: str, title: str = "") -> dict:
    from hashlib import file_digest

    path = Path(source).expanduser().resolve(strict=True)
    with path.open("rb") as stream:
        document_id = file_digest(stream, "sha256").hexdigest()[:24]
    manifest = render_document(path, store.root / "documents" / document_id)
    document = store.register_manifest(manifest, title=title)
    store.freeze_policy(
        document["id"],
        {
            "ocr_model": "gpt-6.1-terra",
            "review_model": "gpt-6.1-sol",
            "organization_model": "gpt-6.1-sol",
            "review_failure": "bounded",
        },
    )
    return store.status(document["id"])
