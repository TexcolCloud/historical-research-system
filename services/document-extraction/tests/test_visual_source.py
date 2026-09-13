import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image

from document_extraction.visual_source import source_reading
from hrs_runtime.local_vision import MODEL


class IndependentSourceTests(unittest.TestCase):
    def test_source_only_reading_is_hashed_and_reused_without_a_draft(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'page.png'
            Image.new('RGB', (100, 300), 'white').save(image)
            settings = SimpleNamespace(model=MODEL, endpoint='http://fixture.invalid',
                cache_enabled=True, cache_path=root/'cache', timeout_seconds=1)
            native = {'model': MODEL, 'choices': [{'finish_reason': 'stop', 'message': {'content':
                json.dumps({'numeric_lines': ['1935年'], 'tables': [], 'unclear': []})}}], 'usage': {'total_tokens': 10}}
            with patch('document_extraction.semantic_completion._request_json', return_value=native) as request:
                first = source_reading(image, settings)
                second = source_reading(image, settings)
            request.assert_called_once()
            self.assertTrue(second['cache_hit'])
            self.assertEqual(second['usage'], {})
            self.assertEqual(first['reading']['numeric_lines'], ['1935年'])
            parts = json.loads(request.call_args.args[0].data)['messages'][0]['content']
            self.assertEqual(sum(p['type'] == 'image_url' for p in parts), 4)
            self.assertNotIn('1935', parts[0]['text'])
            self.assertEqual(first['crop_boxes_pixels'][0], (0, 0, 100, 300))

    def test_incomplete_source_schema_is_rejected(self):
        from document_extraction.visual_source import validate
        for value in ({}, {'numeric_lines': '1935', 'tables': [], 'unclear': []}):
            with self.assertRaises(ValueError):
                validate(value)

    def test_disagreeing_or_missing_table_numbers_remain_unverified(self):
        from document_extraction.visual_source import table_number_conflict
        draft = '<table><tr><td>1,234</td><td>5.6</td></tr></table>'
        self.assertEqual(table_number_conflict(draft, {'tables': ['| 1234 | 5.6 |']}), '')
        self.assertTrue(table_number_conflict(draft, {'tables': ['| 1239 | 5.6 |']}))
        self.assertTrue(table_number_conflict(draft, {'tables': ['| 1234 |']}))
        self.assertTrue(table_number_conflict(draft, {'tables': []}))

    def test_cell_boundaries_and_outside_page_numbers_do_not_create_conflicts(self):
        from document_extraction.visual_source import table_number_conflict
        draft = '<table><tr><td>1,234</td><td>5.6</td><td>125</td></tr></table>'
        for table in (
            '表9\n| 收入 | 比率 | 数量 |\n|---|---|---|\n|1234|5.6|125|\n\n125',
            '125\n<table><tr><td>1234</td><td>5.6</td><td>125</td></tr></table>\n125',
        ):
            self.assertEqual(table_number_conflict(draft, {'tables': [table]}), '')
        # A real cell equal to the printed folio must never disappear from comparison.
        self.assertTrue(table_number_conflict(draft, {'tables': ['|1234|5.6|']}))
        self.assertTrue(table_number_conflict(draft, {'tables': ['|1234|5.6|126|\n125']}))
