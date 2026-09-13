"""Source identity and Unicode offsets for the exported document."""

from __future__ import annotations
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from .models import PageResult
from .utils import sha256 as file_hash


def text_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def ownership_hash(spans: list[dict]) -> str:
    fields = (
        "start",
        "end",
        "page",
        "source_start",
        "source_end",
        "source_kind",
        "article_id",
        "content_role",
        "article_identity_status",
        "article_relation",
        "boundary_basis",
        "boundary_evidence",
        "note_id",
        "article_association_basis",
        "association_anchors",
    )
    return text_hash(
        json.dumps(
            [{k: s.get(k) for k in fields} for s in spans],
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@dataclass
class MappedText:
    text: str = ""
    spans: list[dict] = field(default_factory=list)

    @classmethod
    def source(cls, text: str, **evidence) -> MappedText:
        return cls(
            text,
            [
                {
                    "start": 0,
                    "end": len(text),
                    "source_start": 0,
                    "source_end": len(text),
                    **evidence,
                }
            ]
            if text
            else [],
        )

    def __add__(self, other: MappedText | str) -> MappedText:
        if isinstance(other, str):
            other = MappedText(other)
        offset = len(self.text)
        return MappedText(
            self.text + other.text,
            [
                *self.spans,
                *[
                    {
                        **item,
                        "start": item["start"] + offset,
                        "end": item["end"] + offset,
                    }
                    for item in other.spans
                ],
            ],
        )


def source_map(
    source: Path, output: Path, final: MappedText, results: list[PageResult]
) -> dict:
    pages = []
    for result in results:
        pages.append(
            {
                "page": result.page,
                "image": result.image,
                "image_sha256": file_hash(output / result.image),
                "candidate_sha256": text_hash(result.candidate_text),
            }
        )
    spans = [
        {**item, "text_sha256": text_hash(final.text[item["start"] : item["end"]])}
        for item in final.spans
    ]
    uncovered = final.text
    for item in reversed(spans):
        uncovered = (
            uncovered[: item["start"]]
            + " " * (item["end"] - item["start"])
            + uncovered[item["end"] :]
        )
    return {
        "schema_version": 1,
        "offset_basis": "final_markdown_unicode_codepoints_lf",
        "source": str(source),
        "source_sha256": file_hash(source),
        "markdown_sha256": text_hash(final.text),
        "pages": pages,
        "spans": spans,
        "structure_sha256": ownership_hash(spans),
        "structure_evidence_type": "engineering-source-relations-not-model-identity-approval",
        "article_identity_basis": "local-source-relations-v3; not-bibliographic-verification",
        "coordinate_basis": "original-page-image-pixels-when-recorded-otherwise-full-page",
        "generated_text": uncovered.strip(),
    }
