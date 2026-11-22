import pytest
from tracelens.normalize import normalize, parse_upload

def test_public_adapters_and_no_hidden_simulator_instruction():
    raw = {"task_id": 12, "reward": 0, "info": {"task": {"instruction": "SECRET HIDDEN GOAL", "actions": ["SECRET ANSWER"]}}, "traj": [{"index": 1, "role": "system", "content": "policy"}, {"index": 2, "role": "user", "content": "visible user request"}, {"index": 3, "role": "assistant", "content": None, "tool_calls": [{"function": {"name": "test", "arguments": "{}"}}]}]}
    trace = normalize(raw)
    assert trace.task_description == "visible user request"
    assert trace.messages[0].source_step == "1"
    assert trace.messages[0].step == 0
    assert '"test"' in trace.messages[2].content
    assert "SECRET" not in trace.model_dump_json()
    unified = {"messages": [{"step": 0, "role": "assistant", "content": "answer"}], "metadata": {"task_id": "one", "dataset": "test", "reward": 0, "annotation": {"critical_error_step": 0}}}
    assert normalize(unified).annotation["critical_error_step"] == 0
    unified["messages"][0]["step"] = 1
    with pytest.raises(ValueError, match="按消息顺序连续编号"):
        normalize(unified)


@pytest.mark.parametrize("raw", [{"messages": None, "metadata": {}}, {"messages": []}, {"messages": [{"role": "invalid"}]}])
def test_bad_input_rejected(raw):
    with pytest.raises(ValueError):
        normalize(raw)


def test_jsonl_and_message_stream():
    content = b'{"role":"user","content":"hello"}\n{"role":"assistant","content":"hi"}\n'
    assert len(parse_upload(content, "stream.jsonl")[0].messages) == 2
    with pytest.raises(ValueError):
        parse_upload(b'not-json', "broken.json")


