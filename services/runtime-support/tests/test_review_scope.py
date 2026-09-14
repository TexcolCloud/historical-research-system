import pytest

from hrs_runtime.review_scope import concern_ranges, split_change


@pytest.mark.parametrize(
    ('before', 'after', 'expected'),
    [
        ('ab', 'ac', [(1, 2)]),
        ('a\nb', 'ab', [(0, 3)]),
        ('ab', 'a\nb', [(0, 2)]),
        ('a\r\nb', 'a\nb', [(0, 4)]),
        ('a\r b', 'ab', [(0, 4)]),
        ('ab', '', [(0, 2)]),
        ('', 'ab', [(0, 0)]),
        ('ab', 'ab', []),
    ],
)
def test_split_changes_always_have_source_bounds(before, after, expected):
    change = {'before': before, 'after': after, 'start_before': 7, 'kind': 'organization'}
    edits = split_change(change)
    assert [(e['start_before'] - 7, e['end_before'] - 7) for e in edits] == expected
    restored = before
    for edit in reversed(edits):
        a, b = edit['start_before'] - 7, edit['end_before'] - 7
        assert before[a:b] == edit['before']
        restored = restored[:a] + edit['after'] + restored[b:]
        assert edit['kind'] == 'organization'
    assert restored == after
    assert 'end_before' not in change


@pytest.mark.parametrize('stored_end', [None, 999])
def test_structural_change_derives_end_from_before_not_optional_metadata(stored_end):
    change = {'before': 'a\nb', 'after': 'ab'}
    if stored_end is not None:
        change['end_before'] = stored_end
    assert [(e['start_before'], e['end_before']) for e in split_change(change)] == [(0, 3)]


@pytest.mark.parametrize(
    ('before', 'after', 'expected'),
    [('a\nb', 'ab', [(4, 7)]), ('ab', 'a\nb', [(4, 6)]), ('ab', 'axb', [(4, 6)])],
)
def test_proposal_location_comes_from_current_page(before, after, expected):
    text = 'head' + before + 'tail'
    proposal = {'before': before, 'after': after, 'start_before': 888, 'end_before': 999}
    assert concern_ranges(text, {'proposed_change': proposal}) == expected
    assert proposal['start_before'] == 888


def test_unlocated_or_unchanged_proposal_keeps_concern_fallback():
    assert concern_ranges('ab ab', {'proposed_change': {'before': 'ab', 'after': 'a\nb'}}) is None
    assert concern_ranges('ab', {'proposed_change': {'before': 'missing', 'after': 'x'}}) is None
    assert concern_ranges('ab', {'excerpt': 'ab', 'proposed_change': {'before': 'ab', 'after': 'ab'}}) == [(0, 2)]
