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


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_jsonl_and_message_stream(encoding):
    content = '{"role":"user","content":"查询状态😊"}\r\n{"role":"assistant","content":"hi"}\r\n'
    [trace] = parse_upload(content.encode(encoding), "stream.jsonl")
    assert [message.content for message in trace.messages] == ["查询状态😊", "hi"]


def test_upload_reports_encoding_and_json_errors():
    with pytest.raises(ValueError, match="请保存为 UTF-8"):
        parse_upload('{"messages":[{"role":"user","content":"查询"}]}'.encode("gbk"), "gbk.json")
    with pytest.raises(ValueError):
        parse_upload(b'not-json', "broken.json")


