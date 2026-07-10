from __future__ import annotations

from pathlib import Path

from app import paths
from app.db.models import ChatSession, ModelConfig, Skill, Tool
from app.llm import LLMClient, LLMError
from app.session.session_schema import RouterDecision, StepAgentResult


PROMPT_PATH = paths.resource_dir() / "app" / "llm" / "prompts" / "step_agent_prompt.md"
INTERNAL_SCHEDULER_SLOT_KEYS = {"_graph_pending_steps"}


class StepAgent:
    def run(
        self,
        message: str,
        session: ChatSession,
        skill: Skill | None,
        tools: list[Tool],
        model_config: ModelConfig,
        router_decision: RouterDecision | None = None,
        repair_context: dict[str, object] | None = None,
        recent_messages: list[dict[str, str]] | None = None,
        memory_context: list[dict[str, object]] | None = None,
        conversation_context: dict[str, object] | None = None,
    ) -> StepAgentResult:
        payload = {
            "user_message": message,
            "recent_messages": recent_messages or [],
            "conversation_context": conversation_context or {},
            "memory_context": memory_context or [],
            "active_skill": skill.content_json if skill else None,
            "active_step": _active_step(skill, session.active_step_id),
            "knowledge_context": session.knowledge_context_json or [],
            "router_decision": router_decision.model_dump() if router_decision else None,
            "slots": _step_agent_slots(session.slots_json),
            "awaiting_input": session.awaiting_input_json,
            "skill_stack": session.skill_stack_json or [],
            "pending_tasks": session.pending_tasks_json or [],
            "repair_context": repair_context,
            "available_tools": [
                {
                    "name": tool.name,
                    "display_name": tool.display_name,
                    "description": tool.description,
                    "bucket": getattr(tool, "bucket", None) or "未分桶",
                    "input_schema": tool.input_schema,
                    "allowed_skills": tool.allowed_skills_json,
                }
                for tool in tools
                if tool.enabled
            ],
        }
        try:
            raw = LLMClient(model_config).generate_json(PROMPT_PATH.read_text(encoding="utf-8"), payload)
            return StepAgentResult.model_validate(raw)
        except Exception as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError(f"Step agent returned invalid JSON schema: {exc}") from exc


def _active_step(skill: Skill | None, active_step_id: str | None) -> dict[str, object] | None:
    if not skill or not active_step_id:
        return None
    content = skill.content_json or {}
    for node in content.get("nodes", []):
        if isinstance(node, dict) and node.get("node_id") == active_step_id:
            return _node_as_step(node)
    return None


def _step_agent_slots(slots: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(slots, dict):
        return {}
    return {
        key: value
        for key, value in slots.items()
        if str(key) not in INTERNAL_SCHEDULER_SLOT_KEYS
    }


def _node_as_step(node: dict[str, object]) -> dict[str, object]:
    return {
        "step_id": node.get("node_id"),
        "node_id": node.get("node_id"),
        "type": node.get("type"),
        "name": node.get("name"),
        "instruction": node.get("instruction"),
        "optional": node.get("optional"),
        "condition": node.get("condition"),
        "expected_user_info": node.get("expected_user_info") or [],
        "allowed_actions": node.get("allowed_actions") or [],
        "knowledge_scope": node.get("knowledge_scope") or {},
        "retry_policy": node.get("retry_policy") or {},
    }
