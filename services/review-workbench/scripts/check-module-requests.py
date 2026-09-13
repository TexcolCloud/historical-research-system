"""Validate captured synthetic browser requests with each backend's actual Pydantic contracts.

Run using the selected module's existing venv; does not start services, jobs, models or import data.
"""
import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

parser = argparse.ArgumentParser()
parser.add_argument("module", choices=["ingestion", "retrieval", "cards"])
parser.add_argument("capture", type=Path)
args = parser.parse_args()
capture = json.loads(args.capture.read_text(encoding="utf-8"))
assert capture["fixtureOnly"] is True

if args.module == "ingestion":
    from document_ingestion.draft_contract import (
        ApplicationRequest,
        DraftRevisionRequest,
        PreviewRequest,
    )
    from document_ingestion.imports import ImportRequest
    from document_ingestion.inputs import PreparationRequest
    from document_ingestion.jobs import RetryRequest
    contracts = {"retry": RetryRequest, "input-preparations": PreparationRequest, "imports": ImportRequest,
                 "revisions": DraftRevisionRequest, "previews": PreviewRequest, "applications": ApplicationRequest}
elif args.module == "retrieval":
    from document_retrieval.contracts import ReadRequest, ResultsRequest, SearchRequest
    contracts = {"searches": SearchRequest, "result": ResultsRequest, "reads": ReadRequest}
else:
    from research_cards.contracts import PreviewRequest, RevisionDecision
    contracts = {"revision-previews": PreviewRequest, "revision-decisions": RevisionDecision}

checked = []
for request in capture["requests"]:
    if request["module"] != args.module:
        continue
    contract = contracts[request["path"].rsplit("/", 1)[-1]]
    TypeAdapter(contract).validate_python(request["body"])
    assert request["key"]
    checked.append(request["path"])
assert checked, "No captured requests for this module"
print(json.dumps({"module": args.module, "validated_requests": len(checked), "paths": checked,
                  "runtime_execution": False, "historical_data_used": False}))
