"""One-time retained-rule extraction, with source hashes for the retirement ledger."""
import ast
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
target=root/'services/research-platform/src/hrs_platform/domain'
target.mkdir(exist_ok=True)
ledger=[]
def retain(source,destination,names=None,preamble=None):
    path=root/source
    text=path.read_text(encoding='utf-8')
    if names:
        nodes=ast.parse(text).body
        lines=text.splitlines(keepends=True)
        selected=[]
        for node in nodes:
            name=getattr(node,'name',None)
            if isinstance(node,ast.Assign):
                name=getattr(node.targets[0],'id',None)
            if name in names:
                start=min([node.lineno]+[d.lineno for d in getattr(node,'decorator_list',[])])-1
                selected.append(''.join(lines[start:node.end_lineno]))
        text=(preamble or '')+'\n\n'+'\n\n'.join(selected)+'\n'
    (target/destination).write_text(text,encoding='utf-8')
    ledger.append({'source':source,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                   'destination':str((target/destination).relative_to(root)), 'retained_names':names or 'module'})
retain('services/document-ingestion/src/document_ingestion/book_structure.py','book_structure.py')
retain('services/research-cards/src/research_cards/prompts.py','prompts.py',
       ['COMMON','READ','SYNTHESIZE','CHECK_READING','CHECK_CARD','VISION'])
retain('services/research-cards/src/research_cards/contracts.py','base.py',['Contract','HistoricalDate'],
       'from typing import Literal\nfrom pydantic import BaseModel, ConfigDict, Field, model_validator')
retain('services/research-cards/src/research_cards/generation_contracts.py','generation_contracts.py',
       ['QuoteSelection','UnitReading','ResearchQuestion','ReadingRecord','DigestFact','ReadingDigest',
        'DraftItem','DraftRelation','DraftEntity','CardDraft','CheckFinding','SemanticCheck',
        'ImageItemCheck','OriginalPageObservation','ImageCheck'],
       'from typing import Literal\nfrom pydantic import Field, model_validator\nfrom .base import Contract, HistoricalDate')
retain('services/research-cards/src/research_cards/vision.py','vision.py',['original_crops'],
       'import io\nfrom PIL import Image\nfrom .records import sha256')
retain('services/research-cards/src/research_cards/digests.py','digests.py',['DIGEST_INSTRUCTIONS','partition'],
       'from . import prompts\nfrom .tokens import estimate_request')
retain('services/research-cards/src/research_cards/metering.py','tokens.py',['tokenizer','estimate_request'],
       'import base64\nimport math\nfrom functools import lru_cache\nfrom pathlib import Path\nfrom tokenizers import Tokenizer\nfrom .records import read_json, sha256, json_text\nfrom .errors import Problem')
retain('services/document-retrieval/src/document_retrieval/models.py','retrieval_models.py')
retain('services/document-retrieval/src/document_retrieval/errors.py','errors.py')
for name in ['deepseek-v4-tokenizer.json','tokenizer-manifest.json']:
    (target/'data').mkdir(exist_ok=True)
    (target/'data'/name).write_bytes((root/'services/research-cards/src/research_cards/data'/name).read_bytes())
(target/'__init__.py').write_text('"""Retained extraction-independent research rules; no HTTP servers or queues."""\n',encoding='utf-8')
(root/'output/refactor-v2/domain-retention.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
