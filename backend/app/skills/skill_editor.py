from __future__ import annotations

import json
from pathlib import Path
import re
from time import sleep
from typing import Any, Iterator

from app.db.models import ModelConfig
from app.llm import LLMClient, LLMError
from app.skills.llm_limits import skill_model_config
from app.skills.skill_schema import SkillCard, SkillRewriteRequest, SkillRewriteResponse
from app.skills.skill_distiller import (
    _compact_warnings,
    _normalize_tool_suggestions,
    _remove_unknown_tool_actions,
    _tool_resolution_warnings,
)
from app.skills.step_ids import skill_card_with_unique_step_ids


PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "skill_editor_prompt.md"
STREAM_INTERVAL_SECONDS = 0.035
BASIC_FIELDS = {
    "name",
    "version",
    "business_domain",
    "description",
    "trigger_intents",
    "user_utterance_examples",
    "goal",
    "required_info",
    "slot_filling_policy",
    "interruption_policy",
    "response_rules",
}
STEP_FIELDS = {"step_id", "name", "instruction", "expected_user_info", "allowed_actions"}


class SkillEditor:
    def rewrite(self, request: SkillRewriteRequest, model_config: ModelConfig) -> SkillRewriteResponse:
        raw = LLMClient(skill_model_config(model_config)).generate_json(
            PROMPT_PATH.read_text(encoding="utf-8"), self._payload(request)
        )
        return self._normalize_response(raw, request)

    def stream_text(
        self, request: SkillRewriteRequest, model_config: ModelConfig
    ) -> Iterator[dict[str, object]]:
        chunks: list[str] = []
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        payload = self._payload(request)
        client = LLMClient(skill_model_config(model_config))
        try:
            yield {"event": "status", "data": {"text": "模型正在分析改写范围"}}
            for chunk in client.generate_text_stream(prompt, payload):
                chunks.append(chunk)
            yield {"event": "status", "data": {"text": "正在校验局部改写结果"}}
            response = self._response_from_text("".join(chunks), request)
        except (LLMError, json.JSONDecodeError, TypeError, ValueError) as exc:
            try:
                yield {"event": "status", "data": {"text": "模型输出需要修复，正在重试一次"}}
                repair_text = client.generate_text(
                    prompt,
                    {
                        **payload,
                        "previous_output": "".join(chunks),
                        "previous_error": str(exc),
                        "repair_instruction": (
                            "请基于 current_skill、instruction 和 target_paths 修复上一次输出。"
                            "只输出合法 JSON，可以使用 patches 做局部修改，或返回完整 draft_skill。"
                        ),
                    },
                )
                response = self._response_from_text(repair_text, request)
            except (LLMError, json.JSONDecodeError, TypeError, ValueError) as repair_exc:
                yield {"event": "status", "data": {"text": "模型改写失败，正在保留原版本"}}
                response = SkillRewriteResponse(
                    draft_skill=request.current_skill,
                    assistant_message="改写失败，已保留当前技能内容。",
                    changed_paths=[],
                    warnings=[f"模型未能完成局部改写：{repair_exc}"],
                )
        for chunk in _chunk_text(response.assistant_message):
            yield {"event": "message_chunk", "data": {"content": chunk}}
            sleep(STREAM_INTERVAL_SECONDS)
        yield {"event": "complete", "data": response.model_dump(mode="json")}

    def _response_from_text(self, text: str, request: SkillRewriteRequest) -> SkillRewriteResponse:
        raw = json.loads(_extract_json(text))
        if not isinstance(raw, dict):
            raise ValueError("模型输出不是 JSON object")
        return self._normalize_response(raw, request)

    def _payload(self, request: SkillRewriteRequest) -> dict[str, Any]:
        return {
            "current_skill": request.current_skill.model_dump(mode="json"),
            "instruction": request.instruction,
            "target_path": request.target_path,
            "target_paths": _target_paths(request),
            "target_label": request.target_label,
            "conversation": request.conversation[-12:],
            "available_tools": request.available_tools,
        }

    def _normalize_response(
        self, raw: dict[str, Any], request: SkillRewriteRequest
    ) -> SkillRewriteResponse:
        target_paths = _target_paths(request)
        patched = _skill_from_patches(raw, request, target_paths)
        draft = (
            patched.model_dump(mode="json")
            if patched is not None
            else raw.get("draft_skill")
            if isinstance(raw.get("draft_skill"), dict)
            else raw
        )
        candidate = SkillCard.model_validate(draft)
        merged = _merge_targets(request.current_skill, candidate, target_paths)
        merged_data = merged.model_dump(mode="json")
        steps, missing_tool_names = _remove_unknown_tool_actions(
            [step for step in merged_data.get("steps", []) if isinstance(step, dict)],
            request.available_tools,
        )
        if steps:
            merged_data["steps"] = steps
            merged = SkillCard.model_validate(merged_data)
        merged, id_warnings = skill_card_with_unique_step_ids(merged)
        assistant_message = str(raw.get("assistant_message") or "已完成选中部分的改写。").strip()
        warnings = [str(item) for item in raw.get("warnings", []) if str(item).strip()]
        warnings.extend(warning for warning in id_warnings if warning not in warnings)
        for tool_name in missing_tool_names:
            warning = (
                f"改写结果引用了未配置工具 {tool_name}，已移出 allowed_actions；"
                "如确需该工具，模型必须在 tool_mentions 中提供来自上下文的完整工具提及。"
            )
            if warning not in warnings:
                warnings.append(warning)
        warnings = _compact_warnings(warnings)
        changed_paths = [str(item) for item in raw.get("changed_paths", []) if str(item).strip()]
        if not changed_paths and merged.model_dump() != request.current_skill.model_dump():
            changed_paths = _changed_paths(request.current_skill, merged)
        tool_resolutions = _normalize_tool_suggestions(
            raw.get("tool_mentions") if isinstance(raw.get("tool_mentions"), list) else raw.get("tool_suggestions"),
            request,
            missing_tool_names,
        )
        warnings = _compact_warnings([*warnings, *_tool_resolution_warnings(tool_resolutions)])
        tool_suggestions = [
            item for item in tool_resolutions if item.resolution_status in {"existing", "new_candidate"}
        ]
        return SkillRewriteResponse(
            draft_skill=merged,
            assistant_message=assistant_message,
            changed_paths=changed_paths,
            warnings=warnings,
            tool_suggestions=tool_suggestions,
        )


def _target_paths(request: SkillRewriteRequest) -> list[str]:
    paths = [path.strip() for path in request.target_paths if path.strip()]
    if not paths:
        paths = [request.target_path.strip() or "all"]
    if "all" in paths:
        return ["all"]
    deduped: list[str] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return deduped or ["all"]


def _skill_from_patches(
    raw: dict[str, Any],
    request: SkillRewriteRequest,
    target_paths: list[str],
) -> SkillCard | None:
    patches = raw.get("patches")
    if not isinstance(patches, list):
        return None
    data = request.current_skill.model_dump(mode="json")
    applied = False
    ignored_paths: list[str] = []
    for item in patches:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        if not _patch_allowed(data, path, target_paths):
            ignored_paths.append(path)
            continue
        if _apply_patch(data, path, item.get("value")):
            applied = True
    if ignored_paths:
        warnings = raw.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
            raw["warnings"] = warnings
        warnings.append(f"已忽略越界改写路径：{', '.join(ignored_paths)}")
    if not applied:
        return None
    return SkillCard.model_validate(data)


def _patch_allowed(data: dict[str, Any], path: str, target_paths: list[str]) -> bool:
    if "all" in target_paths:
        return _patch_path_is_known(data, path)
    if _basic_patch_field(path):
        return "basic" in target_paths
    if path == "steps":
        return any(_is_step_target(target) for target in target_paths)
    step_index = _patch_step_index(data, path)
    if step_index is None:
        return False
    steps = [step for step in data.get("steps", []) if isinstance(step, dict)]
    step_id = str(steps[step_index].get("step_id") or "")
    return f"steps[{step_index}]" in target_paths or f"steps.{step_id}" in target_paths


def _patch_path_is_known(data: dict[str, Any], path: str) -> bool:
    return bool(_basic_patch_field(path)) or path == "steps" or _patch_step_index(data, path) is not None


def _apply_patch(data: dict[str, Any], path: str, value: Any) -> bool:
    basic_field = _basic_patch_field(path)
    if basic_field:
        data[basic_field] = value
        return True
    if path == "steps" and isinstance(value, list):
        data["steps"] = value
        return True
    step_index = _patch_step_index(data, path)
    if step_index is None:
        return False
    step_field = _patch_step_field(path)
    steps = [step for step in data.get("steps", []) if isinstance(step, dict)]
    if not (0 <= step_index < len(steps)):
        return False
    if step_field is None:
        if not isinstance(value, dict):
            return False
        steps[step_index] = value
    else:
        steps[step_index][step_field] = value
    data["steps"] = steps
    return True


def _basic_patch_field(path: str) -> str | None:
    normalized = path.removeprefix("basic.")
    return normalized if normalized in BASIC_FIELDS else None


def _patch_step_index(data: dict[str, Any], path: str) -> int | None:
    steps = [step for step in data.get("steps", []) if isinstance(step, dict)]
    bracket_match = re.fullmatch(r"steps\[(\d+)\](?:\.[A-Za-z_][A-Za-z0-9_]*)?", path)
    if bracket_match:
        index = int(bracket_match.group(1))
        return index if 0 <= index < len(steps) else None
    dot_match = re.fullmatch(r"steps\.([^.]+)(?:\.[A-Za-z_][A-Za-z0-9_]*)?", path)
    if not dot_match:
        return None
    step_id = dot_match.group(1)
    return next((index for index, step in enumerate(steps) if str(step.get("step_id") or "") == step_id), None)


def _patch_step_field(path: str) -> str | None:
    bracket_match = re.fullmatch(r"steps\[\d+\]\.([A-Za-z_][A-Za-z0-9_]*)", path)
    dot_match = re.fullmatch(r"steps\.[^.]+\.([A-Za-z_][A-Za-z0-9_]*)", path)
    field = bracket_match.group(1) if bracket_match else dot_match.group(1) if dot_match else None
    return field if field in STEP_FIELDS else None


def _merge_targets(current: SkillCard, candidate: SkillCard, target_paths: list[str]) -> SkillCard:
    if "all" in target_paths:
        return candidate
    if _has_step_structure_change(current, candidate, target_paths):
        current_data = current.model_dump(mode="json")
        candidate_data = candidate.model_dump(mode="json")
        current_data["steps"] = [
            step for step in candidate_data.get("steps", []) if isinstance(step, dict)
        ]
        if "basic" in target_paths:
            for field in BASIC_FIELDS:
                if field in candidate_data:
                    current_data[field] = candidate_data[field]
        return SkillCard.model_validate(current_data)
    merged = current
    for path in target_paths:
        merged = _merge_target(merged, candidate, path)
    return merged


def _has_step_structure_change(current: SkillCard, candidate: SkillCard, target_paths: list[str]) -> bool:
    if not any(_is_step_target(path) for path in target_paths):
        return False
    current_steps = [step for step in current.model_dump(mode="json").get("steps", []) if isinstance(step, dict)]
    candidate_steps = [step for step in candidate.model_dump(mode="json").get("steps", []) if isinstance(step, dict)]
    if len(candidate_steps) != len(current_steps):
        return True
    current_ids = [str(step.get("step_id") or "") for step in current_steps]
    candidate_ids = [str(step.get("step_id") or "") for step in candidate_steps]
    return sorted(current_ids) == sorted(candidate_ids) and current_ids != candidate_ids


def _is_step_target(path: str) -> bool:
    return path.startswith("steps.") or path.startswith("steps[")


def _merge_target(current: SkillCard, candidate: SkillCard, target_path: str) -> SkillCard:
    normalized_path = target_path.strip() or "all"
    if normalized_path == "all":
        return candidate

    current_data = current.model_dump(mode="json")
    candidate_data = candidate.model_dump(mode="json")
    if normalized_path == "basic":
        for field in BASIC_FIELDS:
            if field in candidate_data:
                current_data[field] = candidate_data[field]
        return SkillCard.model_validate(current_data)

    target_index = _step_target_index(current_data, normalized_path)
    if target_index is not None:
        candidate_steps = [step for step in candidate_data.get("steps", []) if isinstance(step, dict)]
        current_steps = [step for step in current_data.get("steps", []) if isinstance(step, dict)]
        replacement = _replacement_step(
            candidate_steps,
            current_steps[target_index],
            target_index,
            prefer_index=normalized_path.startswith("steps["),
        )
        if isinstance(replacement, dict):
            next_step = dict(current_steps[target_index])
            for field in STEP_FIELDS:
                if field in replacement:
                    next_step[field] = replacement[field]
            current_steps[target_index] = next_step
            current_data["steps"] = current_steps
            return SkillCard.model_validate(current_data)

    return current


def _changed_paths(previous: SkillCard, next_skill: SkillCard) -> list[str]:
    previous_data = previous.model_dump(mode="json")
    next_data = next_skill.model_dump(mode="json")
    changed: list[str] = []
    if any(previous_data.get(field) != next_data.get(field) for field in BASIC_FIELDS):
        changed.append("basic")
    previous_steps = [step for step in previous_data.get("steps", []) if isinstance(step, dict)]
    next_steps = [step for step in next_data.get("steps", []) if isinstance(step, dict)]
    for index in range(max(len(previous_steps), len(next_steps))):
        previous_step = previous_steps[index] if index < len(previous_steps) else None
        next_step = next_steps[index] if index < len(next_steps) else None
        if previous_step != next_step:
            changed.append(f"steps[{index}]")
    return changed


def _step_target_index(current_data: dict[str, Any], path: str) -> int | None:
    current_steps = [step for step in current_data.get("steps", []) if isinstance(step, dict)]
    bracket_match = re.fullmatch(r"steps\[(\d+)\]", path)
    if bracket_match:
        index = int(bracket_match.group(1))
        return index if 0 <= index < len(current_steps) else None
    if path.startswith("steps."):
        step_id = path.split(".", 1)[1]
        return next(
            (index for index, step in enumerate(current_steps) if step.get("step_id") == step_id),
            None,
        )
    return None


def _replacement_step(
    candidate_steps: list[dict[str, Any]],
    current_step: dict[str, Any],
    target_index: int,
    prefer_index: bool = False,
) -> dict[str, Any] | None:
    if prefer_index and target_index < len(candidate_steps):
        return candidate_steps[target_index]
    step_id = str(current_step.get("step_id") or "")
    if step_id:
        matching_steps = [step for step in candidate_steps if str(step.get("step_id") or "") == step_id]
        if len(matching_steps) == 1:
            return matching_steps[0]
    if target_index < len(candidate_steps):
        return candidate_steps[target_index]
    return None


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


def _chunk_text(text: str, size: int = 12) -> Iterator[str]:
    for index in range(0, len(text), size):
        yield text[index : index + size]
