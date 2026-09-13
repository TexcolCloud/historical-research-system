import hashlib
import json

from hrs_platform.activities import Activities


def test_modified_markdown_or_missing_page_cannot_reuse_success_manifest(tmp_path):
    def write(name, data):
        content = data if isinstance(data, bytes) else json.dumps(data).encode()
        (tmp_path / name).write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    markdown_sha = write("document.candidate.md", b"original recognized text")
    mapping_sha = write("source-map.json", {"markdown_sha256": markdown_sha})
    image_sha = write("page.png", b"technical fixture, not image evidence")
    manifest = {
        "source_sha256": "source",
        "page_count": 1,
        "content_status": "release-accepted",
        "source_map": "source-map.json",
        "candidate_markdown": "document.candidate.md",
        "evidence_hashes": {"source-map.json": mapping_sha},
        "pages": [{"image": "page.png", "image_sha256": image_sha}],
    }
    write("manifest.json", manifest)
    path = tmp_path / "manifest.json"
    assert Activities._recoverable(path, "source")
    write("document.candidate.md", b"changed text")
    assert not Activities._recoverable(path, "source")
    write("document.candidate.md", b"original recognized text")
    (tmp_path / "page.png").unlink()
    assert not Activities._recoverable(path, "source")
