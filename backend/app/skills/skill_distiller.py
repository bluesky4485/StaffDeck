from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from time import sleep
from typing import Any
from urllib.parse import urlparse

from app.db.models import ModelConfig
from app.llm import LLMClient, LLMError
from app.skills.llm_limits import skill_model_config
from app.skills.skill_schema import SkillDistillRequest, SkillDistillResponse, SkillCard, SkillStep, ToolSuggestion
from app.skills.step_ids import ensure_unique_step_ids, skill_card_with_unique_step_ids


PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "skill_distiller_prompt.md"
STREAM_INTERVAL_SECONDS = 0.035
MODEL_REPAIR_ATTEMPTS = 2
CLOSED_LOOP_RESPONSE_RULE = (
    "流程必须形成闭环：不得把“请稍候/正在处理/稍后反馈”作为最终回复；"
    "需要外部事实、外部状态或外部副作用时必须调用已配置工具或转人工，并向用户给出明确结果。"
)
ADAPTIVE_FLOW_RESPONSE_RULE = (
    "步骤是可自适应推进的目标，不是固定问答脚本；已由当前用户消息、历史信息或路由意图满足的内容"
    "不得重复追问，应直接推进到下一缺失信息、工具调用或最终回复。"
)
CONFIRMATION_FLOW_RESPONSE_RULE = (
    "涉及外部系统写入、用户资产变更、不可逆操作或明确需要确认的处理时，"
    "调用工具或执行处理前必须先让用户确认关键对象、范围和操作内容。"
)
TOOL_STEP_INSTRUCTION_SUFFIX = (
    "工具参数满足时直接调用工具；工具成功后必须基于工具结果进入最终回复，"
    "不要停留在“请稍候”或“正在处理”。"
)
ADAPTIVE_STEP_INSTRUCTION_SUFFIX = (
    "将本步骤作为目标而不是固定话术；如果用户当前消息、历史 slots 或路由意图已满足本步骤，"
    "直接写入对应 slot 并继续到下一缺失信息、工具调用或最终回复，不要重复确认。"
)
class SkillDistiller:
    def distill(self, request: SkillDistillRequest, model_config: ModelConfig) -> SkillDistillResponse:
        return self._generate_response(request, model_config)

    def distill_stream(self, request: SkillDistillRequest, model_config: ModelConfig) -> SkillDistillResponse:
        return self._generate_response(request, model_config)

    def stream_text(self, request: SkillDistillRequest, model_config: ModelConfig):
        payload = self._payload(request)
        chunks: list[str] = []
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        client = LLMClient(skill_model_config(model_config))
        try:
            yield {"event": "status", "data": {"text": "模型正在规划技能结构"}}
            for chunk in client.generate_text_stream(prompt, payload):
                chunks.append(chunk)
                yield {"event": "chunk", "data": {"content": chunk}}
            yield {"event": "status", "data": {"text": "正在校验模型输出结构"}}
            response = self._response_from_text("".join(chunks), request)
        except (LLMError, json.JSONDecodeError, TypeError, ValueError) as exc:
            try:
                yield {"event": "status", "data": {"text": "模型输出需要修复，正在重试"}}
                response = self._repair_response(client, prompt, payload, "".join(chunks), str(exc), request)
            except (LLMError, json.JSONDecodeError, TypeError, ValueError) as repair_exc:
                try:
                    yield {"event": "status", "data": {"text": "模型修复失败，改用分段生成"}}
                    response = self._staged_response(client, prompt, payload, request, str(repair_exc))
                except (LLMError, json.JSONDecodeError, TypeError, ValueError) as staged_exc:
                    yield {"event": "status", "data": {"text": "模型多轮生成失败，使用最低可运行草稿"}}
                    response = self._fallback_response(
                        request, f"模型多轮生成未能完成，已使用最低可运行草稿：{staged_exc}"
                    )
            yield {"event": "chunk_reset", "data": {}}
            for chunk in _chunk_text(_serialize_response_for_stream(response)):
                yield {"event": "chunk", "data": {"content": chunk}}
                sleep(STREAM_INTERVAL_SECONDS)
        yield {"event": "status", "data": {"text": "已完成 Skill Card 结构化"}}
        yield {"event": "complete", "data": response.model_dump(mode="json")}

    def _generate_response(self, request: SkillDistillRequest, model_config: ModelConfig) -> SkillDistillResponse:
        payload = self._payload(request)
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        client = LLMClient(skill_model_config(model_config))
        output = ""
        try:
            output = client.generate_text(prompt, payload)
            return self._response_from_text(output, request)
        except (LLMError, json.JSONDecodeError, TypeError, ValueError) as exc:
            try:
                return self._repair_response(client, prompt, payload, output, str(exc), request)
            except (LLMError, json.JSONDecodeError, TypeError, ValueError) as repair_exc:
                try:
                    return self._staged_response(client, prompt, payload, request, str(repair_exc))
                except (LLMError, json.JSONDecodeError, TypeError, ValueError) as staged_exc:
                    return self._fallback_response(
                        request, f"模型多轮生成未能完成，已使用最低可运行草稿：{staged_exc}"
                    )

    def _response_from_text(self, text: str, request: SkillDistillRequest) -> SkillDistillResponse:
        raw = _raw_json_from_text(text)
        return self._normalize_response(raw, request)

    def _repair_response(
        self,
        client: LLMClient,
        prompt: str,
        payload: dict[str, Any],
        previous_output: str,
        previous_error: str,
        request: SkillDistillRequest,
    ) -> SkillDistillResponse:
        output = previous_output
        error = previous_error
        for attempt in range(MODEL_REPAIR_ATTEMPTS):
            repair_payload = {
                **payload,
                "previous_output": output,
                "previous_error": error,
                "repair_attempt": attempt + 1,
                "repair_instruction": (
                    "上一次输出无法解析或未通过 Skill Card 校验。请修复为完整合法 JSON。"
                    "不要解释，不要使用代码围栏。必须保留原始流程中的步骤、工具建议和闭环约束。"
                ),
            }
            output = client.generate_text(prompt, repair_payload)
            try:
                return self._response_from_text(output, request)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                error = str(exc)
        raise ValueError(error)

    def _staged_response(
        self,
        client: LLMClient,
        prompt: str,
        payload: dict[str, Any],
        request: SkillDistillRequest,
        previous_error: str,
    ) -> SkillDistillResponse:
        outline_text = client.generate_text(
            prompt,
            {
                **payload,
                "generation_mode": "outline_only",
                "previous_error": previous_error,
                "generation_instruction": (
                    "先生成完整但紧凑的 Skill Card 大纲。steps 必须覆盖原始流程全部步骤，"
                    "每个 instruction 只写一句目标说明；保留 response_rules、slot_filling_policy、"
                    "interruption_policy 和 tool_mentions。只输出 JSON。"
                ),
            },
        )
        outline = self._response_from_text(outline_text, request)
        draft_data = outline.draft_skill.model_dump(mode="json")
        warnings = list(outline.warnings)
        tool_mentions = [item.model_dump(mode="json") for item in outline.tool_suggestions]
        steps = [step for step in draft_data.get("steps", []) if isinstance(step, dict)]

        for index, step in enumerate(steps):
            step_text = client.generate_text(
                prompt,
                {
                    **payload,
                    "generation_mode": "expand_step",
                    "current_draft": draft_data,
                    "target_step_index": index,
                    "target_step": step,
                    "generation_instruction": (
                        "只扩写 target_step。输出 JSON：{\"step\": {...}, \"warnings\": [], "
                        "\"tool_mentions\": []}。step 必须包含 step_id、name、instruction、"
                        "expected_user_info、allowed_actions。不要输出完整技能。"
                    ),
                },
            )
            try:
                step_raw = _raw_json_from_text(step_text)
                step_data = step_raw.get("step") if isinstance(step_raw.get("step"), dict) else step_raw
                steps[index] = SkillStep.model_validate(step_data).model_dump(mode="json")
                warnings.extend(str(item) for item in step_raw.get("warnings", []) if str(item).strip())
                if isinstance(step_raw.get("tool_mentions"), list):
                    tool_mentions.extend(item for item in step_raw["tool_mentions"] if isinstance(item, dict))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                warnings.append(f"模型未能扩写步骤 {index + 1}，已保留大纲步骤：{exc}")

        draft_data["steps"] = steps
        reviewed = self._normalize_response(
            {"draft_skill": draft_data, "warnings": warnings, "tool_mentions": tool_mentions},
            request,
        )
        review_text = client.generate_text(
            prompt,
            {
                **payload,
                "generation_mode": "final_review",
                "current_draft": reviewed.draft_skill.model_dump(mode="json"),
                "generation_instruction": (
                    "检查 current_draft 是否遗漏原始流程、闭环回复、工具建议或中断策略。"
                    "如需修正，返回完整 draft_skill；如果无需修正，也返回完整 draft_skill。只输出 JSON。"
                ),
            },
        )
        try:
            return self._response_from_text(review_text, request)
        except (json.JSONDecodeError, TypeError, ValueError):
            return reviewed

    def _payload(self, request: SkillDistillRequest) -> dict[str, Any]:
        return {
            "title": request.title,
            "business_domain": request.business_domain,
            "raw_content": request.raw_content,
            "available_tools": request.available_tools,
        }

    def _normalize_response(self, raw: dict[str, Any], request: SkillDistillRequest) -> SkillDistillResponse:
        draft = raw.get("draft_skill") if isinstance(raw.get("draft_skill"), dict) else raw
        warnings = list(raw.get("warnings") or [])
        fallback = self._fallback_card(request)

        required_info = _string_list(draft.get("required_info"), fallback.required_info)
        steps = self._normalize_steps(draft.get("steps"), fallback.steps)
        steps, step_warnings = self._ensure_closed_loop_steps(steps, request)
        warnings.extend(step_warnings)
        steps, unique_step_warnings = ensure_unique_step_ids(steps)
        warnings.extend(unique_step_warnings)
        steps, missing_tool_names = _remove_unknown_tool_actions(steps, request.available_tools)
        for tool_name in missing_tool_names:
            warnings.append(
                f"技能草稿引用了未配置工具 {tool_name}，已移出 allowed_actions；"
                "如确需该工具，模型必须在 tool_mentions 中提供来自原文的完整工具提及。"
            )
        response_rules = _string_list(draft.get("response_rules"), fallback.response_rules)
        if CLOSED_LOOP_RESPONSE_RULE not in response_rules:
            response_rules.append(CLOSED_LOOP_RESPONSE_RULE)
        if ADAPTIVE_FLOW_RESPONSE_RULE not in response_rules:
            response_rules.append(ADAPTIVE_FLOW_RESPONSE_RULE)
        if _steps_declare_confirmation(steps) and CONFIRMATION_FLOW_RESPONSE_RULE not in response_rules:
            response_rules.append(CONFIRMATION_FLOW_RESPONSE_RULE)
        normalized = {
            "skill_id": _string(draft.get("skill_id"), fallback.skill_id),
            "name": _string(draft.get("name"), fallback.name),
            "version": _string(draft.get("version"), "1.0.0"),
            "business_domain": _string(draft.get("business_domain"), fallback.business_domain or "general"),
            "description": _string(draft.get("description"), fallback.description),
            "trigger_intents": _string_list(draft.get("trigger_intents"), fallback.trigger_intents),
            "user_utterance_examples": _string_list(
                draft.get("user_utterance_examples"), fallback.user_utterance_examples
            ),
            "goal": _string_list(draft.get("goal"), fallback.goal),
            "required_info": required_info,
            "slot_filling_policy": _slot_filling_policy(
                draft.get("slot_filling_policy"),
                required_info,
                steps,
                fallback.slot_filling_policy,
            ),
            "response_rules": response_rules,
            "steps": steps,
            "interruption_policy": _string_dict(draft.get("interruption_policy"), fallback.interruption_policy),
        }
        draft_skill, card_warnings = skill_card_with_unique_step_ids(SkillCard.model_validate(normalized))
        warnings.extend(card_warnings)
        tool_resolutions = _normalize_tool_suggestions(
            raw.get("tool_mentions") if isinstance(raw.get("tool_mentions"), list) else raw.get("tool_suggestions"),
            request,
            missing_tool_names,
        )
        warnings.extend(_tool_resolution_warnings(tool_resolutions))
        tool_suggestions = [
            item for item in tool_resolutions if item.resolution_status in {"existing", "new_candidate"}
        ]
        response = SkillDistillResponse(
            draft_skill=draft_skill,
            warnings=_compact_warnings(warnings),
            tool_suggestions=tool_suggestions,
        )
        if not response.draft_skill.steps:
            response.draft_skill.steps = fallback.steps
            response.warnings = _compact_warnings([*response.warnings, "模型未生成步骤，已使用规则生成默认步骤。"])
        return response

    def _ensure_closed_loop_steps(
        self, steps: list[dict[str, Any]], request: SkillDistillRequest
    ) -> tuple[list[dict[str, Any]], list[str]]:
        normalized_steps = [dict(step) for step in steps]
        warnings: list[str] = []
        _attach_declared_confirmation_to_tool_steps(normalized_steps)

        for step in normalized_steps:
            _ensure_adaptive_step_instruction(step)
            actions = [str(action) for action in step.get("allowed_actions", [])]
            if not any(action.startswith("call_tool:") for action in actions):
                continue
            if "continue_flow" not in actions:
                actions.append("continue_flow")
                step["allowed_actions"] = actions
            instruction = str(step.get("instruction") or "")
            if "工具成功后" not in instruction:
                step["instruction"] = f"{instruction}{TOOL_STEP_INSTRUCTION_SUFFIX}"

        if not _last_step_allows_answer(normalized_steps):
            normalized_steps.append(
                {
                    "step_id": _unique_step_id(normalized_steps, "reply_final_result"),
                    "name": "反馈最终结果",
                    "instruction": (
                        "基于已收集信息和工具结果给用户明确最终回复；"
                        "信息不足时追问缺失信息，无法闭环时转人工，不要只说请稍候；"
                        f"{ADAPTIVE_STEP_INSTRUCTION_SUFFIX}"
                    ),
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user", "handoff_human"],
                }
            )
            warnings.append("原始改写缺少最终回复步骤，已补充闭环反馈步骤。")
        else:
            last_step = normalized_steps[-1]
            instruction = str(last_step.get("instruction") or "")
            if "明确" not in instruction or "请稍候" in instruction:
                last_step["instruction"] = (
                    f"{instruction}给用户明确最终回复；无法闭环时转人工，不要只说请稍候。"
                )

        return normalized_steps, warnings

    def _normalize_steps(self, value: Any, fallback_steps: list[SkillStep]) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return [step.model_dump() for step in fallback_steps]
        steps: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            fallback = fallback_steps[min(index, len(fallback_steps) - 1)]
            steps.append(
                {
                    "step_id": _string(item.get("step_id"), fallback.step_id),
                    "name": _string(item.get("name"), fallback.name),
                    "instruction": _string(item.get("instruction"), fallback.instruction),
                    "expected_user_info": _string_list(
                        item.get("expected_user_info"), fallback.expected_user_info
                    ),
                    "allowed_actions": _normalize_actions(
                        _string_list(item.get("allowed_actions"), fallback.allowed_actions)
                    ),
                }
            )
        return steps or [step.model_dump() for step in fallback_steps]

    def _fallback_response(self, request: SkillDistillRequest, warning: str) -> SkillDistillResponse:
        return SkillDistillResponse(draft_skill=self._fallback_card(request), warnings=_compact_warnings([warning]))

    def _fallback_card(self, request: SkillDistillRequest) -> SkillCard:
        title = request.title.strip() or "新技能"
        raw = request.raw_content
        required_info: list[str] = []
        steps: list[SkillStep] = []
        steps.append(
            SkillStep(
                step_id="understand_request",
                name="理解原始流程",
                instruction=(
                    "根据原始流程文档理解用户目标、缺失信息和下一步处理方式；"
                    "不要基于固定话术推进，信息不足时追问，涉及外部事实或外部副作用时转人工或等待人工补充工具配置；"
                    f"{ADAPTIVE_STEP_INSTRUCTION_SUFFIX}"
                ),
                expected_user_info=[],
                allowed_actions=["ask_user", "continue_flow", "handoff_human"],
            )
        )
        steps.append(
            SkillStep(
                step_id="reply_result",
                name="反馈结果",
                instruction=(
                    "根据已收集的信息和工具结果给用户明确回复；信息不足时继续追问，不要编造事实；"
                    f"{ADAPTIVE_STEP_INSTRUCTION_SUFFIX}"
                ),
                expected_user_info=[],
                allowed_actions=["answer_user", "handoff_human"],
            )
        )
        return SkillCard(
            skill_id=_slugify(title, raw),
            name=title,
            version="1.0.0",
            business_domain=request.business_domain or "general",
            description=raw[:120] or "根据原始技能文本生成的流程。",
            trigger_intents=[title],
            user_utterance_examples=[title],
            goal=_infer_goals(raw),
            required_info=required_info,
            slot_filling_policy=_default_slot_filling_policy(required_info),
            response_rules=[
                "信息不足时先追问，不要编造事实。",
                ADAPTIVE_FLOW_RESPONSE_RULE,
            ],
            steps=steps,
            interruption_policy={
                "related_question": "回答相关问题后回到当前流程。",
                "unrelated_business": "可切换新流程并保留当前进度。",
                "chitchat": "简短回应后引导用户继续当前流程。",
                "user_wants_human": "直接转人工。",
            },
        )


def _steps_have_tool_action(steps: list[dict[str, Any]]) -> bool:
    for step in steps:
        actions = step.get("allowed_actions", [])
        if isinstance(actions, list) and any(str(action).startswith("call_tool:") for action in actions):
            return True
    return False


def _ensure_adaptive_step_instruction(step: dict[str, Any]) -> None:
    instruction = str(step.get("instruction") or "")
    if "目标而不是固定话术" in instruction or "不是固定问答脚本" in instruction:
        return
    step["instruction"] = f"{instruction}{ADAPTIVE_STEP_INSTRUCTION_SUFFIX}"


def _confirmation_fields(steps: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for step in steps:
        expected = [str(field) for field in step.get("expected_user_info", [])]
        for field in expected:
            if field.endswith("_confirmed") and field not in fields:
                fields.append(field)
    return fields


def _steps_declare_confirmation(steps: list[dict[str, Any]]) -> bool:
    return bool(_confirmation_fields(steps))


def _attach_declared_confirmation_to_tool_steps(steps: list[dict[str, Any]]) -> None:
    confirmed_fields: list[str] = []
    for step in steps:
        if any(str(action).startswith("call_tool:") for action in step.get("allowed_actions", [])):
            _append_tool_confirmation_instruction(step, confirmed_fields)
        for field in _confirmation_fields([step]):
            if field not in confirmed_fields:
                confirmed_fields.append(field)


def _append_tool_confirmation_instruction(step: dict[str, Any], confirmation_fields: list[str]) -> None:
    if not confirmation_fields:
        return
    instruction = str(step.get("instruction") or "")
    if "确认字段" in instruction or "confirmed=true" in instruction:
        return
    field_text = "、".join(f"{field}=true" for field in confirmation_fields)
    step["instruction"] = f"{instruction}调用工具前必须确认字段已满足：{field_text}。"


def _last_step_allows_answer(steps: list[dict[str, Any]]) -> bool:
    if not steps:
        return False
    actions = [str(action) for action in steps[-1].get("allowed_actions", [])]
    return "answer_user" in actions or "reply" in actions


def _unique_step_id(steps: list[dict[str, Any]], base: str) -> str:
    existing = {str(step.get("step_id") or "") for step in steps}
    if base not in existing:
        return base
    index = 2
    while f"{base}_{index}" in existing:
        index += 1
    return f"{base}_{index}"


def _unique_warnings(warnings: list[str]) -> list[str]:
    deduped: list[str] = []
    for warning in warnings:
        text = str(warning).strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _compact_warnings(warnings: list[str]) -> list[str]:
    return _unique_warnings([_compact_warning(str(warning)) for warning in warnings if str(warning).strip()])


def _compact_warning(warning: str) -> str:
    text = warning.strip()
    tool_name = _warning_tool_name(text)
    if tool_name and (
        "未配置工具" in text
        or "available_tools" in text
        or "tool_suggestions" in text
        or "tool_mentions" in text
        or "allowed_actions" in text
    ):
        return f"未配置工具 {tool_name}，已移出调用动作；需在原文中提供完整工具接口信息。"
    if "没有任何工具支持" in text or ("available_tools" in text and "工具" in text):
        return "缺少可用工具，需先新增工具后再执行该流程。"
    replacements = (
        ("原始改写未包含工具步骤，已按可用工具补充闭环执行步骤。", "已补充工具执行步骤。"),
        ("原始改写缺少执行前确认步骤，已补充确认步骤。", "已补充执行前确认步骤。"),
        ("原始改写缺少最终回复步骤，已补充闭环反馈步骤。", "已补充最终回复步骤。"),
        ("模型未生成步骤，已使用规则生成默认步骤。", "已生成默认步骤。"),
    )
    for source, target in replacements:
        if text == source:
            return target
    return text


def _warning_tool_name(text: str) -> str:
    patterns = (
        r"未配置工具\s+`?([A-Za-z0-9_.:-]+)`?",
        r"工具\s+`?([A-Za-z0-9_.:-]+)`?\s+不在",
        r"引用了未配置工具\s+`?([A-Za-z0-9_.:-]+)`?",
        r"提到了工具\s+`?([A-Za-z0-9_.:-]+)`?",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip("`，。,. ")
    return ""


def _request_text(request: Any) -> str:
    return f"{_request_title(request)}\n{_request_raw_content(request)}"


def _request_title(request: Any) -> str:
    title = getattr(request, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    current_skill = getattr(request, "current_skill", None)
    name = getattr(current_skill, "name", None)
    return str(name or "新技能").strip()


def _request_raw_content(request: Any) -> str:
    raw_content = getattr(request, "raw_content", None)
    if isinstance(raw_content, str) and raw_content.strip():
        return raw_content
    instruction = getattr(request, "instruction", None)
    return str(instruction or "")


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


def _raw_json_from_text(text: str) -> dict[str, Any]:
    raw = json.loads(_extract_json(text))
    if not isinstance(raw, dict):
        raise ValueError("模型输出不是 JSON object")
    return raw


def _serialize_response_for_stream(response: SkillDistillResponse) -> str:
    return json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _chunk_text(text: str, size: int = 18):
    for index in range(0, len(text), size):
        yield text[index : index + size]


def _string(value: Any, fallback: str | None = "") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback or ""


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        if items:
            return items
    return fallback


def _string_dict(value: Any, fallback: dict[str, str]) -> dict[str, str]:
    if isinstance(value, dict):
        items = {str(key): str(item) for key, item in value.items() if str(key)}
        if items:
            return items
    return fallback


def _slot_filling_policy(
    value: Any,
    required_info: list[str],
    steps: list[dict[str, Any]],
    fallback_policy: dict[str, Any],
) -> dict[str, Any]:
    has_explicit_policy = isinstance(value, dict)
    if has_explicit_policy:
        policy = dict(value)
    else:
        policy = dict(fallback_policy or {})
    expected_infos = set(required_info)
    for step in steps:
        expected_infos.update(str(field) for field in step.get("expected_user_info", []))
    if has_explicit_policy and isinstance(policy.get("target_info"), list):
        expected_infos.update(str(field) for field in policy["target_info"] if str(field).strip())
    default_policy = _default_slot_filling_policy(sorted(expected_infos))
    return {
        **default_policy,
        **policy,
        "enabled": True,
        "multi_slot_per_turn": True,
        "extract_scope": "all_skill_expected_user_info",
        "skip_satisfied_steps": True,
        "target_info": sorted(expected_infos),
    }


def _default_slot_filling_policy(expected_infos: list[str]) -> dict[str, Any]:
    return {
        "enabled": True,
        "multi_slot_per_turn": True,
        "extract_scope": "all_skill_expected_user_info",
        "skip_satisfied_steps": True,
        "description": "每轮用户消息都应同时抽取所有可识别的信息；如果用户一次提供多个字段，必须一次性写入 slot_updates，不要按步骤重复追问。",
        "target_info": expected_infos,
    }


def _normalize_actions(actions: list[str]) -> list[str]:
    aliases = {
        "ask_for_info": "ask_user",
        "ask": "ask_user",
        "reply": "answer_user",
        "respond": "answer_user",
    }
    normalized: list[str] = []
    for action in actions:
        value = aliases.get(action, action)
        if value not in normalized:
            normalized.append(value)
    return normalized


def _available_tool_names(available_tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in available_tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _remove_unknown_tool_actions(
    steps: list[dict[str, Any]], available_tools: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    available_names = _available_tool_names(available_tools)
    missing_names: list[str] = []
    if not available_names:
        available_names = set()
    normalized_steps: list[dict[str, Any]] = []
    for step in steps:
        next_step = dict(step)
        actions = []
        for action in next_step.get("allowed_actions", []):
            action_text = str(action)
            if not action_text.startswith("call_tool:"):
                actions.append(action_text)
                continue
            tool_name = action_text.replace("call_tool:", "", 1).strip()
            if tool_name in available_names:
                actions.append(action_text)
                continue
            if tool_name and tool_name not in missing_names:
                missing_names.append(tool_name)
        next_step["allowed_actions"] = actions
        normalized_steps.append(next_step)
    return normalized_steps, missing_names


def _normalize_tool_suggestions(
    value: Any, request: Any, missing_tool_names: list[str]
) -> list[ToolSuggestion]:
    suggestions: list[ToolSuggestion] = []
    seen: set[str] = set()

    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            suggestion = _tool_mention_to_resolution(item, request)
            if suggestion is None:
                continue
            if suggestion.name in seen:
                continue
            suggestions.append(suggestion)
            seen.add(suggestion.name)

    return suggestions


def _tool_resolution_warnings(suggestions: list[ToolSuggestion]) -> list[str]:
    warnings: list[str] = []
    for suggestion in suggestions:
        if suggestion.resolution_status != "incomplete":
            continue
        label = suggestion.display_name or suggestion.name
        reason = suggestion.missing_reason or "缺少完整接口信息"
        warnings.append(f"模型提到了可能的工具「{label}」，但当前不能新增：{reason}。")
    return warnings


def _tool_mention_to_resolution(item: dict[str, Any], request: Any) -> ToolSuggestion | None:
    name = _string(item.get("name"), "") or _string(item.get("inferred_name"), "")
    display_name = _string(item.get("display_name"), "") or _string(item.get("label"), "")
    description = _string(item.get("description"), "") or _string(item.get("purpose"), "")
    url = _string(item.get("url"), "")
    method = _tool_method(item.get("method"), "POST")
    input_schema = item.get("input_schema")
    output_schema = item.get("output_schema")
    source_excerpt = _string(item.get("source_excerpt"), "") or None
    reason = _string(item.get("reason"), "") or _string(item.get("purpose"), "") or "模型从技能文档中抽取到该工具提及。"

    matched_tool = _match_available_tool(name, url, request.available_tools)
    if matched_tool is not None:
        matched_name = _string(matched_tool.get("name"), name)
        return ToolSuggestion(
            name=matched_name,
            display_name=_string(matched_tool.get("display_name"), display_name or matched_name),
            description=_string(matched_tool.get("description"), description),
            method=_tool_method(matched_tool.get("method"), method),
            url=_string(matched_tool.get("url"), url),
            input_schema=matched_tool.get("input_schema") if isinstance(matched_tool.get("input_schema"), dict) else {},
            output_schema=matched_tool.get("output_schema") if isinstance(matched_tool.get("output_schema"), dict) else {},
            sample_arguments=item.get("sample_arguments") if isinstance(item.get("sample_arguments"), dict) else {},
            source_excerpt=source_excerpt,
            probe_result=item.get("probe_result") if isinstance(item.get("probe_result"), dict) else None,
            reason="已匹配到现有工具配置。",
            resolution_status="existing",
            matched_tool_id=_string(matched_tool.get("id"), "") or None,
            matched_tool_name=matched_name,
            matched_tool_display_name=_string(matched_tool.get("display_name"), "") or None,
        )

    if not name and not display_name and not url:
        return None

    missing_reasons = _tool_mention_missing_reasons(url, input_schema, output_schema, request)
    if missing_reasons:
        return ToolSuggestion(
            name=name or _tool_name_from_url(url) or display_name or "incomplete_tool",
            display_name=display_name or name or _tool_name_from_url(url) or "未完整配置的工具",
            description=description,
            method=method,
            url=url if _tool_suggestion_url_in_source(url, request) else "",
            input_schema=input_schema if isinstance(input_schema, dict) else {},
            output_schema=output_schema if isinstance(output_schema, dict) else {},
            sample_arguments=item.get("sample_arguments") if isinstance(item.get("sample_arguments"), dict) else {},
            source_excerpt=source_excerpt,
            probe_result=item.get("probe_result") if isinstance(item.get("probe_result"), dict) else None,
            reason=reason,
            resolution_status="incomplete",
            missing_reason="；".join(missing_reasons),
        )

    return ToolSuggestion(
        name=name or _tool_name_from_url(url),
        display_name=display_name or name or _tool_name_from_url(url),
        description=description,
        method=method,
        url=url,
        input_schema=input_schema,
        output_schema=output_schema,
        sample_arguments=item.get("sample_arguments") if isinstance(item.get("sample_arguments"), dict) else {},
        source_excerpt=source_excerpt,
        probe_result=item.get("probe_result") if isinstance(item.get("probe_result"), dict) else None,
        reason=reason,
        resolution_status="new_candidate",
    )


def _tool_mention_missing_reasons(url: str, input_schema: Any, output_schema: Any, request: Any) -> list[str]:
    reasons: list[str] = []
    if not url:
        reasons.append("缺少可访问接口地址或路径")
    elif not _tool_suggestion_url_in_source(url, request):
        reasons.append("接口地址未在技能原文或改写上下文中出现")
    if not isinstance(input_schema, dict) or not input_schema:
        reasons.append("缺少输入参数结构")
    if not isinstance(output_schema, dict) or not output_schema:
        reasons.append("缺少返回结果结构")
    return reasons


def _match_available_tool(name: str, url: str, available_tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    name_text = name.strip()
    url_candidates = set(_tool_url_candidates(url))
    for tool in available_tools:
        if not isinstance(tool, dict):
            continue
        tool_name = _string(tool.get("name"), "")
        if name_text and tool_name and name_text == tool_name:
            return tool
        tool_url = _string(tool.get("url"), "")
        if tool_url and url_candidates.intersection(_tool_url_candidates(tool_url)):
            return tool
    return None


def _tool_name_from_url(url: str) -> str:
    candidates = _tool_url_candidates(url)
    path = candidates[-1] if candidates else url
    text = path.strip("/").replace("-", "_").replace("/", ".")
    text = re.sub(r"[^A-Za-z0-9_.]+", "_", text).strip("._")
    return text or "tool_candidate"


def _tool_suggestion_url_in_source(url: str, request: Any) -> bool:
    source = _tool_suggestion_source_text(request)
    if not source:
        return False
    return any(candidate in source for candidate in _tool_url_candidates(url))


def _tool_suggestion_source_text(request: Any) -> str:
    parts: list[str] = []
    for attr in ("raw_content", "instruction", "title", "business_domain", "target_label"):
        value = getattr(request, attr, None)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    current_skill = getattr(request, "current_skill", None)
    if current_skill is not None:
        try:
            parts.append(json.dumps(current_skill.model_dump(mode="json"), ensure_ascii=False))
        except (TypeError, ValueError, AttributeError):
            parts.append(str(current_skill))
    conversation = getattr(request, "conversation", None)
    if isinstance(conversation, list):
        for item in conversation[-12:]:
            if isinstance(item, dict):
                content = item.get("content")
                if isinstance(content, str) and content.strip():
                    parts.append(content)
    return "\n".join(parts)


def _tool_url_candidates(url: str) -> list[str]:
    normalized = url.strip().strip("`'\"<>，。；;,")
    if not normalized:
        return []
    candidates = {normalized}
    parsed_source = normalized
    if normalized.startswith("/"):
        parsed_source = f"http://placeholder{normalized}"
    parsed = urlparse(parsed_source)
    if parsed.path and len(parsed.path.strip("/")) >= 3:
        candidates.add(parsed.path.rstrip("/") or parsed.path)
    if not normalized.startswith("/") and "/" in normalized and "://" not in normalized:
        candidates.add(f"/{normalized.lstrip('/')}")
    return sorted({item for item in candidates if len(item.strip("/")) >= 3}, key=len, reverse=True)


def _tool_method(value: Any, fallback: str = "POST") -> str:
    method = str(value or fallback or "POST").upper()
    return method if method in {"GET", "POST", "PUT", "PATCH", "DELETE"} else "POST"


def _infer_goals(raw: str) -> list[str]:
    clauses = [clause.strip() for clause in _split_clauses(raw) if clause.strip()]
    return clauses or ["理解用户诉求", "收集必要信息", "完成流程处理", "向用户反馈结果"]


def _split_clauses(text: str) -> list[str]:
    normalized = text.replace("\n", "，").replace("；", "，").replace(";", "，").replace(",", "，").replace("。", "，")
    return [part.strip() for part in normalized.split("，")]


def _slugify(title: str, raw: str) -> str:
    ascii_slug = "".join(char.lower() if char.isalnum() else "_" for char in title if ord(char) < 128)
    ascii_slug = "_".join(part for part in ascii_slug.split("_") if part)
    if ascii_slug:
        return ascii_slug[:48]
    digest = hashlib.md5(f"{title}:{raw}".encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"skill_{digest}"
