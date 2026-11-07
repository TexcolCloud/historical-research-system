"""Deterministic decoded-string checks, separate from historical judgments."""

from hrs.processing.errors import EvidenceMismatch
from hrs.processing.hashing import digest


def string_properties(text):
    return {
        "unicode_codepoints": len(text),
        "utf8_bytes": len(text.encode("utf-8")),
        "line_feed_u000a": text.count("\n"),
        "carriage_return_u000d": text.count("\r"),
        "literal_backslash_then_n": text.count("\\n"),
    }


def decoded_evidence_receipts(cards, sources, publication_sha256):
    """Fixed-size metadata per citation; never duplicate quotation/source text."""
    source_hashes = {identity: digest(source["text"]) for identity, source in sources.items()}
    receipts = []
    for card in cards:
        for evidence in card["evidence"]:
            source = sources.get(evidence["article_id"])
            if source is None:
                raise EvidenceMismatch("Mechanical evidence source is outside the frozen task")
            start, end, quote = evidence["start"], evidence["end"], evidence["quote"]
            valid_range = 0 <= start < end <= len(source["text"])
            span = source["text"][start:end] if valid_range else ""
            receipts.append(
                {
                    "receipt_id": f"{card['id']}/{evidence['id']}",
                    "article_id": evidence["article_id"],
                    "start": start,
                    "end": end,
                    "publication_sha256": source.get("publication_sha256", publication_sha256),
                    "source_text_sha256": source_hashes[evidence["article_id"]],
                    "source_hash_verified": source_hashes[evidence["article_id"]]
                    == source["text_sha256"],
                    "quote_exact": valid_range and quote == span,
                    "decoded_quote": {"sha256": digest(quote), **string_properties(quote)},
                    "source_span": {"sha256": digest(span), **string_properties(span)},
                }
            )
    return {
        "schema": "decoded-evidence-v1",
        "receipts": receipts,
        "transport": "JSON escaping is syntax, not the decoded quote. U+000A is an actual line feed; U+005C followed by U+006E is a literal backslash and letter n. Counters and UTF-8 hashes use decoded stored fields and frozen source slices.",
        "scope": "Mechanical string/range/hash checks only. These are not semantic, attribution, note-ownership or historical-accuracy approval; independent review remains required.",
    }
