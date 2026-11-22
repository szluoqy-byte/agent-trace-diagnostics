import pytest
from tracelens.demo import demo_trace, demo_report
from tracelens.evaluation import source_step_to_unified, score_prediction, check_evidence, agentrx_gold, stage_scores


def test_original_step_mapping_does_not_confuse_one_and_zero_based_indices():
    trace = demo_trace()
    for m in trace.messages:
        m.source_step = str(m.step + 1)
    gold = [{"trajectory_id":trace.task_id,"failures":[{"failure_id":1,"step_number":5,"failure_category":"test","step_reason":"test"}],"root_cause":{"failure_id":1}}]
    assert agentrx_gold(trace,gold)["step"] == 4
    trace.messages[5].source_step = "5"
    with pytest.raises(ValueError,match="唯一映射"):
        source_step_to_unified(trace,5)


def test_null_prediction_is_a_miss_and_zero_is_valid():
    assert score_prediction(None,0)["exact_match"] is False
    assert score_prediction(0,0)["exact_match"] is True
    assert score_prediction(3,4)["within_1"] is True
    assert score_prediction(3,6)["within_2"] is False


def test_evidence_score_checks_offsets_and_quotes():
    trace,report = demo_trace(),demo_report()
    score = check_evidence(trace,report)
    assert score["total"] > 0 and score["total"] == score["matched"]
    report["triggers"][0]["commitment"]["start"] = 999999
    assert check_evidence(trace,report)["matched"] < score["matched"]


def test_evidence_score_supports_model_anchors_with_start_only_or_null_optional_offsets():
    trace = demo_trace()
    quote = '先不要提交退款。'
    start = trace.messages[1].content.index(quote)
    assert check_evidence(trace, {'anchor': {'step': 1, 'quote': quote, 'start': start}})['matched'] == 1
    assert check_evidence(trace, {'anchor': {'step': 1, 'quote': quote, 'start': None}})['matched'] == 1
    assert check_evidence(trace, {'anchor': {'step': 1, 'quote': quote, 'start': start + 1}})['matched'] == 0


def test_stage_metrics_identify_loss_in_semantic_review_instead_of_detector():
    report = {'engine_version': 'fusion-0.5.1', 'proposals': [{'step': 3}, {'step': 4}],
              'triggers': [{'step': 4}], 'instances': [{'origin_step': 4, 'terminal_relevant': True}],
              'attribution': {'critical_step': 4}}
    scores = stage_scores(report, 3)
    assert scores['proposal_recall'] and not scores['trigger_recall']
    assert not scores['candidate_retained'] and not scores['exact_match']


def test_legacy_stage_metrics_keep_original_repaired_filter():
    report = {'engine_version': 'fusion-0.1', 'triggers': [{'step': 3}],
              'instances': [{'origin_step': 3, 'terminal_relevant': True, 'status': 'repaired'}]}
    scores = stage_scores(report, 3)
    assert scores['proposal_recall'] and scores['trigger_recall']
    assert not scores['candidate_retained'] and scores['unknown_prediction']
