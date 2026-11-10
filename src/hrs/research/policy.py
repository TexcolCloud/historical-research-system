"""Defaults for new source-card runs; retained runs keep their frozen policy."""

from copy import deepcopy

from hrs.processing.contracts import SOL
from hrs.research.supervision import COMPLETE_SUPERVISION

STAGES = ("author", "enrich", "verify", "repair", "reverify")
VERIFY_STAGES = {"verify", "reverify"}
ENRICH_STAGES = {"enrich", "repair"}
CARD_STAGES = tuple(stage for stage in STAGES if stage != "enrich")
POLICY = {
    "version": 7,
    "card_only": True,
    "dossier_cards": True,
    "complete_research": True,
    "max_research_queries": 8,
    "max_content_repairs": 1,
    "incremental_updates": True,
    "verify_decoded_evidence": True,
    "verify_search_audit": True,
    "business_content_scope": "cited_sources_only_without_runtime_accounting",
    "enrichment_search_required": False,
    "model": SOL,
    "coordinator_model": SOL,
    "models": dict.fromkeys(CARD_STAGES, SOL),
    "reasoning_effort": "high",
    "stages": {stage: {"model": SOL, "reasoning_effort": "high"} for stage in CARD_STAGES},
    "source_first": True,
    "independent_verification": True,
    "max_technical_attempts": 3,
    "supervision": deepcopy(COMPLETE_SUPERVISION),
}
