import copy
from tracelens.citation_views import locate_quote
from tracelens.demo import demo_trace
from tracelens.evaluation import check_evidence
from tracelens.reporting import markdown_report
from tracelens.trajdebug import present_report


def test_original_quote_keeps_original_offsets_and_kind():
    trace = demo_trace()
    evidence = locate_quote('先不要提交退款。', trace, through=4)
    assert evidence['source_kind'] == 'original'
    assert evidence['original_matched'] and evidence['matched']
    assert evidence['step'] == 1
    assert trace.messages[1].content[evidence['start']:evidence['end']] == evidence['quote']


def test_detector_quotes_use_official_current_and_history_views():
    trace = demo_trace()
    trace.messages[3].compress_role = 'compress'
    pool = {'3': {'th1': 'unused detailed observation', 'th3': 'concise observation'},
            '4': {'th1': 'current compressed claim', 'th3': 'unused current summary'}}
    evidence = locate_quote('concise observation', trace, through=4, pool=pool, focus=4)
    assert evidence['source_kind'] == 'compressed'
    assert evidence['view_matches'] == [{'step':3,'tier':'th3','start':0,'end':19}]
    assert not evidence['original_matched'] and evidence['matched']
    assert 'start' not in evidence  # compressed offsets must never highlight raw logs
    current = locate_quote('current compressed claim', trace, preferred=4, through=4, exact_step=True, pool=pool, focus=4)
    assert current['view_matches'][0]['tier'] == 'th1'


def test_unused_or_future_views_cannot_falsely_locate_a_detector_quote():
    trace = demo_trace()
    trace.messages[3].compress_role = 'compress'
    pool = {'3': {'th1':'only in unused detail','th3':'visible summary'},
            '5': {'th1':'only in the future'}}
    for quote in ['only in unused detail','only in the future']:
        evidence = locate_quote(quote, trace, through=4, pool=pool, focus=4)
        assert evidence['source_kind'] == 'unlocated'
        assert not evidence['matched']


def test_multiple_view_locations_are_preserved_for_review():
    trace = demo_trace()
    pool = {'2': {'th1':'shared compressed statement'},'3': {'th1':'shared compressed statement'}}
    evidence = locate_quote('shared compressed statement', trace, through=4, pool=pool, focus=4)
    assert [m['step'] for m in evidence['view_matches']] == [2,3]


def report_fixture():
    trace = demo_trace()
    trigger = {'step':4,'category':'cat-1','attribution':'agent','taxonomy_tag':'act.UnsafeOrForbiddenAction',
               'wrong_content_quote':'compressed refund call','reference_quote':'compressed no-refund request',
               'confidence':'high','confidence_reasoning':'fixture'}
    official = {'step_triggers':[trigger], 'full_instance_states':[],
                'critical_error':{'unified_critical_step':4,'llm_pick_supporting_instance_id':None,'llm_pick_rationale':'fixture rationale'}}
    outputs = {'stage_a':{'step_compressions':{'1':{'th3':'compressed no-refund request'},'4':{'th1':'compressed refund call'}}},
               'stage_b':{'step_triggers':[trigger]}, 'final':official}
    # At focus 4, step 1 uses th2, with the upstream fallback to th3.
    return trace, outputs


def test_locating_compressed_quotes_does_not_change_official_output_or_decision():
    trace, outputs = report_fixture()
    frozen = copy.deepcopy(outputs)
    report = present_report(trace,outputs,model='test',usage={})
    assert outputs == frozen
    assert report['official_output'] == frozen['final']
    assert report['attribution']['critical_step'] == 4
    assert report['evidence_summary'] == {'total':2,'original':0,'compressed':2,'unlocated':0}
    assert report['warnings'] == []
    assert report['triggers'][0]['commitment']['quote'] == outputs['stage_b']['step_triggers'][0]['wrong_content_quote']
    score = check_evidence(trace,report)
    assert score['matched'] == score['compressed_matched'] == 2
    assert score['original_matched'] == 0
    report['triggers'][0]['commitment']['view_matches'][0]['start'] = 9999
    assert check_evidence(trace,report)['matched'] == 1


def test_missing_sources_stay_visible_and_do_not_filter_official_trigger():
    trace, outputs = report_fixture()
    outputs.pop('stage_a')
    report = present_report(trace,outputs,model='test',usage={})
    assert len(report['triggers']) == 1
    assert report['attribution']['critical_step'] == 4
    assert report['evidence_summary']['unlocated'] == 2
    assert '尚未定位' in report['warnings'][0]


def test_export_labels_compressed_sources_without_calling_them_original_quotes():
    trace, outputs = report_fixture()
    report = present_report(trace,outputs,model='test',usage={})
    text = markdown_report({'title':trace.title},{'result':report},[])
    assert '压缩视图 #4 · th1' in text
    assert '对照原始消息 #4' in text
    assert '压缩视图 2' in text
    assert '来源未定位' not in text
    assert 'compressed refund call' in text
