from hrs.library.card_contracts import CardDraft


def draft():
    return CardDraft(
        title="虚构技术材料",
        document_type="测试",
        source_layer="测试原文",
        formation_date={},
        event_date={},
        tags=[],
        entities=[],
        evidence_relations=[],
        no_argument_reason="仅测试原图观察回流",
        items=[
            {
                "item_id": "e",
                "kind": "evidence",
                "title": "原文",
                "text": "运送120吨。",
                "source_unit_ids": ["u"],
                "selections": [{"unit_id": "u", "quote": "运送120吨。"}],
            },
            {
                "item_id": "s",
                "kind": "section",
                "title": "版面",
                "text": "尚待原页核验。",
                "evidence_refs": ["e"],
            },
        ],
    )
