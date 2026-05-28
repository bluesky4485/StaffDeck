from app.core.agent_loop import AgentLoop
from app.core.skill_runtime import SkillRuntime
from app.db.models import ChatSession, Skill, Tool
from app.session.session_schema import StepAgentResult
from app.tools.tool_schema import ToolCall, ToolResult


class FakeEvents:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str, dict]] = []

    def record(self, tenant_id: str, session_id: str, event_type: str, payload: dict) -> None:
        self.records.append((tenant_id, session_id, event_type, payload))


def test_terminal_skill_completion_when_required_slots_are_complete() -> None:
    loop = object.__new__(AgentLoop)
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="reply_ticket_result",
        slots_json={"reporter_name": "hm", "asset_id": "EQ-9", "issue_desc": "无法开机"},
    )

    assert loop._should_complete_skill(
        _repair_skill(),
        session,
        StepAgentResult(is_step_completed=True),
        None,
    )


def test_stale_terminal_skill_is_cleared_before_next_route() -> None:
    loop = object.__new__(AgentLoop)
    loop.runtime = SkillRuntime()
    loop.events = FakeEvents()
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="reply_ticket_result",
        slots_json={"reporter_name": "hm", "asset_id": "EQ-9", "issue_desc": "无法开机"},
    )

    loop._finish_stale_completed_skill("tenant_demo", session, [_repair_skill()])

    assert session.active_skill_id is None
    assert session.active_step_id is None
    assert session.slots_json == {}
    assert loop.events.records[0][2] == "skill_completed"
    assert loop.events.records[0][3]["reason"] == "stale_terminal_state"


def test_stale_terminal_skill_is_removed_from_suspended_stack() -> None:
    loop = object.__new__(AgentLoop)
    loop.runtime = SkillRuntime()
    loop.events = FakeEvents()
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="visitor_badge",
        active_step_id="collect_visit_info",
        skill_stack_json=[
            {
                "skill_id": "repair_ticket",
                "step_id": "reply_ticket_result",
                "slots": {"reporter_name": "hm", "asset_id": "EQ-9", "issue_desc": "无法开机"},
            }
        ],
    )

    loop._finish_stale_completed_skill("tenant_demo", session, [_repair_skill()])

    assert session.active_skill_id == "visitor_badge"
    assert session.skill_stack_json == []
    assert loop.events.records[0][2] == "skill_completed"
    assert loop.events.records[0][3]["reason"] == "stale_suspended_terminal_state"


def test_intermediate_step_with_next_step_is_not_completed() -> None:
    loop = object.__new__(AgentLoop)
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
        slots_json={"reporter_name": "hm"},
    )

    assert not loop._should_complete_skill(
        _repair_skill(),
        session,
        StepAgentResult(is_step_completed=True, next_step_id="reply_ticket_result"),
        None,
    )


def test_model_can_complete_non_terminal_skill_when_no_next_action() -> None:
    loop = object.__new__(AgentLoop)
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
        slots_json={"reporter_name": "hm"},
    )

    assert loop._should_complete_skill(
        _repair_skill(),
        session,
        StepAgentResult(reply="好的，已取消本次报修流程。", is_step_completed=True),
        None,
    )


def test_successful_tool_call_advances_to_final_reply_step() -> None:
    loop = object.__new__(AgentLoop)
    loop.events = FakeEvents()
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="refund",
        active_step_id="check_refund",
    )
    step_result = StepAgentResult(tool_call=_refund_tool_call(), is_step_completed=True)

    advanced = loop._advance_after_successful_tool(
        "tenant_demo",
        session,
        _refund_skill(),
        step_result,
        ToolResult(tool_name="order.query", success=True, data={"eligible": True}),
    )

    assert advanced
    assert session.active_step_id == "reply_result"
    assert step_result.next_step_id == "reply_result"
    assert loop.events.records[0][2] == "skill_step_changed"


def test_answer_step_can_complete_even_if_distilled_order_has_later_satisfied_collect_step() -> None:
    loop = object.__new__(AgentLoop)
    loop.events = FakeEvents()
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="refund",
        active_step_id="check_refund",
        slots_json={"order_id": "A12345", "refund_reason": "商品质量"},
    )
    step_result = StepAgentResult(tool_call=_refund_tool_call(), is_step_completed=True)

    advanced = loop._advance_after_successful_tool(
        "tenant_demo",
        session,
        _refund_skill_with_late_collect_step(),
        step_result,
        ToolResult(tool_name="order.query", success=True, data={"eligible": True}),
    )

    assert not advanced
    assert session.active_step_id == "check_refund"
    assert loop._should_complete_skill(
        _refund_skill_with_late_collect_step(),
        session,
        step_result,
        ToolResult(tool_name="order.query", success=True, data={"eligible": True}),
    )


def _repair_skill() -> Skill:
    return Skill(
        tenant_id="tenant_demo",
        skill_id="repair_ticket",
        name="设备报修",
        content_json={
            "skill_id": "repair_ticket",
            "name": "设备报修",
            "required_info": ["reporter_name", "asset_id", "issue_desc"],
            "steps": [
                {
                    "step_id": "collect_repair_info",
                    "name": "收集报修信息",
                    "expected_user_info": ["reporter_name", "asset_id", "issue_desc"],
                    "allowed_actions": ["ask_user"],
                },
                {
                    "step_id": "reply_ticket_result",
                    "name": "反馈工单结果",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user", "handoff_human"],
                },
            ],
        },
        status="published",
    )


def _refund_skill() -> Skill:
    return Skill(
        tenant_id="tenant_demo",
        skill_id="refund",
        name="售后退款流程",
        content_json={
            "skill_id": "refund",
            "name": "售后退款流程",
            "required_info": ["order_id", "refund_reason"],
            "steps": [
                {
                    "step_id": "check_refund",
                    "name": "核实退款条件",
                    "expected_user_info": ["order_id", "refund_reason"],
                    "allowed_actions": ["continue_flow", "call_tool:order.query"],
                },
                {
                    "step_id": "reply_result",
                    "name": "反馈结果",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user", "handoff_human"],
                },
            ],
        },
        status="published",
    )


def _refund_tool() -> Tool:
    return Tool(
        tenant_id="tenant_demo",
        name="order.query",
        display_name="订单查询",
        method="POST",
        url="http://localhost:8000/api/mock/order/query",
        input_schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "refund_reason": {"type": "string"},
            },
            "required": ["order_id", "refund_reason"],
        },
        allowed_skills_json=["refund"],
        enabled=True,
    )


def _refund_skill_with_late_collect_step() -> Skill:
    return Skill(
        tenant_id="tenant_demo",
        skill_id="refund",
        name="售后退款流程",
        content_json={
            "skill_id": "refund",
            "name": "售后退款流程",
            "required_info": ["order_id", "refund_reason"],
            "steps": [
                {
                    "step_id": "collect_order",
                    "name": "收集订单",
                    "expected_user_info": ["order_id"],
                    "allowed_actions": ["ask_user", "call_tool:order.query"],
                },
                {
                    "step_id": "check_refund",
                    "name": "查询退款资格",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user", "handoff_human"],
                },
                {
                    "step_id": "collect_refund_reason",
                    "name": "收集退款原因",
                    "expected_user_info": ["refund_reason"],
                    "allowed_actions": ["ask_user", "continue_flow"],
                },
            ],
        },
        status="published",
    )


def _refund_tool_call():
    return ToolCall(
        name="order.query",
        arguments={"order_id": "A12345", "refund_reason": "商品质量"},
    )
