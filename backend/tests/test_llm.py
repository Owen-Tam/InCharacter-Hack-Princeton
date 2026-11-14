import json

import httpx
import pytest

from companion import config, llm as llm_module
from companion.llm import AdaptionLLM, LLMError, parse_json_text

SCHEMA = {"type": "object", "properties": {"names": {"type": "array"}}}
KEY = "pt_test_secret_123"


def tool_reply(arguments, **extra):
    return {"status": "completed", "output": [
        {"type": "reasoning"},
        {"type": "function_call", "name": "report", "arguments": arguments},
    ], "output_text": "", "usage": {"input_tokens": 100, "output_tokens": 20, "cost": 0.5}, **extra}


def client(handler, **kwargs):
    return AdaptionLLM(api_key=KEY + " ", transport=httpx.MockTransport(handler),
                       sleep=lambda seconds: None, **kwargs)


def call(llm):
    return llm.complete_json(name="report", instructions="List names.",
                             input="Theo and Mara.", schema=SCHEMA)


def test_forced_tool_call_is_parsed_and_request_is_well_formed():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=tool_reply('{"names": ["Theo"]}'))

    llm = client(handler)
    assert call(llm) == {"names": ["Theo"]}
    request = seen[0]
    assert request.url == "https://api.prod.adaptionlabs.ai/api/v1/responses"
    assert request.headers["authorization"] == f"Bearer {KEY}"  # stray space stripped
    body = json.loads(request.content)
    assert body["model"] == config.EXTRACT_MODEL
    assert body["temperature"] == 0
    assert body["tools"][0]["parameters"] == SCHEMA
    assert body["tool_choice"] == {"type": "function", "name": "report"}
    assert (llm.usage.calls, llm.usage.input_tokens, llm.usage.output_tokens) == (1, 100, 20)
    assert llm.usage.credits(llm.model) == 0.5


def sse(*events, done=True):
    lines = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event)}", ""]
    if done:
        lines += ["data: [DONE]", ""]
    return httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"},
                          content="\n".join(lines).encode())


def test_streamed_response_is_read_from_the_completed_event():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return sse(
            {"type": "response.created", "response": {"status": "in_progress"}},
            {"type": "response.function_call_arguments.delta", "delta": "{\"na"},
            {"type": "response.completed", "response": tool_reply('{"names": ["Theo"]}')},
        )

    llm = client(handler)
    assert call(llm) == {"names": ["Theo"]}
    assert seen[0]["stream"] is True
    assert llm.usage.input_tokens == 100


def test_stream_cut_off_before_completion_is_retried():
    replies = iter([
        sse({"type": "response.created", "response": {}}, done=False),
        sse({"type": "response.completed", "response": tool_reply('{"names": []}')}),
    ])
    assert call(client(lambda r: next(replies))) == {"names": []}


def test_stream_that_never_finishes_is_cut_off_at_the_deadline_and_retried():
    ticks = iter(range(0, 100_000, 100))  # each clock() call advances 100 s
    notes = []
    replies = iter([
        sse(*[{"type": "response.reasoning_text.delta", "delta": "hmm"}] * 20, done=False),
        sse({"type": "response.completed", "response": tool_reply('{"names": ["ok"]}')}),
    ])
    llm = client(lambda r: next(replies), deadline=450, clock=lambda: next(ticks),
                 on_call=notes.append)
    assert call(llm) == {"names": ["ok"]}
    assert "passed its deadline (response.reasoning_text.delta×" in notes[0]
    assert "retrying" in notes[0]
    assert notes[1].startswith("  report: ")


def test_stream_error_event_raises():
    with pytest.raises(LLMError, match="stream error: overloaded"):
        call(client(lambda r: sse({"type": "error", "message": "overloaded"})))


def test_streamed_incomplete_response_raises():
    incomplete = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    with pytest.raises(LLMError, match="max_output_tokens"):
        call(client(lambda r: sse({"type": "response.incomplete", "response": incomplete})))


def test_falls_back_to_json_in_output_text():
    def handler(request):
        return httpx.Response(200, json={
            "status": "completed", "output": [{"type": "message"}],
            "output_text": 'Sure!\n```json\n{"names": ["Mara"]}\n```'})

    assert call(client(handler)) == {"names": ["Mara"]}


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('Here you go: {"a": {"b": 2}} done', {"a": {"b": 2}}),
])
def test_parse_json_text(text, expected):
    assert parse_json_text(text) == expected


@pytest.mark.parametrize("text", ["", "no json", "[1, 2]"])
def test_parse_json_text_rejects(text):
    with pytest.raises(json.JSONDecodeError):
        parse_json_text(text)


def test_retries_rate_limits_and_server_errors():
    replies = iter([httpx.Response(429, text="slow down"), httpx.Response(502, text="bad gateway"),
                    httpx.Response(200, json=tool_reply('{"names": []}'))])
    assert call(client(lambda request: next(replies))) == {"names": []}


def test_retries_network_errors_then_gives_up():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(LLMError, match="gave up after 4 tries: ConnectError"):
        call(client(handler))


def test_request_with_no_response_is_abandoned_and_retried():
    import threading
    release = threading.Event()
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            release.wait(5)  # simulates a server that never sends headers
        return httpx.Response(200, json=tool_reply('{"names": ["late"]}'))

    notes = []
    llm = client(handler, deadline=-29.8, on_call=notes.append)  # watchdog fires after 0.2 s
    try:
        assert call(llm) == {"names": ["late"]}
    finally:
        release.set()
    assert len(calls) == 2
    assert "no response within" in notes[0]


def test_client_errors_are_not_retried_and_report_the_server_message():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text='{"error": "unknown model"}')

    with pytest.raises(LLMError, match="HTTP 400.*unknown model") as info:
        call(client(handler))
    assert len(calls) == 1
    assert KEY not in str(info.value)


def test_incomplete_and_failed_responses_raise():
    with pytest.raises(LLMError, match="max_output_tokens"):
        call(client(lambda r: httpx.Response(200, json={
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})))
    with pytest.raises(LLMError, match="failed"):
        call(client(lambda r: httpx.Response(200, json={
            "status": "failed", "error": {"code": "server_error"}})))


def test_bad_json_is_retried_once():
    replies = iter([tool_reply("{not json"), tool_reply('{"names": ["ok"]}')])
    assert call(client(lambda r: httpx.Response(200, json=next(replies)))) == {"names": ["ok"]}
    always_bad = client(lambda r: httpx.Response(200, json=tool_reply("{not json")))
    with pytest.raises(LLMError, match="valid JSON"):
        call(always_bad)


def test_missing_key_names_the_variable(monkeypatch):
    monkeypatch.setattr(llm_module.config, "load_env", lambda: None)
    monkeypatch.delenv(config.API_KEY_VAR, raising=False)
    with pytest.raises(LLMError, match=config.API_KEY_VAR):
        AdaptionLLM()


def test_reasoning_effort_is_sent_only_when_set():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=tool_reply('{"names": []}'))

    llm = client(handler)
    call(llm)
    llm.complete_json(name="report", instructions="x", input="y", schema=SCHEMA,
                      reasoning_effort="low")
    assert "reasoning" not in seen[0]
    assert seen[1]["reasoning"] == {"effort": "low"}


def test_text_mode_sends_no_tools():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "output_text": '{"names": []}'})

    call(client(handler, use_tools=False))
    assert seen[0]["tools"] == [] and "tool_choice" not in seen[0]
