"""Synthetic provider payloads: no model execution or source-content approval."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from document_extraction.ocr import PaddleBackend


class LayoutEvidenceTests(unittest.TestCase):
    def test_disabled_and_missing_detector_scores_never_become_confidence_one(self):
        from document_extraction.ocr import paddle_layout_evidence
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory)/'source.png'
            image.write_bytes(b'synthetic')
            disabled = {'model_settings':{'use_layout_detection':False},
                        'layout_det_res':{'boxes':[{'score':1,'coordinate':[0,0,10,10],'label':'text'}]}}
            for payload in (disabled, {}):
                result = paddle_layout_evidence([payload], image)
                self.assertEqual(result['status'], 'unavailable')
                self.assertIsNone(result['minimum_detection_confidence'])

    def test_paddle_layout_scores_survive_export_without_becoming_ocr_confidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root/'original.png'
            image.write_bytes(b'synthetic-layout-image')
            payload = {'width':600, 'height':800,
                'model_settings':{'use_layout_detection':True, 'use_doc_preprocessor':False},
                'layout_det_res':{'boxes':[
                    {'label':'doc_title','coordinate':[10,10,500,60],'score':0.99},
                    {'label':'text','coordinate':[10,80,500,700],'score':0.63},
                    {'label':'footnote','coordinate':[10,720,500,760],'score':True}]},
                'parsing_res_list':[{'block_content':'工程夹具正文','block_bbox':[10,80,500,700],'block_label':'text'}]}
            def save_to_markdown(save_path):
                (save_path/'page.md').write_text('工程夹具正文', encoding='utf-8')
            result = SimpleNamespace(json={'res':payload}, save_to_markdown=save_to_markdown)
            backend = PaddleBackend.__new__(PaddleBackend)
            backend.pipeline = SimpleNamespace(predict=lambda _: [result])
            recognized = backend.extract(image, root/'work')
            self.assertIsNone(recognized.confidence)
            layout = recognized.metadata['layout_evidence']
            self.assertEqual(layout['confidence_kind'], 'layout-detection')
            self.assertEqual(layout['minimum_detection_confidence'], 0.63)
            self.assertEqual(layout['status'], 'partial')
            self.assertIsNone(layout['frames'][0]['detections'][2]['confidence'])
            self.assertEqual(layout['frames'][0]['detections'][0]['bbox'], [10,10,500,60])
            from dataclasses import asdict
            from document_extraction.artifacts import write_outputs
            completion = {'pages':[{'page':1,'image_path':image,'text':recognized.text,
                'ocr_evidence':asdict(recognized), 'verified':True,'concerns':[], 'changes':[],
                'receipts':[{'verdict':{'review_state':'completed'}}]}]}
            write_outputs(image, root, completion, ['synthetic-no-model'], {})
            mapping = json.loads((root/'source-map.json').read_text(encoding='utf-8'))
            self.assertEqual(mapping['spans'][0]['layout_evidence'], layout)
            self.assertIsNone(mapping['spans'][0]['article_id'])
