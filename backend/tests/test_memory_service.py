from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.memories import clear_my_memories
from app.db.models import ChatSession, MemoryRecord, ModelConfig, Tenant, User
from app.llm.client import LLMClient
from app.memory.service import MemoryService, memory_rows_for_read
from app.session.session_schema import ChatTurnRequest, StepAgentResult


def test_memory_capture_uses_model_updates_and_deduplicates_profile_name(monkeypatch) -> None:
    captured_payload = {}

    def fake_init(self, model_config):  # noqa: ANN001
        return None

    def fake_generate_json(self, system_prompt, payload):  # noqa: ANN001
        captured_payload.update(payload)
        return {
            "memories": [
                {
                    "operation": "upsert",
                    "kind": "profile",
                    "key": "preferred_name",
                    "content": "用户姓名/称呼：xyq",
                    "importance": 0.95,
                    "reason": "用户更新了称呼。",
                }
            ],
            "updated_summary": "用户当前称呼为 xyq，正在测试客服购买和售后流程。",
        }

    monkeypatch.setattr(LLMClient, "__init__", fake_init)
    monkeypatch.setattr(LLMClient, "generate_json", fake_generate_json)

    with _test_session() as db:
        db.add(
            MemoryRecord(
                tenant_id="tenant_demo",
                user_id="user_demo",
                username="user_demo",
                session_id="old_session",
                kind="profile",
                content="用户姓名/称呼：hm我想买一个东西",
                importance=0.95,
                metadata_json={"source": "profile_extractor"},
            )
        )
        db.commit()

        saved = MemoryService(db).capture_turn(
            ChatTurnRequest(tenant_id="tenant_demo", user_id="user_demo", message="我叫xyq"),
            ChatSession(id="session_test", tenant_id="tenant_demo", user_id="user_demo"),
            "好的，已记住您的称呼。",
            StepAgentResult(),
            None,
            ModelConfig(tenant_id="tenant_demo", name="demo", api_key_encrypted="", model="demo"),
            [{"role": "user", "content": "我叫xyq"}],
        )
        db.commit()

        rows = list(db.exec(select(MemoryRecord).where(MemoryRecord.user_id == "user_demo")).all())

    profile_rows = [row for row in rows if row.kind == "profile"]
    summary_rows = [row for row in rows if row.kind == "summary"]
    assert len(profile_rows) == 1
    assert profile_rows[0].content == "用户姓名/称呼：xyq"
    assert profile_rows[0].metadata_json["key"] == "preferred_name"
    assert summary_rows == []
    assert saved
    assert captured_payload["existing_memories"][0]["content"] == "用户姓名/称呼：hm我想买一个东西"


def test_memory_capture_ignores_summary_updates(monkeypatch) -> None:
    def fake_init(self, model_config):  # noqa: ANN001
        return None

    def fake_generate_json(self, system_prompt, payload):  # noqa: ANN001
        assert "用户长期摘要" in payload["existing_memories"][0]["content"]
        return {
            "memories": [],
            "updated_summary": "用户希望客服回复简洁，并正在验证多轮下单流程。",
        }

    monkeypatch.setattr(LLMClient, "__init__", fake_init)
    monkeypatch.setattr(LLMClient, "generate_json", fake_generate_json)

    with _test_session() as db:
        db.add(
            MemoryRecord(
                tenant_id="tenant_demo",
                user_id="user_demo",
                username="user_demo",
                session_id="old_session",
                kind="summary",
                content="用户长期摘要\n- 用户本轮诉求：我要买东西；最近处理结果：请问数量",
                importance=0.8,
                metadata_json={"turn_count": 3},
            )
        )
        db.commit()

        MemoryService(db).capture_turn(
            ChatTurnRequest(tenant_id="tenant_demo", user_id="user_demo", message="一个"),
            ChatSession(id="session_test", tenant_id="tenant_demo", user_id="user_demo"),
            "已为您创建订单。",
            StepAgentResult(),
            None,
            ModelConfig(tenant_id="tenant_demo", name="demo", api_key_encrypted="", model="demo"),
            [{"role": "user", "content": "一个"}],
        )
        db.commit()

        rows = list(db.exec(select(MemoryRecord).where(MemoryRecord.kind == "summary")).all())

    assert len(rows) == 1
    assert rows[0].content == "用户长期摘要\n- 用户本轮诉求：我要买东西；最近处理结果：请问数量"
    assert rows[0].metadata_json["turn_count"] == 3


def test_memory_recall_excludes_summary_history() -> None:
    with _test_session() as db:
        db.add(
            MemoryRecord(
                tenant_id="tenant_demo",
                user_id="user_demo",
                username="user_demo",
                session_id="old_session",
                kind="summary",
                content="用户正在测试客服购买和售后流程。",
                importance=0.9,
            )
        )
        db.add(
            MemoryRecord(
                tenant_id="tenant_demo",
                user_id="user_demo",
                username="user_demo",
                session_id="old_session",
                kind="preference",
                content="用户偏好客服回复简洁。",
                importance=0.85,
                metadata_json={"key": "communication_style"},
            )
        )
        db.commit()

        rows = MemoryService(db).recall("tenant_demo", "user_demo", "客服回复")

    assert [row.kind for row in rows] == ["preference"]
    assert rows[0].content == "用户偏好客服回复简洁。"


def test_memory_rows_for_read_hides_legacy_duplicate_profile_and_raw_summary() -> None:
    rows = [
        MemoryRecord(
            tenant_id="tenant_demo",
            user_id="user_demo",
            kind="profile",
            content="用户姓名/称呼：hm",
            metadata_json={"source": "profile_extractor"},
        ),
        MemoryRecord(
            tenant_id="tenant_demo",
            user_id="user_demo",
            kind="profile",
            content="用户姓名/称呼：hm我想买一个东西",
            metadata_json={"source": "profile_extractor"},
        ),
        MemoryRecord(
            tenant_id="tenant_demo",
            user_id="user_demo",
            kind="summary",
            content="用户长期摘要\n- 用户本轮诉求：我要买东西；最近处理结果：请问数量",
            metadata_json={"turn_count": 3},
        ),
    ]

    visible = memory_rows_for_read(rows)

    assert [row.content for row in visible] == ["用户姓名/称呼：hm"]


def test_clear_my_memories_scopes_to_current_user_and_agent() -> None:
    with _test_session() as db:
        user = User(
            id="user_demo",
            tenant_id="tenant_demo",
            username="user_demo",
            password_hash="hash",
        )
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(user)
        db.add(ChatSession(id="session_agent_a", tenant_id="tenant_demo", user_id="user_demo", agent_id="agent_a"))
        db.add_all(
            [
                MemoryRecord(
                    tenant_id="tenant_demo",
                    user_id="user_demo",
                    username="user_demo",
                    session_id="session_direct",
                    kind="profile",
                    content="当前用户 agent_a 直接记忆",
                    metadata_json={"agent_id": "agent_a"},
                ),
                MemoryRecord(
                    tenant_id="tenant_demo",
                    user_id="user_demo",
                    username="user_demo",
                    session_id="session_agent_a",
                    kind="preference",
                    content="当前用户 agent_a 会话推断记忆",
                ),
                MemoryRecord(
                    tenant_id="tenant_demo",
                    user_id="user_demo",
                    username="user_demo",
                    session_id="session_other_agent",
                    kind="fact",
                    content="当前用户其他员工记忆",
                    metadata_json={"agent_id": "agent_b"},
                ),
                MemoryRecord(
                    tenant_id="tenant_demo",
                    user_id="other_user",
                    username="other_user",
                    session_id="session_agent_a",
                    kind="profile",
                    content="其他用户同员工记忆",
                    metadata_json={"agent_id": "agent_a"},
                ),
                MemoryRecord(
                    tenant_id="tenant_demo",
                    user_id="user_demo",
                    username="user_demo",
                    session_id="session_agent_a",
                    kind="conversation",
                    content="原始对话记录不清理",
                    metadata_json={"agent_id": "agent_a"},
                ),
            ]
        )
        db.commit()

        result = clear_my_memories("tenant_demo", "agent_a", user, db)
        remaining = list(db.exec(select(MemoryRecord).order_by(MemoryRecord.content)).all())

    assert result == {"deleted": 2}
    assert [row.content for row in remaining] == [
        "其他用户同员工记忆",
        "原始对话记录不清理",
        "当前用户其他员工记忆",
    ]


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
