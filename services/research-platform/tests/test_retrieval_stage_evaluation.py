from types import SimpleNamespace
from uuid import uuid4

from hrs_platform.services.retrieval.chunks import source_excerpt
from hrs_platform.services.retrieval.evaluation import evaluate_cases


def test_complete_rank_requires_all_evidence_and_reports_where_a_range_is_lost():
    text = '甲地运入。乙地未收到。'
    chapter = {**{k:str(uuid4()) for k in ('id','book_id','run_id')}, 'title':'记录', 'text':text,
        'parts':[{'span_id':'p','start':0,'text':text,'source':{'pages':[1]}}]}
    first, second = source_excerpt(chapter,0,5), source_excerpt(chapter,5,len(text))
    def search(*args, **kwargs):
        kwargs['metrics']['total_ms'] = 1
        kwargs['trace'].update(lexical=[first,second], vector=[], rerank_input=[first], ranked=[first])
        return [first,second]
    case = {'query':'两地运输情况','expected':[{'chapter_id':chapter['id'],'start':0,'end':5},
        {'chapter_id':chapter['id'],'start':5,'end':len(text)}]}
    report=evaluate_cases(SimpleNamespace(search=search),chapter['run_id'],chapter['book_id'],[case],lambda _:chapter)
    result=report['cases'][0]
    assert result['reciprocal_rank']==1 and result['complete_evidence_rank']==2
    assert result['complete_reciprocal_rank']==0.5
    assert result['stage_evidence_coverage']=={'lexical':1,'vector':0,'rerank_input':0.5,'ranked':0.5,'returned':1}
