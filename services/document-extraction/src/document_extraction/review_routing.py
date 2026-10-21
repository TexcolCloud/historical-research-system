"""Conservative page routing. Missing recognition confidence is never a pass."""
import math
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

# Stdlib-only so the workbench can validate this same policy without importing OCR.
def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

ROUTING_POLICY = "recognition-risk-routing-v1"
RELEASE_POLICY = "risk-routed-errors-only-block-v1"
ARCHIVE_POLICY = "conversion-archive-article-review-v1"


def route_page(page, index, count, *, threshold=0.98, mode="risk_based"):
    from .native_pdf import native_eligible
    if mode != 'conversion_only' and native_eligible(page):
        return {'policy': ROUTING_POLICY, 'route': 'native-pass', 'reasons': [],
                'confidence': None, 'threshold': threshold,
                'page': page['page'], 'page_index': index, 'page_count': count,
                'text_sha256': text_hash(page['text']), 'image_sha256': sha256(Path(page['image_path'])),
                'native_evidence_sha256': text_hash(json.dumps(page['native_evidence'], sort_keys=True, ensure_ascii=False))}
    text = page['text']
    evidence = page.get('ocr_evidence') or {}
    metadata = evidence.get('metadata') or {}
    score = evidence.get('confidence')
    valid = (type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1
             and metadata.get('confidence_kind') == 'recognition'
             and metadata.get('confidence_scope') == 'page')
    reasons = []
    if mode == 'full':
        reasons.append('full-review-requested')
    if not valid:
        reasons.append('recognition-confidence-unknown')
    elif score < threshold:
        reasons.append('recognition-confidence-low')
    compact = lambda value: re.sub(r'\s+', '', value)
    if not text.strip() or len(compact(text)) < 30:
        reasons.append('empty-or-sparse-text')
    if compact(text) != compact(evidence.get('text', '')):
        reasons.append('recognition-export-difference')
    if re.search(r'[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]|(.)\1{7,}', text):
        reasons.append('garbled-or-repeated-characters')
    lines = Counter(line.strip() for line in text.splitlines() if len(line.strip()) >= 10)
    if any(n >= 3 for n in lines.values()):
        reasons.append('repeated-lines')
    if re.search(r'<(?:table|figure|img)\b|!\[|^\s*\|', text, re.I | re.M):
        reasons.append('table-or-figure')
    if index in (0, count - 1) or re.search(r'^\s*#{1,6}\s|\[\^|^[①②③④⑤⑥⑦⑧⑨]|参考文献|參考文獻|续上|接下|下转|上接', text, re.M):
        reasons.append('boundary-or-attribution-check')
    blocks = evidence.get('blocks') or []
    boxes = [b.get('bbox') for b in blocks]
    valid_boxes = bool(boxes) and all(
        isinstance(box, (list, tuple)) and len(box) == 4
        and all(type(v) in (int, float) and math.isfinite(v) for v in box)
        and 0 <= box[0] < box[2] and 0 <= box[1] < box[3] for box in boxes)
    if not valid_boxes:
        reasons.append('layout-evidence-unknown')
    elif (any(b.get('label') not in {'text', 'paragraph'} for b in blocks)
          or max(b[0] for b in boxes) - min(b[0] for b in boxes) >
             0.2 * (max(b[2] for b in boxes) - min(b[0] for b in boxes))
          or any(a[1] > b[1] for a, b in zip(boxes, boxes[1:]))):
        reasons.append('complex-layout-or-structure')
    return {'policy':ROUTING_POLICY, 'route':'deferred-article' if mode == 'conversion_only' else 'deepseek' if reasons else 'rule-pass',
            'reasons':reasons, 'confidence':score if valid else None, 'threshold':threshold,
            'page':page['page'], 'page_index':index, 'page_count':count,
            'text_sha256':text_hash(text), 'image_sha256':sha256(Path(page['image_path'])),
            'recognition_evidence_sha256':text_hash(json.dumps(evidence, sort_keys=True, ensure_ascii=False))}


def conversion_deferred(page):
    """Evidence-bound absence of review, never a semantic pass."""
    decision = page.get('routing_decision') or {}
    if page.get('review_route') != 'deferred-article' or page.get('verified') or page.get('receipts'):
        return False
    try:
        return (type(decision['page_index']) is int and type(decision['page_count']) is int
                and 0 <= decision['page_index'] < decision['page_count']
                and decision == route_page(page, decision['page_index'], decision['page_count'],
                    threshold=decision['threshold'], mode='conversion_only'))
    except (KeyError, TypeError, ValueError, OSError):
        return False


def rule_passed(page):
    """Recompute rules and verify bindings; a plain caller-supplied status cannot release text."""
    decision = page.get('routing_decision') or {}
    if page.get('review_route') not in {'rule-pass', 'native-pass'} or page.get('receipts') or page.get('concerns'):
        return False
    try:
        threshold = decision['threshold']
        if (type(decision['page_index']) is not int or type(decision['page_count']) is not int
                or not 0 <= decision['page_index'] < decision['page_count']):
            return False
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 < threshold <= 1:
            return False
        return decision == route_page(page, decision['page_index'], decision['page_count'], threshold=threshold) and decision['route'] in {'rule-pass', 'native-pass'}
    except (KeyError, TypeError, ValueError, OSError, AttributeError):
        return False
