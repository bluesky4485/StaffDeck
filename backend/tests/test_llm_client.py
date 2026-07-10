import pytest

from app.llm.client import LLMClient, LLMError, MULTIMODAL_UNSUPPORTED_MESSAGE, model_supports_images


class _ForbiddenResponses:
    def create(self, **_kwargs):  # noqa: ANN003
        raise AssertionError("responses.create must not be called for OpenAI-compatible models")


class _FakeChatCompletions:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        message = type("Message", (), {"content": "ok"})()
        choice = type("Choice", (), {"message": message})()
        return type("Completion", (), {"choices": [choice]})()


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeChatCompletions()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.responses = _ForbiddenResponses()
        self.chat = _FakeChat()


def test_llm_client_uses_600_second_timeout(monkeypatch):
    captured = {}

    def fake_decrypt_secret(_value):  # noqa: ANN001
        return "api-key"

    def fake_openai(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return _FakeOpenAIClient()

    settings = type("Settings", (), {"model_api_timeout_seconds": 600.0})()
    model_config = type(
        "ModelConfig",
        (),
        {
            "api_key_encrypted": "encrypted",
            "base_url": "https://example.test/v1",
            "model": "demo-model",
            "temperature": 0.2,
            "max_output_tokens": 256,
        },
    )()
    monkeypatch.setattr("app.llm.client.decrypt_secret", fake_decrypt_secret)
    monkeypatch.setattr("app.llm.client.OpenAI", fake_openai)
    monkeypatch.setattr("app.llm.client.get_settings", lambda: settings)

    client = LLMClient(model_config)

    assert client.timeout_seconds == 600.0
    assert captured["timeout"] == 600.0


def _completion_with_content(content):  # noqa: ANN001
    return type(
        "Completion",
        (),
        {
            "choices": [
                type(
                    "Choice",
                    (),
                    {"message": type("Message", (), {"content": content})()},
                )()
            ]
        },
    )()


def test_generate_text_uses_chat_completions_only():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "demo-model"
    client.temperature = 0.2
    client.max_output_tokens = 256

    output = client.generate_text("system prompt", {"hello": "world"})

    assert output == "ok"
    call = client.client.chat.completions.calls[0]
    assert call["model"] == "demo-model"
    assert call["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": '{"hello": "world"}'},
    ]
    assert call["max_tokens"] == 256


def test_generate_text_retries_empty_response():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "demo-model"
    client.temperature = 0.2
    client.max_output_tokens = 256
    contents = iter(["", None, "ok"])

    def fake_create(**kwargs):  # noqa: ANN003
        client.client.chat.completions.calls.append(kwargs)
        return _completion_with_content(next(contents))

    client.client.chat.completions.create = fake_create

    assert client.generate_text("system prompt", {"hello": "world"}) == "ok"
    assert len(client.client.chat.completions.calls) == 3


def test_generate_text_empty_response_reports_provider_diagnostics():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "demo-model"
    client.base_url = "https://user:secret@example.test/v1?token=hidden"
    client.timeout_seconds = 600.0
    client.temperature = 0.2
    client.max_output_tokens = 256

    def fake_create(**kwargs):  # noqa: ANN003
        client.client.chat.completions.calls.append(kwargs)
        message = type(
            "Message",
            (),
            {
                "content": None,
                "reasoning_content": "provider-side reasoning",
                "refusal": None,
                "tool_calls": [],
            },
        )()
        choice = type("Choice", (), {"message": message, "finish_reason": "length"})()
        usage = type("Usage", (), {"completion_tokens": 256})()
        return type("Completion", (), {"id": "resp_demo", "choices": [choice], "usage": usage})()

    client.client.chat.completions.create = fake_create

    with pytest.raises(LLMError) as error:
        client.generate_text("system prompt", {"hello": "world"})

    detail = str(error.value)
    assert "Model returned an empty response after 3 attempts" in detail
    assert "provider returned no usable message.content" in detail
    assert "model=demo-model" in detail
    assert "endpoint=https://example.test/v1" in detail
    assert "finish_reason=length" in detail
    assert "reasoning_chars=23" in detail
    assert "completion_tokens=256" in detail
    assert "secret" not in detail
    assert "hidden" not in detail


def test_generate_text_reads_text_from_structured_content_parts():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "demo-model"
    client.temperature = 0.2
    client.max_output_tokens = 256
    part = type("ContentPart", (), {"text": "structured answer"})()

    client.client.chat.completions.create = lambda **_kwargs: _completion_with_content([part])

    assert client.generate_text("system prompt", {"hello": "world"}) == "structured answer"


def test_generate_text_stream_reports_empty_stream_diagnostics():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "demo-model"
    client.base_url = "https://example.test/v1"
    client.timeout_seconds = 600.0
    client.temperature = 0.2
    client.max_output_tokens = 256

    def fake_create(**kwargs):  # noqa: ANN003
        client.client.chat.completions.calls.append(kwargs)
        delta = type("Delta", (), {"content": None, "reasoning_content": "reasoning only"})()
        choice = type("Choice", (), {"delta": delta, "finish_reason": "stop"})()
        chunk = type("Chunk", (), {"id": "chunk_demo", "choices": [choice]})()
        return iter([chunk])

    client.client.chat.completions.create = fake_create

    with pytest.raises(LLMError) as error:
        list(client.generate_text_stream("system prompt", {"hello": "world"}))

    detail = str(error.value)
    assert "stream_chunks=1" in detail
    assert "finish_reason=stop" in detail
    assert "reasoning_chars=14" in detail
    assert len(client.client.chat.completions.calls) == 3


def test_generate_text_projects_conversation_context_messages():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "demo-model"
    client.temperature = 0.2
    client.max_output_tokens = 256

    output = client.generate_text(
        "system prompt",
        {
            "user_message": "买两个",
            "conversation_context": {
                "messages": [
                    {"role": "user", "content": "我是 hx，我要买 A2"},
                    {"role": "assistant", "content": "请问买几个？"},
                    {"role": "user", "content": "买两个"},
                ],
                "metadata": {"total_messages": 3},
            },
        },
    )

    assert output == "ok"
    call = client.client.chat.completions.calls[0]
    assert call["messages"][:4] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "我是 hx，我要买 A2"},
        {"role": "assistant", "content": "请问买几个？"},
        {"role": "user", "content": "买两个"},
    ]
    assert '"messages":' not in call["messages"][-1]["content"]
    assert '"metadata": {"total_messages": 3}' in call["messages"][-1]["content"]


def test_generate_text_projects_conversation_context_images_for_vision_model():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "gpt-4o-mini"
    client.temperature = 0.2
    client.max_output_tokens = 256

    output = client.generate_text(
        "system prompt",
        {
            "user_message": "看这张图",
            "conversation_context": {
                "messages": [
                    {
                        "role": "user",
                        "content": "看这张图",
                        "images": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,AAAA",
                                    "detail": "auto",
                                },
                            }
                        ],
                    }
                ],
            },
        },
    )

    assert output == "ok"
    call = client.client.chat.completions.calls[0]
    assert call["messages"][1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "auto"}},
        ],
    }
    assert '"messages":' not in call["messages"][-1]["content"]


def test_generate_text_rejects_images_for_text_only_model():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "qwen3-6-27b"
    client.temperature = 0.2
    client.max_output_tokens = 256

    with pytest.raises(LLMError) as exc_info:
        client.generate_text(
            "system prompt",
            {
                "conversation_context": {
                    "messages": [
                        {
                            "role": "user",
                            "content": "看图",
                            "images": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}],
                        }
                    ],
                },
            },
        )

    assert str(exc_info.value) == MULTIMODAL_UNSUPPORTED_MESSAGE
    assert client.client.chat.completions.calls == []


def test_model_supports_images_uses_known_multimodal_model_names():
    assert model_supports_images("qwen2.5-vl-72b")
    assert model_supports_images("gpt-4o-mini")
    assert not model_supports_images("qwen3-6-27b")


def test_generate_json_extracts_fenced_json(monkeypatch):
    client = object.__new__(LLMClient)

    def fake_generate_text(_system_prompt, _payload):
        return '```json\n{"decision": "continue_current_skill"}\n```'

    monkeypatch.setattr(client, "generate_text", fake_generate_text)

    assert client.generate_json("prompt", {}) == {"decision": "continue_current_skill"}


def test_generate_json_requests_json_object_mode():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "demo-model"
    client.temperature = 0.2
    client.max_output_tokens = 256
    client.client.chat.completions.create = lambda **kwargs: (  # noqa: E731
        client.client.chat.completions.calls.append(kwargs)
        or type(
            "Completion",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {"message": type("Message", (), {"content": '{"ok": true}'})()},
                    )()
                ]
            },
        )()
    )

    assert client.generate_json("prompt", {}) == {"ok": True}
    assert client.client.chat.completions.calls[0]["response_format"] == {"type": "json_object"}


def test_generate_json_falls_back_when_json_object_mode_is_unsupported():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "demo-model"
    client.temperature = 0.2
    client.max_output_tokens = 256

    def fake_create(**kwargs):  # noqa: ANN003
        client.client.chat.completions.calls.append(kwargs)
        if "response_format" in kwargs:
            raise ValueError("Unsupported parameter: response_format")
        return type(
            "Completion",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {"message": type("Message", (), {"content": '{"ok": true}'})()},
                    )()
                ]
            },
        )()

    client.client.chat.completions.create = fake_create

    assert client.generate_json("prompt", {}) == {"ok": True}
    assert "response_format" in client.client.chat.completions.calls[0]
    assert "response_format" not in client.client.chat.completions.calls[1]


def test_generate_json_falls_back_when_json_object_mode_returns_empty():
    client = object.__new__(LLMClient)
    client.client = _FakeOpenAIClient()
    client.model = "demo-model"
    client.temperature = 0.2
    client.max_output_tokens = 256

    def fake_create(**kwargs):  # noqa: ANN003
        client.client.chat.completions.calls.append(kwargs)
        if "response_format" in kwargs:
            return _completion_with_content("")
        return _completion_with_content('{"ok": true}')

    client.client.chat.completions.create = fake_create

    assert client.generate_json("prompt", {}) == {"ok": True}
    assert all("response_format" in call for call in client.client.chat.completions.calls[:3])
    assert "response_format" not in client.client.chat.completions.calls[3]


def test_generate_json_retries_invalid_json(monkeypatch):
    client = object.__new__(LLMClient)
    calls = iter(["not json", '{"ok": true}'])

    def fake_generate_text(_system_prompt, _payload):
        return next(calls)

    monkeypatch.setattr(client, "generate_text", fake_generate_text)

    assert client.generate_json("prompt", {}) == {"ok": True}


def test_generate_json_retry_keeps_original_payload(monkeypatch):
    client = object.__new__(LLMClient)
    payloads = []
    calls = iter(["not json", '{"ok": true}'])

    def fake_generate_text(_system_prompt, payload):
        payloads.append(payload)
        return next(calls)

    monkeypatch.setattr(client, "generate_text", fake_generate_text)

    assert client.generate_json("prompt", {"query": "廊坊天气", "skill": {"slug": "weather-zh"}}) == {"ok": True}
    assert payloads[1]["query"] == "廊坊天气"
    assert payloads[1]["skill"]["slug"] == "weather-zh"
    assert payloads[1]["_json_repair"]["previous_output"] == "not json"


def test_generate_json_repairs_unescaped_string_quotes_without_retry(monkeypatch):
    client = object.__new__(LLMClient)
    payloads = []

    def fake_generate_text(_system_prompt, payload, response_format=None):  # noqa: ANN001, ARG001
        payloads.append(payload)
        return (
            '{"decision": "start_new_task", "target_skill_id": "purchase", '
            '"reason": "user_name 在 memory 中已明确为"hm"，不需要追问", '
            '"slot_hints": {"user_name": "hm"}}'
        )

    monkeypatch.setattr(client, "generate_text", fake_generate_text)

    result = client.generate_json("prompt", {"query": "我想买东西"})

    assert result == {
        "decision": "start_new_task",
        "target_skill_id": "purchase",
        "reason": 'user_name 在 memory 中已明确为"hm"，不需要追问',
        "slot_hints": {"user_name": "hm"},
    }
    assert len(payloads) == 1
    assert "_json_repair" not in payloads[0]


def test_generate_json_repairs_trailing_commas_and_string_newlines(monkeypatch):
    client = object.__new__(LLMClient)

    def fake_generate_text(_system_prompt, _payload, response_format=None):  # noqa: ANN001, ARG001
        return '{"ok": true, "reason": "第一行\n第二行",}'

    monkeypatch.setattr(client, "generate_text", fake_generate_text)

    assert client.generate_json("prompt", {}) == {"ok": True, "reason": "第一行\n第二行"}


def test_generate_json_allows_multiple_repair_attempts(monkeypatch):
    client = object.__new__(LLMClient)
    payloads = []
    calls = iter(["not json", '{"reason": "用户称呼为"', '{"ok": true}'])

    def fake_generate_text(_system_prompt, payload):
        payloads.append(payload)
        return next(calls)

    monkeypatch.setattr(client, "generate_text", fake_generate_text)

    assert client.generate_json("prompt", {"query": "你好"}) == {"ok": True}
    assert payloads[1]["_json_repair"]["attempt"] == 1
    assert payloads[2]["_json_repair"]["attempt"] == 2
    assert "parser_error" in payloads[2]["_json_repair"]
