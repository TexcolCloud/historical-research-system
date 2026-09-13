from types import SimpleNamespace

from document_extraction.artifacts import _issue_range
from document_extraction.semantic_completion import complete_document
from hrs_runtime.review_scope import figure_page


def verdict(changes=None):
    return {
        "review_state": "completed",
        "changes": changes or [],
        "concerns": [],
        "structure": {
            "starts_article": False,
            "continues_previous": False,
            "ends_article": False,
            "title": "",
            "author": "",
        },
    }


def test_typographic_quote_localizes_one_paragraph_instead_of_entire_page():
    text = "第一段无误。\n\n在华中方面：青浦--吴江--江宁镇。\n\n后续段落无误。"
    excerpt = "在华中方面：青浦——吴江——江宁镇。"
    a, b = _issue_range({"text": text}, {"excerpt": excerpt})
    assert text[a:b] == "在华中方面：青浦--吴江--江宁镇。"
    assert _issue_range({"text": text}, {"excerpt": "不存在的句子"}) is None


def test_correction_is_written_only_after_clean_original_image_recheck(tmp_path):
    image = tmp_path / "original.png"
    image.write_bytes(b"synthetic-original")
    original = "甲地运入130吨粮食，不包括乙地。"
    change = {
        "before": "130吨",
        "after": "120吨",
        "source_reading": "120吨",
        "location": "正文第一行",
        "kind": "claim",
        "explanation": "数量识别错误",
    }
    calls = []

    def reviewer(packet, images, settings):
        calls.append(packet["target"]["text"])
        return verdict([change]) if len(calls) == 1 else verdict()

    result = complete_document(
        [{"page": 1, "image_path": image, "text": original}],
        SimpleNamespace(review_mode="full"),
        tmp_path,
        reviewer=reviewer,
    )
    page = result["pages"][0]
    assert len(calls) == 2
    assert page["text"] == original.replace("130吨", "120吨")
    assert page["verified"] and page["changes"][0]["human_review"] is False


def test_map_carrier_is_not_plain_body_text():
    assert figure_page("Image\n\n北\n\n![Image](docling-assets/map.png)\n\n图1 行动图\n1937年")
    assert not figure_page("正文。" * 1000 + "![照片](photo.png)")
    assert not figure_page("这是一段需要核验的短正文，记载实际事件。\n\n![照片](photo.png)")
