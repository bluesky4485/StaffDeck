from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.chat import _active_skill_for_assistant_message
from app.api.skills import _skill_stats
from app.db.models import AgentEvent, Message, SkillFeedback
from app.skills.skill_editor import SkillEditor
from app.skills.skill_schema import SkillCard, SkillRewriteRequest


def test_skill_editor_only_merges_selected_step() -> None:
    current = _skill_card()
    candidate = _skill_card()
    candidate.name = "不应修改基础信息"
    candidate.steps[0].instruction = "新的收集说明"
    candidate.steps[1].instruction = "不应修改其他步骤"

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已改写步骤。",
            "draft_skill": candidate.model_dump(),
            "changed_paths": ["steps.collect_info"],
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="只优化第一步",
            target_path="steps.collect_info",
            target_label="步骤 1",
        ),
    )

    assert response.draft_skill.name == current.name
    assert response.draft_skill.steps[0].instruction == "新的收集说明"
    assert response.draft_skill.steps[1].instruction == current.steps[1].instruction


def test_skill_editor_merges_multiple_selected_targets() -> None:
    current = _skill_card()
    candidate = _skill_card()
    candidate.description = "新的描述"
    candidate.steps[0].instruction = "新的收集说明"
    candidate.steps[1].instruction = "不应修改第二步"

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已改写多个区域。",
            "draft_skill": candidate.model_dump(),
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="优化基础信息和第一步",
            target_path="basic",
            target_paths=["basic", "steps.collect_info"],
            target_label="基础信息、步骤 1",
        ),
    )

    assert response.draft_skill.description == "新的描述"
    assert response.draft_skill.steps[0].instruction == "新的收集说明"
    assert response.draft_skill.steps[1].instruction == current.steps[1].instruction


def test_skill_editor_can_target_duplicate_step_ids_by_index() -> None:
    current = _skill_card()
    current.steps[1].step_id = "collect_info"
    candidate = _skill_card()
    candidate.steps[0].instruction = "不应修改第一步"
    candidate.steps[1].step_id = "collect_info"
    candidate.steps[1].instruction = "只修改第二个重复 step"

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已改写指定下标步骤。",
            "draft_skill": candidate.model_dump(),
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="只改第二个重复步骤",
            target_path="steps[1]",
            target_paths=["steps[1]"],
            target_label="步骤 2",
        ),
    )

    assert response.draft_skill.steps[0].instruction == current.steps[0].instruction
    assert response.draft_skill.steps[1].instruction == "只修改第二个重复 step"


def test_skill_stats_counts_skill_entry_and_feedback() -> None:
    with _test_session() as db:
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="skill_started",
                payload_json={"to_skill_id": "purchase"},
            )
        )
        db.add(
            SkillFeedback(
                tenant_id="tenant_demo",
                skill_id="purchase",
                session_id="session_1",
                message_id="msg_1",
                user_id="user_1",
                rating="up",
            )
        )
        db.add(
            SkillFeedback(
                tenant_id="tenant_demo",
                skill_id="purchase",
                session_id="session_2",
                message_id="msg_2",
                user_id="user_2",
                rating="down",
            )
        )
        db.commit()

        stats = _skill_stats(db, "tenant_demo")

    assert stats["purchase"]["call_count"] == 1
    assert stats["purchase"]["positive_feedback_count"] == 1
    assert stats["purchase"]["negative_feedback_count"] == 1
    assert stats["purchase"]["positive_rate"] == 0.5
    assert stats["purchase"]["negative_rate"] == 0.5


def test_message_feedback_attribution_uses_turn_active_skill() -> None:
    with _test_session() as db:
        db.add(Message(id="msg_user", tenant_id="tenant_demo", session_id="session_1", role="user", content="我要退款"))
        assistant = Message(
            id="msg_assistant",
            tenant_id="tenant_demo",
            session_id="session_1",
            role="assistant",
            content="请提供订单号。",
        )
        db.add(assistant)
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="user_message_received",
                payload_json={"message": "我要退款"},
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="skill_started",
                payload_json={"to_skill_id": "refund"},
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="assistant_message_created",
                payload_json={"reply": "请提供订单号。"},
            )
        )
        db.commit()

        skill_id = _active_skill_for_assistant_message(db, "tenant_demo", assistant)

    assert skill_id == "refund"


def _skill_card() -> SkillCard:
    return SkillCard(
        skill_id="purchase",
        name="购买商品",
        version="1.0.0",
        business_domain="commerce",
        description="购买流程",
        trigger_intents=["购买"],
        user_utterance_examples=["我要买 A1"],
        goal=["完成下单"],
        required_info=["product_id"],
        steps=[
            {
                "step_id": "collect_info",
                "name": "收集信息",
                "instruction": "收集商品信息",
                "expected_user_info": ["product_id"],
                "allowed_actions": ["ask_user", "continue_flow"],
            },
            {
                "step_id": "reply_result",
                "name": "反馈结果",
                "instruction": "反馈订单结果",
                "expected_user_info": [],
                "allowed_actions": ["answer_user"],
            },
        ],
        interruption_policy={},
        response_rules=[],
    )


def _test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
