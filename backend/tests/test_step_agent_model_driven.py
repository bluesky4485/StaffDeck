from app.core.step_agent import StepAgent
from app.db.models import ChatSession, Skill, Tool
from app.llm.client import LLMClient
from app.session.session_schema import RouterDecision


def test_step_agent_uses_model_json_for_slots_and_tool(monkeypatch):
    captured = {}

    def fake_init(self, model_config):  # noqa: ANN001
        return None

    def fake_generate_json(self, system_prompt, payload):  # noqa: ANN001
        captured["system_prompt"] = system_prompt
        captured["payload"] = payload
        return {
            "reply": None,
            "slot_updates": {"customer_name": "张三", "asset_id": "EQ-9", "issue": "无法启动"},
            "tool_call": {
                "name": "ticket.create",
                "arguments": {
                    "customer_name": "张三",
                    "asset_id": "EQ-9",
                    "issue": "无法启动",
                },
            },
            "next_step_id": "reply_ticket",
            "is_step_completed": True,
            "handoff": False,
        }

    monkeypatch.setattr(LLMClient, "__init__", fake_init)
    monkeypatch.setattr(LLMClient, "generate_json", fake_generate_json)

    result = StepAgent().run(
        "我是张三，设备 EQ-9 无法启动",
        ChatSession(
            id="session_test",
            tenant_id="tenant_demo",
            active_skill_id="repair_ticket",
            active_step_id="collect_issue",
            last_agent_question="请描述设备问题。",
        ),
        _repair_skill(),
        [_ticket_tool()],
        model_config=None,  # type: ignore[arg-type]
        router_decision=RouterDecision(
            decision="start_skill",
            target_skill_id="repair_ticket",
            user_intent="设备报修",
        ),
        recent_messages=[
            {"role": "user", "content": "我是张三，设备 EQ-9 无法启动"},
        ],
        memory_context=[
            {
                "kind": "profile",
                "content": "用户姓名/称呼：张三",
                "metadata": {"key": "preferred_name"},
            }
        ],
    )

    assert captured["payload"]["active_skill"]["skill_id"] == "repair_ticket"
    assert "技能步骤是业务目标" in captured["system_prompt"]
    assert captured["payload"]["active_step"]["step_id"] == "collect_issue"
    assert captured["payload"]["router_decision"]["user_intent"] == "设备报修"
    assert captured["payload"]["recent_messages"][0]["content"] == "我是张三，设备 EQ-9 无法启动"
    assert captured["payload"]["memory_context"][0]["metadata"]["key"] == "preferred_name"
    assert captured["payload"]["last_agent_question"] == "请描述设备问题。"
    assert "repair_context" in captured["payload"]
    assert result.slot_updates["asset_id"] == "EQ-9"
    assert result.tool_call is not None
    assert result.tool_call.name == "ticket.create"
    assert result.next_step_id == "reply_ticket"


def _repair_skill() -> Skill:
    return Skill(
        tenant_id="tenant_demo",
        skill_id="repair_ticket",
        name="设备报修",
        content_json={
            "skill_id": "repair_ticket",
            "name": "设备报修",
            "required_info": ["customer_name", "asset_id", "issue"],
            "steps": [
                {
                    "step_id": "collect_issue",
                    "name": "收集报修信息",
                    "expected_user_info": ["customer_name", "asset_id", "issue"],
                    "allowed_actions": ["ask_user", "call_tool:ticket.create"],
                },
                {
                    "step_id": "reply_ticket",
                    "name": "反馈工单",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
        },
        status="published",
    )


def _ticket_tool() -> Tool:
    return Tool(
        tenant_id="tenant_demo",
        name="ticket.create",
        display_name="创建工单",
        method="POST",
        url="http://localhost:8000/api/mock/ticket/create",
        input_schema={
            "type": "object",
            "properties": {
                "customer_name": {"type": "string"},
                "asset_id": {"type": "string"},
                "issue": {"type": "string"},
            },
            "required": ["customer_name", "asset_id", "issue"],
        },
        output_schema={},
        enabled=True,
    )
