"""Bounded process cache for immutable, hash-verified chapter content.

SQL visibility and the current content reference are checked by Library on every read.
Only source data/derived parsing is cached; callers receive independent containers.
"""

import json
from copy import deepcopy
from functools import lru_cache

from hrs_runtime.object_storage import S3Objects

from hrs_platform.services.documents.footnotes import resolve_footnotes
from hrs_platform.services.documents.structure import reading_view

MAX_CACHED_BYTES = 2 * 1024 * 1024


def read_content(storage, reference):
    endpoint, bucket, region, access, secret = storage
    objects = S3Objects(endpoint=endpoint, bucket=bucket, region=region,
                        access_key=access.get_secret_value(), secret_key=secret.get_secret_value())
    body = json.loads(objects.read_bytes(json.loads(reference)))
    text = ''.join(part['text'] for part in body['parts'])
    content = {'text': text, 'parts': body['parts'], 'structure': body.get('structure', [])}
    reading = reading_view(content)
    return {**content, 'reading_text': reading['text'], 'reading_footnotes': reading['footnotes'],
            'footnotes': reading['footnotes'] if reading['text'] == text else resolve_footnotes(text, body['parts'])}


@lru_cache(maxsize=16)
def cached_content(storage, reference):
    return read_content(storage, reference)


def chapter_content(settings, reference):
    storage = (settings.s3_endpoint, settings.s3_bucket, settings.s3_region,
               settings.s3_access_key, settings.s3_secret_key)
    key = json.dumps(reference, sort_keys=True)
    # Large chapters remain readable without monopolizing a bounded process cache.
    loader = cached_content if reference['byte_length'] <= MAX_CACHED_BYTES else read_content
    return deepcopy(loader(storage, key))
