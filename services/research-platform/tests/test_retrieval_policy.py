from hrs_platform.search import EVIDENCE_RULE, MODEL_REVISIONS, calibrated_policy


def test_calibration_expires_when_source_or_model_changes():
    policy = {'rule':EVIDENCE_RULE, 'generation':'g1', 'min_top_score':0.5,
        'candidate_limit':30, 'rerank_limit':20,
        'reranker_revision':MODEL_REVISIONS['BAAI/bge-reranker-v2-m3']}
    result = {'retrieval_generation':'g1', 'retrieval_policy':policy}
    assert calibrated_policy(result) == policy
    assert calibrated_policy({**result,'retrieval_generation':'g2'}) == {}
    for patch in [{'reranker_revision':'old'}, {'min_top_score':float('nan')},
                  {'candidate_limit':-1}, {'rerank_limit':True}, {'rule':'old'}]:
        assert calibrated_policy({**result,'retrieval_policy':{**policy,**patch}}) == {}
