from app.core.skill_runtime import SkillRuntime
from app.db.models import ChatSession
from app.session.session_schema import RouterDecision


def test_suspend_and_explicitly_restore_skill_stack():
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
        slots_json={"asset_id": "EQ-9"},
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="suspend_current_and_start_new_skill",
            target_skill_id="visitor_badge",
            target_step_id="collect_visit_info",
        ),
    )

    assert session.active_skill_id == "visitor_badge"
    assert session.active_step_id == "collect_visit_info"
    assert session.slots_json == {}
    assert session.skill_stack_json[0]["skill_id"] == "repair_ticket"

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="suspend_current_and_start_new_skill",
            target_skill_id="repair_ticket",
            target_step_id="collect_repair_info",
        ),
    )

    assert session.active_skill_id == "repair_ticket"
    assert session.active_step_id == "collect_repair_info"
    assert session.slots_json == {"asset_id": "EQ-9"}
    assert session.skill_stack_json[0]["skill_id"] == "visitor_badge"


def test_exit_current_skill_does_not_auto_resume_suspended_skill():
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
        skill_stack_json=[
            {
                "skill_id": "visitor_badge",
                "step_id": "collect_visit_info",
                "slots": {"visitor_name": "hm"},
            }
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(session, RouterDecision(decision="exit_current_skill"))

    assert session.active_skill_id is None
    assert session.active_step_id is None
    assert session.slots_json == {}
    assert session.skill_stack_json[0]["skill_id"] == "visitor_badge"


def test_start_skill_removes_stale_same_skill_stack_frames():
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
        skill_stack_json=[
            {
                "skill_id": "visitor_badge",
                "step_id": "collect_visit_info",
                "slots": {"visitor_name": "hm"},
            },
            {
                "skill_id": "repair_ticket",
                "step_id": "collect_repair_info",
                "slots": {"asset_id": "EQ-9"},
            },
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="start_skill",
            target_skill_id="repair_ticket",
            target_step_id="collect_repair_info",
        ),
    )

    assert session.active_skill_id == "repair_ticket"
    assert session.active_step_id == "collect_repair_info"
    assert session.slots_json == {}
    assert session.skill_stack_json == [
        {
            "skill_id": "visitor_badge",
            "step_id": "collect_visit_info",
            "slots": {"visitor_name": "hm"},
        }
    ]


def test_related_question_restores_after_answer():
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="answer_related_question_then_resume",
            target_skill_id="repair_ticket",
            target_step_id="answer_warranty_policy",
            should_resume_after_answer=True,
        ),
    )
    assert session.active_step_id == "answer_warranty_policy"
    assert session.resume_after_answer_json == {
        "skill_id": "repair_ticket",
        "step_id": "collect_repair_info",
        "slots": {},
        "summary": None,
        "last_agent_question": None,
    }

    runtime.finish_interrupt_response(session)

    assert session.active_step_id == "collect_repair_info"
    assert session.resume_after_answer_json is None


def test_related_question_to_another_skill_suspends_and_restores_original_context():
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="purchase",
        active_step_id="collect_user_name",
        slots_json={"product_id": "A1"},
        summary="最近回复：请问姓名和数量",
        last_agent_question="请问姓名和数量？",
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="answer_related_question_then_resume",
            target_skill_id="price_compare",
            target_step_id="collect_products",
            should_resume_after_answer=True,
            slot_hints={"user_name": "hm", "product_name_1": "A1", "product_name_2": "A3"},
        ),
    )

    assert session.active_skill_id == "price_compare"
    assert session.active_step_id == "collect_products"
    assert session.slots_json == {"user_name": "hm", "product_name_1": "A1", "product_name_2": "A3"}
    assert session.skill_stack_json == [
        {
            "skill_id": "purchase",
            "step_id": "collect_user_name",
            "slots": {
                "product_id": "A1",
                "user_name": "hm",
                "product_name_1": "A1",
                "product_name_2": "A3",
            },
            "summary": "最近回复：请问姓名和数量",
            "last_agent_question": "请问姓名和数量？",
        }
    ]
    assert session.resume_after_answer_json == session.skill_stack_json[0]

    session.slots_json = {"product_name_1": "A1", "product_name_2": "A3"}
    runtime.finish_interrupt_response(session)

    assert session.active_skill_id == "purchase"
    assert session.active_step_id == "collect_user_name"
    assert session.slots_json == {
        "product_id": "A1",
        "user_name": "hm",
        "product_name_1": "A1",
        "product_name_2": "A3",
    }
    assert session.skill_stack_json == []
    assert session.resume_after_answer_json is None


def test_pending_tasks_are_queued_and_popped_without_using_skill_stack():
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="refund",
        active_step_id="confirm_refund_order",
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="continue_current_skill",
            target_skill_id="refund",
            target_step_id="confirm_refund_order",
            pending_tasks=[
                {
                    "decision": "start_skill",
                    "target_skill_id": "purchase",
                    "target_step_id": "collect_user_name",
                    "user_intent": "退款完成后购买 A3",
                    "source_message": "退了吧，退完我想买一个a3",
                    "slot_hints": {"product_id": "A3"},
                }
            ],
        ),
    )

    assert session.active_skill_id == "refund"
    assert session.skill_stack_json == []
    assert session.pending_tasks_json[0]["target_skill_id"] == "purchase"

    next_decision = runtime.pop_next_pending_task(session)

    assert next_decision is not None
    assert next_decision.decision == "start_skill"
    assert next_decision.target_skill_id == "purchase"
    assert next_decision.target_step_id == "collect_user_name"
    assert next_decision.slot_hints == {"product_id": "A3"}
    assert session.pending_tasks_json == []

    runtime.apply_decision(session, next_decision)

    assert session.active_skill_id == "purchase"
    assert session.slots_json == {"product_id": "A3"}
