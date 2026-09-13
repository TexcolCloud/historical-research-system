"""Public quote checks use synthetic packages, never approved historical evidence."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from document_extraction.artifacts import write_outputs
from document_extraction.content_readiness import verify_quote
from document_extraction.provenance import ownership_hash
from document_extraction.semantic_completion import complete_document
from document_extraction.utils import sha256, write_json
from test_single_ocr_completion import verdict


class QuoteEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        Image.new("RGB", (10, 10), "white").save(self.source)
        self.folder = self.root / "output"
        self.folder.mkdir()
        self.image = self.folder / "page.png"
        self.image.write_bytes(self.source.read_bytes())
        self.quote = "1938年开始生产。"
        text = self.quote + "\n\n其后继续。\n\n" + self.quote
        completion = complete_document(
            [{"page": 1, "image_path": self.image, "text": text}],
            None,
            self.folder,
            reviewer=lambda *_: verdict(),
        )
        self.manifest = write_outputs(
            self.source, self.folder, completion, ["single"], {}
        )
        self.markdown = (self.folder / "document.candidate.md").read_text("utf-8")
        self.start = self.markdown.index(self.quote)

    def verify(self, **kwargs):
        return verify_quote(self.folder, self.quote, start=self.start, **kwargs)

    def test_repeated_quotation_needs_explicit_unicode_offset(self):
        self.assertIn(
            "missing-or-ambiguous-exact-quote",
            verify_quote(self.folder, self.quote)["reasons"],
        )
        linked = self.verify()
        self.assertEqual(linked["status"], "evidence-linked")
        self.assertEqual(linked["sources"][0]["source_start"], 0)
        self.assertIn(
            "quote-offset-mismatch",
            verify_quote(self.folder, self.quote, start=self.start + 1)["reasons"],
        )

    def test_changed_markdown_and_evidence_are_rejected(self):
        for name in (
            "document.candidate.md",
            "reviews/page-001.json",
            "source-map.json",
            "content-readiness.json",
        ):
            with self.subTest(name=name):
                path = self.folder / name
                before = path.read_bytes()
                path.write_bytes(before + b"\n ")
                try:
                    self.assertIn(
                        "stale-or-unbound-artifacts", self.verify()["reasons"]
                    )
                finally:
                    path.write_bytes(before)
        self.assertIn(
            "stale-or-unbound-artifacts",
            self.verify(expected_markdown_sha256="other")["reasons"],
        )

    def test_original_source_and_page_image_must_still_match(self):
        for path, reason in (
            (self.source, "source-missing-or-changed"),
            (self.image, "page-evidence-missing-or-changed:1"),
        ):
            with self.subTest(path=path.name):
                before = path.read_bytes()
                path.write_bytes(b"changed evidence")
                try:
                    self.assertIn(reason, self.verify()["reasons"])
                finally:
                    path.write_bytes(before)

    def test_carrier_text_does_not_claim_article_title_or_author(self):
        self.assertEqual(self.verify()["status"], "evidence-linked")
        result = self.verify(fields={"article_title": self.quote, "author": "1938"})
        self.assertIn("field-evidence-required:article_title", result["reasons"])
        self.assertIn("field-evidence-required:author", result["reasons"])

    def test_partial_source_coverage_cannot_validate_complete_quote(self):
        mapping_path = self.folder / "source-map.json"
        readiness_path = self.folder / "content-readiness.json"
        mapping = json.loads(mapping_path.read_text("utf-8"))
        readiness = json.loads(readiness_path.read_text("utf-8"))
        mapping["spans"][0]["start"] += 1
        mapping["spans"][0]["source_start"] += 1
        mapping["structure_sha256"] = ownership_hash(mapping["spans"])
        readiness["structure_sha256"] = mapping["structure_sha256"]
        write_json(mapping_path, mapping)
        write_json(readiness_path, readiness)
        for name in ("source-map.json", "content-readiness.json"):
            self.manifest["evidence_hashes"][name] = sha256(self.folder / name)
        write_json(self.folder / "manifest.json", self.manifest)
        self.assertIn("quote-source-coverage-gap", self.verify()["reasons"])
