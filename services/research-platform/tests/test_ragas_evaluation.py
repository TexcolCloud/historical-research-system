import json
import runpy
from pathlib import Path

import pytest

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'evaluation/tools/ragas/evaluate.py'))


def test_ragas_contexts_keep_ranked_hits_with_their_source_linked_notes():
    values = MODULE['contexts']([{'pages':[7], 'section_path':['1937年末','运输统计'], 'text':'甲地120吨，不含乙地。', 'context':[
        {'role':'footnote', 'pages':[8], 'text':'仅统计三月。'}]}])
    assert len(values)==1 and values[0].startswith('[证据1]')
    assert '不含乙地' in values[0] and '仅统计三月' in values[0] and '[8]' in values[0]
    assert '1937年末 / 运输统计' in values[0]


def test_failed_scores_are_not_zero_and_no_answer_cases_have_separate_denominators():
    rows = [
        {'id':'a','reference':'已知','answer':{'answerable':True},'retrieved_contexts':['证据'],
         'metrics':{'context_recall':{'value':0.5}},'errors':[]},
        {'id':'b','reference':'已知','metrics':{},'errors':[{'metric':'answer','type':'Timeout'}]},
        {'id':'n','reference':None,'answer':{'answerable':False},'retrieved_contexts':[], 'metrics':{},'errors':[]},
    ]
    result = MODULE['summarize'](rows)
    assert result['metrics']['context_recall']=={'mean':0.5,'scored':1,'eligible':2}
    assert result['negative_abstained']==1 and result['negative_empty_retrieval']==1
    assert len(result['errors'])==1


def test_reference_edits_require_a_matching_review_hash_before_search(tmp_path):
    dataset, refs = tmp_path/'input.json', tmp_path/'refs.json'
    dataset.write_text('{}',encoding='utf-8')
    refs.write_text(json.dumps({'references':{'a':'new answer'}, 'development_review':{
        'machine_approval':True,'original_first':True,'references_sha256':'old'}}),encoding='utf-8')
    with pytest.raises(ValueError,match='development verdict'):
        MODULE['snapshot'](None,None,[dataset],refs,'unused',5)


def test_rubric_scores_include_negative_answers_and_explicit_missing_evidence():
    rows = [{'id':'a','reference':'甲','retrieved_contexts':['甲'],
        'expected':[{'chapter_id':'c','start':0,'end':6}],
        'hits':[{'chapter_id':'c','start':0,'end':2,'context':[]}],
        'metrics':{'historical_evidence':{'value':3}}, 'errors':[]},
        {'id':'n','reference':None,'retrieved_contexts':[],
         'metrics':{'historical_evidence':{'value':2}},'errors':[]}]
    result = MODULE['summarize'](rows)
    assert result['all_case_metrics']['historical_evidence'] == {'mean':2.5,'scored':2,'eligible':2}
    assert result['complete_required_evidence'] == 0


def test_table_scope_metadata_is_present_in_model_context_without_rewriting_html():
    html='<table><tr><td rowspan="2">45</td></tr></table>'
    value=MODULE['contexts']([{'pages':[7],'text':html,'table_scopes':[
        {'value':'45','row_numbers':[1,2],'row_labels':[['甲'],['乙']]}]}])[0]
    assert html in value and 'table_scopes' in value and '甲' in value and '乙' in value
