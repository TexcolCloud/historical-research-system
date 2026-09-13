from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from document_extraction.semantic_completion import complete_document


def verdict(*, changes=None, concerns=None):
    return {
        "review_state": "completed",
        "changes": changes or [],
        "concerns": concerns or [],
        "structure": {
            "starts_article": False,
            "continues_previous": False,
            "ends_article": True,
            "title": "",
            "author": "",
        },
    }


class CompletionTests(unittest.TestCase):
    def run_completion(self, text, responses):
        calls = []

        def reviewer(packet, images, settings):
            calls.append(packet)
            return responses[len(calls) - 1]

        with TemporaryDirectory() as directory:
            image = Path(directory) / "page.png"
            image.write_bytes(b"original-fixture")
            pages = [{"page": 1, "image_path": image, "text": text}]
            result = complete_document(pages, None, Path(directory), reviewer=reviewer)
        return result, calls

    def test_known_omission_is_proposed_for_human_review_without_rewriting(self):
        before = "史书也有败笔、“赝[4]。"
        after = "史书也有败笔、“赝品”，用笔述顶替口述。[4]。"
        repair = verdict(
            changes=[
                {
                    "before": before,
                    "after": after,
                    "source_reading": after,
                    "location": "左栏末段",
                    "kind": "omission",
                    "explanation": "遗漏了笔述替代口述的具体批评。",
                }
            ]
        )
        result, calls = self.run_completion(before, [repair, verdict()])
        self.assertEqual(result["pages"][0]["text"], before)
        self.assertFalse(result["pages"][0]["verified"])
        self.assertEqual(result["pages"][0]["concerns"][0]['proposed_change']['after'], after)
        self.assertEqual(result["pages"][0]["changes"], [])
        self.assertEqual(len(calls), 1)
        reviewed, _ = self.run_completion(after, [verdict()])
        self.assertTrue(reviewed['pages'][0]['verified'])

    def test_ambiguous_patch_cannot_modify_two_distinct_source_occurrences(self):
        change = {
            "before": "同一句。",
            "after": "另一句。",
            "source_reading": "另一句。",
            "location": "左栏",
            "kind": "claim",
            "explanation": "修改事实",
        }
        result, _ = self.run_completion(
            "同一句。\n\n同一句。", [verdict(changes=[change])]
        )
        self.assertEqual(result["pages"][0]["text"], "同一句。\n\n同一句。")
        self.assertFalse(result["pages"][0]["verified"])

    def test_harmless_normalization_does_not_require_a_rewrite_or_hold(self):
        result, calls = self.run_completion(
            "應辩证的看待。",
            [
                verdict(
                    concerns=[
                        {
                            "kind": "normalization",
                            "excerpt": "應辩证的看待。",
                            "explanation": "繁简和的地不影响理解",
                        }
                    ]
                )
            ],
        )
        self.assertTrue(result["pages"][0]["verified"])
        self.assertEqual(len(calls), 1)

    def test_failed_final_check_does_not_count_repair_as_accepted(self):
        change = {
            "before": "一人",
            "after": "二人",
            "source_reading": "二人",
            "location": "正文",
            "kind": "claim",
            "explanation": "数量影响结论",
        }
        bad = verdict(
            concerns=[{"kind": "claim", "excerpt": "二人", "explanation": "尚未确认"}]
        )
        result, _ = self.run_completion("一人", [verdict(changes=[change]), bad])
        self.assertFalse(result["pages"][0]["verified"])

    def test_unavailable_review_preserves_recoverable_text(self):
        result, _ = self.run_completion(
            "可回看的原稿。", [{"review_state": "unavailable", "reason": "timeout"}]
        )
        self.assertEqual(result["pages"][0]["text"], "可回看的原稿。")
        self.assertFalse(result["pages"][0]["verified"])

    def test_noop_and_normalization_proposals_do_not_block_content(self):
        identical = {
            "before": "原句。",
            "after": "原句。",
            "source_reading": "原句。",
            "location": "正文",
            "kind": "claim",
            "explanation": "原文一致",
        }
        minor = dict(identical, before="的", after="地", kind="normalization")
        result, calls = self.run_completion(
            "原句。", [verdict(changes=[identical, minor])]
        )
        self.assertTrue(result["pages"][0]["verified"])
        self.assertEqual(result["pages"][0]["changes"], [])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
