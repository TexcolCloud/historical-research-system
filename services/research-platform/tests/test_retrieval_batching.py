"""Optional adapter dependency tests, without weights or GPU allocation."""

from types import SimpleNamespace

import pytest

torch = pytest.importorskip('torch')
pytest.importorskip('transformers')
from hrs_platform.domain.retrieval_models import LocalModels


def test_length_buckets_restore_original_order_even_after_oom():
    models = LocalModels(SimpleNamespace())
    calls = []
    def operation(values):
        calls.append(list(values))
        if len(values) > 2:
            raise torch.OutOfMemoryError('synthetic batch pressure')
        return [v.upper() for v in values]
    result = models._batched(['longest', 'a', 'medium', 'bb', 'ccc'], 4, operation, [7, 1, 6, 2, 3])
    assert result == ['LONGEST', 'A', 'MEDIUM', 'BB', 'CCC']
    assert calls[0] == ['a', 'bb', 'ccc', 'medium']
    assert models.last_batches == [2, 2, 1]


def test_empty_batch_and_token_length_validation():
    models = LocalModels(SimpleNamespace())
    assert models._batched([], 2, lambda _: pytest.fail('empty batch inferred'), []) == []
    assert models._check(lambda text, **_: {'input_ids': list(text)}, ['甲', '乙丙'], 3) == [1, 2]
