from __future__ import annotations

import ast
from collections.abc import Iterator
import copy
import json
import re
from typing import Any
from urllib.parse import urlsplit

from openai import OpenAI

from app.config import get_settings
from app.db.models import ModelConfig
from app.security.encryption import decrypt_secret


class LLMError(Exception):
    """Raised when an LLM provider request or response normalization fails."""


JSON_REPAIR_ATTEMPTS = 3
EMPTY_RESPONSE_RETRIES = 2
EMPTY_RESPONSE_MESSAGE = "Model returned an empty response"
DEFAULT_MODEL_API_TIMEOUT_SECONDS = 600.0
MULTIMODAL_UNSUPPORTED_MESSAGE = "该模型目前不支持多模态输入"
_VISION_MODEL_PATTERNS = (
    r"\bgpt-4o\b",
    r"\bgpt-4\.1\b",
    r"\bgpt-5\b",
    r"\bo3\b",
    r"\bo4\b",
    r"\bclaude-(3|4)\b",
    r"\bgemini\b",
    r"\bglm-4v\b",
    r"\bqwen[^,\s]*[-_./]vl\b",
    r"\bqvq\b",
    r"\bvl[-_./]max\b",
    r"\bvision\b",
    r"\bvisual\b",
    r"\bomni\b",
    r"\bllava\b",
    r"\binternvl\b",
    r"\bminicpm[-_./]?v\b",
    r"\bkimi[-_./]?vl\b",
    r"\bdoubao[^,\s]*vision\b",
)


class LLMClient:
    def __init__(self, model_config: ModelConfig):
        api_key = decrypt_secret(model_config.api_key_encrypted)
        if not api_key:
            raise LLMError("Model API key is not configured")
        self.timeout_seconds = get_settings().model_api_timeout_seconds or DEFAULT_MODEL_API_TIMEOUT_SECONDS
        self.base_url = str(model_config.base_url or "")
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )
        self.model = model_config.model
        self.temperature = model_config.temperature
        self.max_output_tokens = model_config.max_output_tokens

    def generate_text(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        response_format: dict[str, str] | None = None,
    ) -> str:
        context_messages, serialized_payload = _project_context_messages(user_payload)
        if _messages_include_images(context_messages) and not model_supports_images(self.model):
            raise LLMError(MULTIMODAL_UNSUPPORTED_MESSAGE)
        serialized = json.dumps(serialized_payload, ensure_ascii=False)
        try:
            request: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *context_messages,
                    {"role": "user", "content": serialized},
                ],
                "temperature": self.temperature,
                "max_tokens": self.max_output_tokens,
            }
            if response_format:
                request["response_format"] = response_format
            empty_diagnostics: list[str] = []
            for attempt in range(EMPTY_RESPONSE_RETRIES + 1):
                completion = self.client.chat.completions.create(
                    **request,
                )
                content = _completion_message_content(completion)
                if content.strip():
                    return content
                empty_diagnostics.append(_completion_empty_diagnostic(completion, attempt + 1))
                if attempt >= EMPTY_RESPONSE_RETRIES:
                    raise LLMError(_empty_response_detail(self, empty_diagnostics))
        except Exception as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError(_provider_failure_detail(self, exc)) from exc

    def generate_text_stream(self, system_prompt: str, user_payload: dict[str, Any]) -> Iterator[str]:
        context_messages, serialized_payload = _project_context_messages(user_payload)
        if _messages_include_images(context_messages) and not model_supports_images(self.model):
            raise LLMError(MULTIMODAL_UNSUPPORTED_MESSAGE)
        serialized = json.dumps(serialized_payload, ensure_ascii=False)
        try:
            empty_diagnostics: list[str] = []
            for attempt in range(EMPTY_RESPONSE_RETRIES + 1):
                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        *context_messages,
                        {"role": "user", "content": serialized},
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_output_tokens,
                    stream=True,
                )
                pending_parts: list[str] = []
                emitted_text = False
                chunk_count = 0
                choice_chunk_count = 0
                reasoning_chars = 0
                finish_reasons: set[str] = set()
                response_ids: set[str] = set()
                for chunk in stream:
                    chunk_count += 1
                    response_id = _safe_fragment(getattr(chunk, "id", None), 48)
                    if response_id:
                        response_ids.add(response_id)
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    choice_chunk_count += len(choices)
                    choice = choices[0]
                    finish_reason = _safe_fragment(getattr(choice, "finish_reason", None), 32)
                    if finish_reason:
                        finish_reasons.add(finish_reason)
                    delta = getattr(choice, "delta", None)
                    reasoning_chars += len(_reasoning_text(delta))
                    content = _content_text(getattr(delta, "content", None))
                    if not content:
                        continue
                    if emitted_text:
                        yield content
                        continue
                    pending_parts.append(content)
                    buffered = "".join(pending_parts)
                    if buffered.strip():
                        emitted_text = True
                        pending_parts.clear()
                        yield buffered
                if emitted_text:
                    return
                empty_diagnostics.append(
                    _stream_empty_diagnostic(
                        attempt + 1,
                        chunk_count,
                        choice_chunk_count,
                        reasoning_chars,
                        finish_reasons,
                        response_ids,
                    )
                )
            raise LLMError(_empty_response_detail(self, empty_diagnostics))
        except Exception as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError(_provider_failure_detail(self, exc)) from exc

    def generate_json(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        outputs: list[str] = []
        next_payload = user_payload
        last_error: json.JSONDecodeError | None = None
        json_mode_supported = True
        for attempt in range(JSON_REPAIR_ATTEMPTS + 1):
            text = self._generate_json_candidate(system_prompt, next_payload, json_mode_supported)
            if json_mode_supported and _response_format_unsupported(text):
                json_mode_supported = False
                text = self.generate_text(system_prompt, next_payload)
            outputs.append(text)
            try:
                return _loads_llm_json(text)
            except json.JSONDecodeError as exc:
                last_error = exc
                if attempt >= JSON_REPAIR_ATTEMPTS:
                    break
                next_payload = copy.deepcopy(user_payload)
                next_payload["_json_repair"] = {
                    "attempt": attempt + 1,
                    "max_attempts": JSON_REPAIR_ATTEMPTS,
                    "previous_output": _preview(text),
                    "parser_error": str(exc),
                    "instruction": (
                        "上一轮输出不是合法 JSON。请基于原始任务上下文重新输出完整、可解析的 JSON object。"
                        "字符串内部的双引号必须转义；不要输出 Markdown、解释、代码块或额外文本。"
                    ),
                }
        previews = "; ".join(f"attempt_{index + 1}_preview={_preview(output)!r}" for index, output in enumerate(outputs))
        raise LLMError(
            f"Model did not return valid JSON after {JSON_REPAIR_ATTEMPTS} repair attempts; {previews}"
        ) from last_error

    def _generate_json_candidate(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        json_mode_supported: bool,
    ) -> str:
        if not json_mode_supported:
            return self.generate_text(system_prompt, user_payload)
        try:
            return self.generate_text(
                system_prompt,
                user_payload,
                response_format={"type": "json_object"},
            )
        except TypeError:
            return self.generate_text(system_prompt, user_payload)
        except LLMError as exc:
            message = str(exc)
            if _response_format_unsupported(message):
                return message
            if _empty_response(message):
                return self.generate_text(system_prompt, user_payload)
            raise


def _completion_message_content(completion: Any) -> str:
    try:
        choice = completion.choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
    except (IndexError, TypeError, AttributeError):
        return ""
    return _content_text(content)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_content_part_text(item) for item in content)
    return _content_part_text(content)


def _content_part_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    text: Any = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(text, dict) and isinstance(text.get("value"), str):
        return text["value"]
    value = getattr(text, "value", None)
    return value if isinstance(value, str) else ""


def _completion_empty_diagnostic(completion: Any, attempt: int) -> str:
    choices = getattr(completion, "choices", None) or []
    response_id = _safe_fragment(getattr(completion, "id", None), 48) or "missing"
    if not choices:
        return f"attempt_{attempt}: response_id={response_id}, choices=0"
    choice = choices[0]
    message = getattr(choice, "message", None)
    finish_reason = _safe_fragment(getattr(choice, "finish_reason", None), 32) or "missing"
    refusal = _safe_fragment(getattr(message, "refusal", None), 80)
    reasoning_chars = len(_reasoning_text(message))
    tool_calls = getattr(message, "tool_calls", None) or []
    content = getattr(message, "content", None)
    content_shape = _content_shape(content)
    usage = getattr(completion, "usage", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    parts = [
        f"attempt_{attempt}: response_id={response_id}",
        f"choices={len(choices)}",
        f"finish_reason={finish_reason}",
        f"content={content_shape}",
        f"reasoning_chars={reasoning_chars}",
        f"tool_calls={len(tool_calls)}",
    ]
    if refusal:
        parts.append(f"refusal={refusal}")
    if completion_tokens is not None:
        parts.append(f"completion_tokens={completion_tokens}")
    return ", ".join(parts)


def _stream_empty_diagnostic(
    attempt: int,
    chunk_count: int,
    choice_chunk_count: int,
    reasoning_chars: int,
    finish_reasons: set[str],
    response_ids: set[str],
) -> str:
    return (
        f"attempt_{attempt}: stream_chunks={chunk_count}, choice_chunks={choice_chunk_count}, "
        f"finish_reason={','.join(sorted(finish_reasons)) or 'missing'}, text_chars=0, "
        f"reasoning_chars={reasoning_chars}, response_id={','.join(sorted(response_ids)) or 'missing'}"
    )


def _empty_response_detail(client: Any, diagnostics: list[str]) -> str:
    attempts = EMPTY_RESPONSE_RETRIES + 1
    model = _safe_fragment(getattr(client, "model", None), 80) or "unknown"
    endpoint = _endpoint_label(getattr(client, "base_url", None))
    response_details = " | ".join(diagnostics)
    return (
        f"{EMPTY_RESPONSE_MESSAGE} after {attempts} attempts; provider returned no usable message.content; "
        f"model={model}; endpoint={endpoint}; {response_details}"
    )


def _provider_failure_detail(client: Any, exc: Exception) -> str:
    model = _safe_fragment(getattr(client, "model", None), 80) or "unknown"
    endpoint = _endpoint_label(getattr(client, "base_url", None))
    timeout = getattr(client, "timeout_seconds", None)
    status_code = getattr(exc, "status_code", None)
    request_id = _safe_fragment(getattr(exc, "request_id", None), 64)
    error_type = type(exc).__name__
    message = _safe_fragment(exc, 240) or "no provider error message"
    provider_code = ""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error_body = body.get("error") if isinstance(body.get("error"), dict) else body
        provider_code = _safe_fragment(error_body.get("code") or error_body.get("type"), 64)
        provider_message = _safe_fragment(error_body.get("message"), 160)
        if provider_message and provider_message not in message:
            message = f"{message}; provider_message={provider_message}"
    details = [
        f"LLM provider request failed ({error_type})",
        f"message={message}",
        f"model={model}",
        f"endpoint={endpoint}",
    ]
    if status_code is not None:
        details.append(f"status_code={status_code}")
    if provider_code:
        details.append(f"provider_code={provider_code}")
    if request_id:
        details.append(f"request_id={request_id}")
    if timeout is not None:
        details.append(f"timeout_seconds={timeout}")
    return "; ".join(details)


def _content_shape(content: Any) -> str:
    if content is None:
        return "null"
    text = _content_text(content)
    if isinstance(content, str):
        return f"string({len(content)} chars{' whitespace' if content and not content.strip() else ''})"
    if isinstance(content, list):
        return f"list({len(content)} parts, {len(text)} text_chars)"
    return f"{type(content).__name__}({len(text)} text_chars)"


def _reasoning_text(value: Any) -> str:
    if value is None:
        return ""
    for key in ("reasoning_content", "reasoning", "thinking"):
        content = value.get(key) if isinstance(value, dict) else getattr(value, key, None)
        text = _content_text(content)
        if text:
            return text
    return ""


def _safe_fragment(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-***", text)
    text = re.sub(r"\bpt-[A-Za-z0-9_-]{8,}\b", "pt-***", text)
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|access[_-]?token|token)=([^&\s;]+)",
        r"\1=***",
        text,
    )
    return text[:limit]


def _endpoint_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    parsed = urlsplit(raw)
    if not parsed.hostname:
        return "configured-endpoint"
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return "configured-endpoint"
    if port:
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return _safe_fragment(f"{parsed.scheme or 'http'}://{host}{path}", 160)


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        return stripped[start : end + 1]
    return stripped


def _loads_llm_json(text: str) -> Any:
    candidate = _extract_json(text)
    last_error: json.JSONDecodeError | None = None
    seen: set[str] = set()
    for variant in _json_candidate_variants(candidate):
        if variant in seen:
            continue
        seen.add(variant)
        try:
            return json.loads(variant)
        except json.JSONDecodeError as exc:
            last_error = exc
    try:
        literal = ast.literal_eval(candidate)
    except (SyntaxError, ValueError):
        literal = None
    if isinstance(literal, (dict, list)):
        return literal
    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("Could not decode JSON", candidate, 0)


def _json_candidate_variants(text: str) -> tuple[str, ...]:
    stripped = text.strip()
    no_trailing_commas = _remove_trailing_commas(stripped)
    repaired_strings = _repair_json_string_content(stripped)
    repaired_strings_no_trailing = _remove_trailing_commas(repaired_strings)
    return (
        stripped,
        no_trailing_commas,
        repaired_strings,
        repaired_strings_no_trailing,
    )


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _repair_json_string_content(text: str) -> str:
    output: list[str] = []
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if not in_string:
            output.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == "\\":
            output.append(char)
            index += 1
            if index < len(text):
                output.append(text[index])
                index += 1
            continue
        if char == '"':
            if _quote_likely_closes_string(text, index):
                output.append(char)
                in_string = False
            else:
                output.append('\\"')
            index += 1
            continue
        if char == "\n":
            output.append("\\n")
        elif char == "\r":
            output.append("\\r")
        elif char == "\t":
            output.append("\\t")
        else:
            output.append(char)
        index += 1
    return "".join(output)


def _quote_likely_closes_string(text: str, quote_index: int) -> bool:
    index = quote_index + 1
    while index < len(text) and text[index].isspace():
        index += 1
    return index >= len(text) or text[index] in {":", ",", "}", "]"}


def _preview(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"


def _response_format_unsupported(message: str) -> bool:
    lowered = message.lower()
    return "response_format" in lowered and any(
        phrase in lowered
        for phrase in (
            "unsupported",
            "not support",
            "not_supported",
            "unknown parameter",
            "unrecognized",
            "extra inputs are not permitted",
            "invalid parameter",
        )
    )


def _empty_response(message: str) -> bool:
    return EMPTY_RESPONSE_MESSAGE.lower() in message.lower()


def model_supports_images(model_config_or_name: Any) -> bool:
    model = str(getattr(model_config_or_name, "model", model_config_or_name) or "").lower()
    provider = str(getattr(model_config_or_name, "provider", "") or "").lower()
    text = f"{provider} {model}"
    return any(re.search(pattern, text) for pattern in _VISION_MODEL_PATTERNS)


def _project_context_messages(user_payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = copy.deepcopy(user_payload)
    context = payload.get("conversation_context")
    if not isinstance(context, dict):
        return [], payload
    messages = context.pop("messages", [])
    if not isinstance(messages, list):
        return [], payload
    projected: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        images = _normalize_image_parts(message.get("images"))
        if role not in {"system", "user", "assistant"} or (not content and not images):
            continue
        if images and role == "user":
            projected.append(
                {
                    "role": role,
                    "content": [
                        {"type": "text", "text": content or "（用户上传了图片附件）"},
                        *images,
                    ],
                }
            )
        else:
            projected.append({"role": role, "content": content})
    return projected, payload


def _normalize_image_parts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    parts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "image_url" and isinstance(item.get("image_url"), dict):
            url = str(item["image_url"].get("url") or "").strip()
            if not url:
                continue
            image_url: dict[str, Any] = {"url": url}
            detail = str(item["image_url"].get("detail") or "").strip()
            if detail:
                image_url["detail"] = detail
            parts.append({"type": "image_url", "image_url": image_url})
    return parts


def _messages_include_images(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if any(isinstance(item, dict) and item.get("type") == "image_url" for item in content):
            return True
    return False
