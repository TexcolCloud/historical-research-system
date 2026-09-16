"""Real PDF rendering and S3 recovery with synthetic pages, never OCR/model approval."""

from io import BytesIO

import pytest
from botocore.exceptions import EndpointConnectionError
from PIL import Image, ImageDraw
from temporalio.exceptions import ApplicationError
from test_review import seed

from hrs_platform.services.visual_review import VisualReview


@pytest.fixture
def originals(platform, tmp_path):
    settings, engine = platform
    run_id, _ = seed(engine)
    reviewer = VisualReview(settings.model_copy(update={"cache_root": tmp_path}), engine)
    pages = []
    for number, color in [(1, "red"), (2, "blue")]:
        image = Image.new("RGB", (300, 200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 30, 290, 180), outline=color, width=8)
        draw.text((20, 60), f"SOURCE PAGE {number}", fill=color)
        image.save(tmp_path / f"source-page-{number}.png")
        pages.append(image)
    buffer = BytesIO()
    pages[0].save(buffer, format="PDF", save_all=True, append_images=pages[1:])
    for page in pages:
        page.close()
    source = reviewer.review.objects.put_bytes(buffer.getvalue(), "application/pdf")
    run = {"id": run_id, "source": source, "recovery_attempt": 0}
    bundle = {"manifest": {"source_sha256": source["sha256"], "page_count": 2, "pages": []}, "files": {}}
    return reviewer, run, bundle, tmp_path


def test_missing_mapping_renders_exact_physical_page_and_reuses_s3(originals, monkeypatch):
    reviewer, run, bundle, directory = originals
    result = reviewer.prepare(run, bundle, [2])
    assert not list(directory.rglob("*.pdf"))
    assert result["issues"] == [] and result["machine_approval"] is False
    reference = result["pages"][0]["reference"]
    raw = reviewer.review.objects.read_bytes(reference)
    (directory / "recovered-page-2.png").write_bytes(raw)
    image = Image.open(BytesIO(raw))
    assert image.size == (900, 600)
    red, green, blue = image.getpixel((30, 100))[:3]
    assert blue > red + 100
    image.close()
    monkeypatch.setattr(
        reviewer, "_restore_page", lambda *a: pytest.fail("Successful recovery rendered twice")
    )
    assert reviewer.prepare(run, bundle, [2]) == result


def test_deleted_s3_page_is_restored_without_changing_conversion_manifest(originals):
    reviewer, run, bundle, directory = originals
    original = reviewer.review.objects.put_bytes(b"deleted image", "image/png")
    bundle["manifest"]["pages"] = [{"page": 1, "image": "page.png"}]
    bundle["files"]["page.png"] = original
    reviewer.review.objects.client.delete_object(Bucket=reviewer.review.objects.bucket, Key=original["key"])
    result = reviewer.prepare(run, bundle, [1])
    assert not result["issues"]
    assert bundle["files"]["page.png"] == original
    assert result["pages"][0]["reference"] != original


@pytest.mark.parametrize("problem", ["hash", "count", "page", "missing_pdf"])
def test_ambiguous_or_missing_original_remains_pending(originals, problem):
    reviewer, run, bundle, directory = originals
    number = 1
    if problem == "hash":
        bundle["manifest"]["source_sha256"] = "wrong"
    elif problem == "count":
        bundle["manifest"]["page_count"] = 3
    elif problem == "page":
        number = 3
    else:
        reviewer.review.objects.client.delete_object(
            Bucket=reviewer.review.objects.bucket, Key=run["source"]["key"]
        )
    result = reviewer.prepare(run, bundle, [number])
    assert not list(directory.rglob("*.pdf"))
    assert not result["pages"] and result["issues"][0]["error_type"] == "original_missing"


def test_storage_transport_failure_is_retryable_and_never_relabelled_as_missing(originals, monkeypatch):
    reviewer, run, bundle, _ = originals
    bundle["manifest"]["pages"] = [{"page": 1, "image": "page.png"}]
    bundle["files"]["page.png"] = run["source"]

    def unavailable(*args):
        raise EndpointConnectionError(endpoint_url="http://synthetic.invalid")

    monkeypatch.setattr(reviewer.review.objects, "verify", unavailable)
    monkeypatch.setattr(
        reviewer, "_restore_page", lambda *a: pytest.fail("Transport failure rendered a page")
    )
    with pytest.raises(ApplicationError) as error:
        reviewer.prepare(run, bundle, [1])
    assert error.value.type == "original_unavailable" and not error.value.non_retryable
