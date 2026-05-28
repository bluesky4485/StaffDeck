from app.core.agent_loop import AgentLoop
from app.core.reflection_agent import ReflectionDecision
from app.db.models import ChatSession, ModelConfig, Skill, Tool
from app.session.session_schema import ChatTurnRequest, RouterDecision, StepAgentResult, ToolCall
from app.tools.tool_schema import ToolResult


def test_reflection_switches_wrong_active_skill_without_suspending() -> None:
    loop = object.__new__(AgentLoop)
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="visitor_badge",
        active_step_id="collect_visitor",
    )

    decision = loop._router_decision_from_reflection(
        ReflectionDecision(
            needs_retry=True,
            reason="用户要报修，不是办理访客证。",
            target_skill_id="repair_ticket",
        ),
        session,
        [_skill("visitor_badge"), _skill("repair_ticket")],
        previous_decision=RouterDecision(decision="continue_current_skill"),
    )

    assert decision is not None
    assert decision.decision == "start_skill"
    assert decision.target_skill_id == "repair_ticket"


def test_reflection_builds_tool_call_from_slots() -> None:
    loop = object.__new__(AgentLoop)
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        slots_json={"customer_name": "张三", "asset_id": "EQ-9", "issue": "无法启动"},
    )

    tool_call = loop._tool_call_from_reflection(
        ReflectionDecision(needs_retry=True, target_tool_name="ticket.create"),
        session,
        [_ticket_tool()],
    )

    assert tool_call is not None
    assert tool_call.name == "ticket.create"
    assert tool_call.arguments["customer_name"] == "张三"
    assert tool_call.arguments["asset_id"] == "EQ-9"
    assert tool_call.arguments["issue"] == "无法启动"


def test_reflection_builds_archive_order_tool_call_from_order_slot() -> None:
    loop = object.__new__(AgentLoop)
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="after_sales_refund",
        slots_json={"order_id": "ARCHIVE-1001"},
    )

    tool_call = loop._tool_call_from_reflection(
        ReflectionDecision(needs_retry=True, target_tool_name="order.archive_query"),
        session,
        [_archive_order_tool()],
    )

    assert tool_call is not None
    assert tool_call.name == "order.archive_query"
    assert tool_call.arguments == {"order_id": "ARCHIVE-1001"}


def test_reflection_tool_retry_is_preferred_for_current_skill_target() -> None:
    loop = object.__new__(AgentLoop)
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="after_sales_refund",
    )

    assert loop._reflection_tool_retry_targets_current_skill(
        ReflectionDecision(
            needs_retry=True,
            target_skill_id="after_sales_refund",
            target_tool_name="order.archive_query",
        ),
        session,
    )


def test_reflection_tool_retry_preserves_router_decision_and_streams_tool_events() -> None:
    loop = object.__new__(AgentLoop)
    loop.db = _FakeDb()
    loop.events = _FakeEvents()
    loop.tool_executor = _FakeToolExecutor()
    loop._tool_activity_payload = lambda tenant_id, name, result: {  # type: ignore[method-assign]
        "toolId": name,
        "toolName": name,
        "rawToolName": name,
        "success": result.success,
        "isError": not result.success,
        "content": result.model_dump(mode="json"),
    }
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="after_sales_refund",
        active_step_id="check_refund_eligibility",
    )
    decision = RouterDecision(
        decision="continue_current_skill",
        target_skill_id="after_sales_refund",
        target_step_id="check_refund_eligibility",
        user_intent="申请退款",
    )
    stream_events: list[tuple[str, dict[str, object]]] = []

    active_skill, returned_decision, step_result, tool_result = loop._retry_with_reflection_tool_call(
        ChatTurnRequest(tenant_id="tenant_demo", message="我要退款"),
        session,
        None,
        decision,
        ToolCall(name="order.archive_query", arguments={"order_id": "ARCHIVE-1001"}),
        "主工具未命中，尝试历史订单查询",
        stream_events,
    )

    assert active_skill is None
    assert returned_decision is decision
    assert step_result.tool_call is not None
    assert step_result.tool_call.name == "order.archive_query"
    assert tool_result is not None
    assert tool_result.success
    assert stream_events[0][0] == "status"
    assert stream_events[0][1]["phase"] == "tool"
    assert stream_events[1][0] == "tool_result"


def test_zero_reflection_rounds_skips_reflection_agent() -> None:
    loop = object.__new__(AgentLoop)
    loop.reflection_agent = _RaisingReflectionAgent()
    session = ChatSession(id="session_test", tenant_id="tenant_demo")
    decision = RouterDecision(decision="continue_current_skill", user_intent="申请退款")
    step_result = StepAgentResult(is_step_completed=True)
    tool_result = ToolResult(tool_name="order.query", success=True, data={"found": False})

    returned = loop._run_reflection_rounds(
        ChatTurnRequest(tenant_id="tenant_demo", message="我要退款"),
        session,
        [],
        [],
        ModelConfig(tenant_id="tenant_demo", name="demo", api_key_encrypted="", model="demo"),
        None,
        decision,
        step_result,
        tool_result,
        0,
    )

    assert returned == (None, decision, step_result, tool_result)


class _FakeDb:
    def commit(self) -> None:
        pass

    def refresh(self, _row: object) -> None:
        pass


class _FakeEvents:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str, dict]] = []

    def record(self, tenant_id: str, session_id: str, event_type: str, payload: dict) -> None:
        self.records.append((tenant_id, session_id, event_type, payload))


class _FakeToolExecutor:
    def execute(self, tenant_id: str, tool_call: ToolCall, active_skill_id: str | None) -> ToolResult:
        return ToolResult(
            tool_name=tool_call.name,
            success=True,
            data={"source": "archive_order_center", "found": True},
        )


class _RaisingReflectionAgent:
    def review(self, *args: object, **kwargs: object) -> ReflectionDecision:
        raise AssertionError("reflection agent should not be called")


def _skill(skill_id: str) -> Skill:
    return Skill(
        tenant_id="tenant_demo",
        skill_id=skill_id,
        name=skill_id,
        content_json={
            "skill_id": skill_id,
            "name": skill_id,
            "steps": [{"step_id": "start", "name": "开始", "allowed_actions": ["ask_user"]}],
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
        allowed_skills_json=["repair_ticket"],
        enabled=True,
    )


def _archive_order_tool() -> Tool:
    return Tool(
        tenant_id="tenant_demo",
        name="order.archive_query",
        display_name="历史订单查询",
        method="POST",
        url="http://localhost:8000/api/mock/order/archive-query",
        input_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        allowed_skills_json=["after_sales_refund", "after_sales_exchange"],
        enabled=True,
    )
