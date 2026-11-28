import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tracelens.config import Settings
from tracelens.main import create_app
from tracelens.normalize import normalize, parse_upload


TEMPLATES = Path(__file__).parents[2] / "frontend" / "public" / "templates"


def test_playground_loads_model_inputs_from_deepagent_and_otel(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    store = app.state.store
    client = TestClient(app)

    deep_export = json.loads((TEMPLATES / "deepagent-template.json").read_bytes())
    llm = next(span for span in deep_export["result"]["spans"] if span["ai_span_kind"] == "LLM")
    raw = json.loads(llm["span_input"])
    raw.update(model="captured-model", stream=True, headers={"Authorization": "recorded-key"}, temperature=0.3)
    llm["span_input"] = json.dumps(raw)
    [deepagent] = parse_upload(json.dumps(deep_export).encode(), "deepagent.json")
    deepagent_id = store.add_trace(deepagent)["id"]
    response = client.get(f"/api/playground/traces/{deepagent_id}")
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["selected_span"] == llm["span_id"]
    assert preview["parameters"] == {"messages": raw["messages"], "temperature": 0.3}
    assert "recorded-key" not in response.text and "captured-model" not in response.text

    [otel] = parse_upload((TEMPLATES / "otel-genai-template.json").read_bytes(), "otel.json")
    otel_id = store.add_trace(otel)["id"]
    path = f"/api/playground/traces/{otel_id}"
    latest = client.get(path).json()
    assert len(latest["calls"]) == 2 and latest["selected_span"] == latest["calls"][1]["id"]
    messages = latest["parameters"]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
    assert messages[1]["tool_calls"][0]["id"] == messages[2]["tool_call_id"]
    assert client.get(path, params={"span_id": latest["calls"][0]["id"]}).json()["parameters"]["messages"] == messages[:1]
    assert client.get(path, params={"span_id": "missing"}).status_code == 404

    legacy = normalize({"messages": [{"role": "user", "content": "input"}, {"role": "assistant", "content": "old output"}]})
    legacy_id = store.add_trace(legacy)["id"]
    seed = client.get(f"/api/playground/traces/{legacy_id}").json()
    assert seed["selected_span"] is None and seed["parameters"] == {"messages": [{"role": "user", "content": "input"}]}


def test_playground_calls_only_the_selected_playground_connection(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path))
    connections = app.state.store.model_connections
    play = connections.add(name="Debug", model="debug-model", base_url="https://playground.example/v1",
                           api_key="private-key", headers={"X-Tenant": "playground"}, purpose="playground")
    diagnosis = connections.add(name="Diagnosis", model="diagnosis-model", base_url="https://diagnosis.example/v1",
                                api_key="diagnosis-key", make_default=True)
    calls = []
    provider_status = 200

    def provider(request):
        calls.append(request)
        if provider_status != 200:
            return httpx.Response(provider_status, text="private upstream log")
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "tool_calls", "message": {
                "content": "已处理", "tool_calls": [{"id": "call-1", "type": "function"}],
            }}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
        })

    original = httpx.AsyncClient
    monkeypatch.setattr("tracelens.playground.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(provider), **kwargs))
    client = TestClient(app)
    parameters = {"messages": [{"role":"user", "content":"编辑后的输入"}], "max_tokens": 256}
    path = "/api/playground/run"
    assert client.post(path, json={"model_connection_id": diagnosis, "parameters": parameters}).status_code == 404
    assert client.post(path, json={"model_connection_id": play, "parameters": {**parameters, "model": "override"}}).status_code == 422
    assert not calls

    result = client.post(path, json={"model_connection_id": play, "parameters": parameters})
    assert result.status_code == 200, result.text
    request, = calls
    assert str(request.url) == "https://playground.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer private-key" and request.headers["X-Tenant"] == "playground"
    assert json.loads(request.content) == {**parameters, "model": "debug-model", "stream": False}
    assert "private-key" not in result.text
    assert result.json()["message"]["content"] == "已处理"
    assert result.json()["message"]["tool_calls"][0]["id"] == "call-1"
    assert result.json()["usage"]["total_tokens"] == 11

    provider_status = 403
    result = client.post(path, json={"model_connection_id": play, "parameters": parameters})
    assert result.status_code == 502 and "HTTP 403" in result.json()["detail"]
    assert "private upstream log" not in result.text
