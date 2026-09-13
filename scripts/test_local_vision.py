"""Isolated checks: GPU admission, OCR release, visual routing and durable card receipts."""
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[1]
for folder in ['runtime-support', 'document-extraction']:
    sys.path.insert(0, str(ROOT / 'services' / folder / 'src'))
import local_vision as broker
from hrs_runtime import local_vision as client

class SchedulingTests(unittest.TestCase):

    def test_retrieval_uses_shared_lease_and_unloads_vision_once_per_entry(self):
        vision = broker.Vision()
        vision.retrieval_process = Mock()
        vision.retrieval_process.is_alive.return_value = True
        vision.retrieval_pipe = Mock()
        vision.retrieval_pipe.poll.return_value = True
        vision.retrieval_pipe.recv.return_value = {'result': {'vectors': [[1]]}, 'seconds': 1}
        with patch.object(broker, 'gpu_lease', return_value=nullcontext()) as lease, patch.object(vision, 'unload') as unload, patch.object(vision, 'event'):
            self.assertEqual(vision.retrieve({'operation': 'embed', 'texts': ['样本']}), {'vectors': [[1]]})
            lease.assert_called_once()
            unload.assert_called_once()
        with patch.object(vision, 'unload_retrieval') as release, patch.object(broker, 'free_gib', return_value=12), patch.object(vision, 'event'):
            vision.prepare_ocr()
            release.assert_called_once()

    def test_failed_retrieval_releases_owned_gpu_process(self):
        vision = broker.Vision()
        vision.retrieval_process = Mock()
        vision.retrieval_process.is_alive.return_value = True
        vision.retrieval_pipe = Mock()
        vision.retrieval_pipe.poll.return_value = False
        with patch.object(broker, 'gpu_lease', return_value=nullcontext()), patch.object(vision, 'unload'), patch.object(vision, 'unload_retrieval') as release:
            with self.assertRaises(TimeoutError):
                vision.retrieve({'operation': 'embed', 'texts': ['样本']})
            release.assert_called_once()

    def test_gpu_lease_blocks_a_second_owner_and_releases_after_exception(self):
        import os
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'HRS_GPU_LOCK': str(Path(directory) / 'gpu.lock')}):
            with client.gpu_lease():
                with self.assertRaises(TimeoutError), client.gpu_lease(timeout=0.01):
                    pass
            with client.gpu_lease(timeout=0.01):
                pass

    def test_ocr_keeps_resident_vision_only_with_enough_memory(self):
        vision = broker.Vision()
        vision.process = Mock()
        with patch.object(vision, 'event'), patch.object(vision, 'unload') as unload, patch.object(broker, 'free_gib', return_value=10):
            self.assertTrue(vision.prepare_ocr()['vision_resident'])
            unload.assert_not_called()
        with patch.object(vision, 'event'), patch.object(vision, 'unload') as unload, patch.object(broker, 'free_gib', side_effect=[3, 12]):
            vision.prepare_ocr()
            unload.assert_called_once()
        with patch.object(vision, 'unload'), patch.object(broker, 'free_gib', return_value=2):
            with self.assertRaisesRegex(RuntimeError, 'gpu_memory_unavailable'):
                vision.prepare_ocr()

    def test_truncated_or_wrong_model_response_cannot_pass(self):
        for model, reason in [(client.MODEL, 'length'), ('deepseek-flash', 'stop')]:
            with patch.object(client, 'request', return_value={'model': model, 'choices': [{'finish_reason': reason}]}):
                with self.assertRaises(ValueError):
                    client.chat([])

    def test_ocr_releases_before_return_and_releases_after_failure(self):
        from document_extraction import pipeline, ocr
        backend = Mock()
        backend.convert_document.return_value = ([], {})
        wrapped = pipeline.ScheduledConverter(SimpleNamespace(ocr_backends=[{'name': 'paddleocr-vl'}]), None)
        for failure in (None, RuntimeError('OCR failed')):
            backend.reset_mock()
            backend.convert_document.side_effect = failure
            with patch.object(pipeline, 'gpu_lease', return_value=nullcontext()), patch.object(pipeline, 'prepare_ocr'), patch.object(pipeline, 'DoclingConverter', return_value=backend), patch.object(ocr, '_release_cuda') as release:
                if failure:
                    with self.assertRaises(RuntimeError):
                        wrapped.convert_document('source', 'out')
                else:
                    wrapped.convert_document('source', 'out')
                backend.close.assert_called_once()
                release.assert_called_once()
if __name__ == '__main__':
    unittest.main()
