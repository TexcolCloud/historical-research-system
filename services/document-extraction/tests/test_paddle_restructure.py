"""Exercise installed Paddle postprocessing, without initializing OCR or calling models."""
import copy
import json
import tempfile
import unittest
from pathlib import Path


class RestructureTests(unittest.TestCase):
    def test_real_title_releveling_updates_unique_markdown_heading_only(self):
        from document_extraction.paddle_restructure import restructure_document
        from paddlex.inference.pipelines.paddleocr_vl.pipeline import _PaddleOCRVLPipeline
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            raw = {'input_path':'fixture.png','page_index':0,'page_count':1,'width':600,'height':800,
                'model_settings':{'format_block_content':True,'use_doc_preprocessor':False,'use_layout_detection':False},
                'parsing_res_list':[{'block_label':'paragraph_title','block_bbox':[10,10,500,70],
                    'block_content':'第一章 绪论','block_id':0}]}
            page = {'page':1,'text':'###### 第一章 绪论\n\n正文内容保持不变。',
                    'ocr_evidence':{'metadata':{'paddle_results':[raw]}}}
            report = restructure_document([page], output,
                lambda data, **kwargs: _PaddleOCRVLPipeline.restructure_pages(None, data, **kwargs))
            self.assertEqual(report['applied_titles'], 1)
            self.assertFalse(page['text'].startswith('######'))
            self.assertTrue(page['text'].endswith('正文内容保持不变。'))
            self.assertEqual(raw['parsing_res_list'][0]['block_content'], '第一章 绪论')

    def test_real_cross_page_merge_keeps_physical_parts_and_binds_a_logical_table(self):
        from document_extraction.paddle_restructure import restructure_document
        from paddlex.inference.pipelines.paddleocr_vl.pipeline import _PaddleOCRVLPipeline
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            pages = []
            for number, value in [(1, '甲'), (2, '乙')]:
                image = output/f'page-{number}.png'
                image.write_bytes(f'synthetic-page-{number}'.encode())
                table = f'<table><tr><td>姓名</td></tr><tr><td>{value}</td></tr></table>'
                raw = {'input_path':str(image), 'page_index':0,'page_count':1,'width':600,'height':800,
                    'model_settings':{'format_block_content':True,'use_doc_preprocessor':False,'use_layout_detection':False},
                    'parsing_res_list':[{'block_label':'table','block_bbox':[10,10,500,700],
                                         'block_content':table,'block_id':0}]}
                pages.append({'page':number,'image_path':image,'text':table,
                    'ocr_evidence':{'metadata':{'paddle_results':[raw]}}})
            before = copy.deepcopy(pages)
            calls = []
            def run(results, **kwargs):
                calls.append(kwargs)
                return _PaddleOCRVLPipeline.restructure_pages(None, results, **kwargs)
            report = restructure_document(pages, output, run)
            self.assertEqual(calls, [{'merge_tables':True,'relevel_titles':True,'concatenate_pages':False}])
            self.assertEqual([p['text'] for p in pages], [p['text'] for p in before])
            self.assertEqual(pages[1]['ocr_evidence'], before[1]['ocr_evidence'])
            self.assertEqual(report['merged_table_count'], 1)
            audit = json.loads((output/'paddle-restructure.json').read_text(encoding='utf-8'))
            group = audit['tables'][0]
            self.assertEqual(group['pages'], [1,2])
            self.assertIn('甲', group['merged_html'])
            self.assertIn('乙', group['merged_html'])
            self.assertEqual(len(group['members']), 2)
            self.assertEqual(group['qualification'], 'structural-candidate-not-release-approval')
            self.assertEqual(pages[0]['restructure_evidence']['tables'][0]['group_id'],
                             pages[1]['restructure_evidence']['tables'][0]['group_id'])
            from document_extraction.semantic_completion import complete_document
            from document_extraction.artifacts import write_outputs
            from test_single_ocr_completion import verdict
            packets = []
            def review(packet, images, settings):
                packets.append(packet)
                return verdict()
            completion = complete_document(pages, None, output, reviewer=review)
            self.assertTrue(all(p['target']['restructure_evidence']['tables'] for p in packets))
            manifest = write_outputs(pages[0]['image_path'], output, completion, ['fixture'], {})
            self.assertEqual(manifest['paddle_restructure'], 'paddle-restructure.json')
            self.assertIn('paddle-restructure.json', manifest['evidence_hashes'])
            mapping = json.loads((output/'source-map.json').read_text(encoding='utf-8'))
            self.assertTrue(mapping['spans'][1]['restructure_evidence']['tables'])
