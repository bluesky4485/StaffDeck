from __future__ import annotations

from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import ModelConfig, PersonaConfig, Skill, Tenant, Tool, User
from app.security.encryption import encrypt_secret
from app.security.auth import hash_password


REFUND_SKILL = {
    "skill_id": "after_sales_refund",
    "name": "售后退款流程",
    "version": "1.0.0",
    "business_domain": "after_sales",
    "description": "处理用户退款、退货、取消订单等诉求。",
    "trigger_intents": ["退款", "退货", "取消订单", "不想要了"],
    "user_utterance_examples": ["我想退货", "这个不要了", "买错了能退吗", "给我退钱"],
    "goal": ["确认用户退款诉求", "收集订单号", "查询订单状态", "说明退款政策", "引导用户继续处理或转人工"],
    "required_info": ["order_id", "refund_reason"],
    "steps": [
        {
            "step_id": "identify_refund_intent",
            "name": "确认退款诉求",
            "instruction": "仅当用户诉求不明确时确认用户是否要退款、退货或取消订单；如果用户已明确说退货/退款/取消订单，不要反问类型，直接进入订单信息收集。",
            "expected_user_info": ["refund_type"],
            "allowed_actions": ["ask_clarification", "continue_flow"],
        },
        {
            "step_id": "collect_order_info",
            "name": "收集订单信息",
            "instruction": "如果用户未提供订单号，直接询问订单号；如果已提供订单号，调用 order.query。不要再询问用户是退货还是退款。",
            "expected_user_info": ["order_id"],
            "allowed_actions": ["ask_user", "call_tool:order.query"],
        },
        {
            "step_id": "check_refund_eligibility",
            "name": "查询退款资格",
            "instruction": "根据订单查询结果说明是否可能支持退款，不要承诺一定退款。",
            "expected_user_info": [],
            "allowed_actions": ["answer_user", "handoff_human"],
        },
        {
            "step_id": "collect_refund_reason",
            "name": "收集退款原因",
            "instruction": "询问用户退款原因。",
            "expected_user_info": ["refund_reason"],
            "allowed_actions": ["ask_user", "continue_flow"],
        },
    ],
    "interruption_policy": {
        "related_question": "可以临时回答，回答后回到当前退款流程。",
        "unrelated_business": "可以切换到新技能，并保存当前流程进度。",
        "chitchat": "简短回应后，引导用户继续退款流程。",
        "user_wants_human": "直接转人工。",
    },
    "response_rules": ["不要承诺一定能退款。", "未查询订单前，不要判断是否符合退款条件。", "如果用户要求人工，应转人工。"],
}

EXCHANGE_SKILL = {
    "skill_id": "after_sales_exchange",
    "name": "售后换货流程",
    "version": "1.0.0",
    "business_domain": "after_sales",
    "description": "处理用户换货、更换商品、尺码颜色不合适等诉求。",
    "trigger_intents": ["换货", "更换商品", "换尺码", "换颜色"],
    "user_utterance_examples": ["我想换货", "能不能换个颜色", "尺码不合适想换一下"],
    "goal": ["确认换货诉求", "收集订单号", "确认换货原因", "引导用户继续处理或转人工"],
    "required_info": ["order_id", "exchange_reason"],
    "steps": [
        {
            "step_id": "identify_exchange_intent",
            "name": "确认换货诉求",
            "instruction": "确认用户需要换货的商品和换货类型。",
            "expected_user_info": ["exchange_type"],
            "allowed_actions": ["ask_clarification", "continue_flow"],
        },
        {
            "step_id": "collect_exchange_order_info",
            "name": "收集订单信息",
            "instruction": "询问订单号，并确认需要换货的商品。",
            "expected_user_info": ["order_id"],
            "allowed_actions": ["ask_user", "call_tool:order.query"],
        },
    ],
    "interruption_policy": {
        "related_question": "可以临时回答，回答后回到当前换货流程。",
        "unrelated_business": "可以切换到新技能，并保存当前流程进度。",
        "chitchat": "简短回应后，引导用户继续换货流程。",
        "user_wants_human": "直接转人工。",
    },
    "response_rules": ["不要承诺一定能换货。", "如政策不确定，应转人工确认。"],
}

ORDER_QUERY_TOOL = {
    "name": "order.query",
    "display_name": "订单查询",
    "description": "根据订单号查询订单状态、签收天数和是否可能支持退款。",
    "method": "POST",
    "url": "http://localhost:8000/api/mock/order/query",
    "headers_json": {},
    "auth_json": {},
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "订单号"}},
        "required": ["order_id"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "found": {"type": "boolean"},
            "status": {"type": "string"},
            "signed_days": {"type": "integer"},
            "refundable": {"type": "boolean"},
            "miss_reason": {"type": "string"},
        },
    },
    "allowed_skills_json": ["after_sales_refund", "after_sales_exchange"],
    "enabled": True,
}

ORDER_ARCHIVE_QUERY_TOOL = {
    "name": "order.archive_query",
    "display_name": "历史订单查询",
    "description": "备用订单查询工具；当 order.query 主订单中心未命中、found=false、miss_reason 或历史订单场景时，用同一 order_id 查询归档订单。",
    "method": "POST",
    "url": "http://localhost:8000/api/mock/order/archive-query",
    "headers_json": {},
    "auth_json": {},
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "订单号"}},
        "required": ["order_id"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "found": {"type": "boolean"},
            "source": {"type": "string"},
            "status": {"type": "string"},
            "signed_days": {"type": "integer"},
            "refundable": {"type": "boolean"},
            "recommendation": {"type": "string"},
        },
    },
    "allowed_skills_json": ["after_sales_refund", "after_sales_exchange"],
    "enabled": True,
}

PRODUCT_PURCHASE_TOOL = {
    "name": "product.purchase",
    "display_name": "购买商品",
    "description": "模拟用户购买商品，返回支付后的订单与购买记录。",
    "method": "POST",
    "url": "http://localhost:8000/api/mock/product/purchase",
    "headers_json": {},
    "auth_json": {},
    "input_schema": {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户 ID"},
            "product_id": {"type": "string", "description": "商品 ID，如 SKU-001"},
            "sku_id": {"type": "string", "description": "可选 SKU ID"},
            "quantity": {"type": "integer", "minimum": 1, "maximum": 99, "description": "购买数量"},
            "payment_method": {"type": "string", "description": "支付方式"},
        },
        "required": ["product_id"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "purchase_id": {"type": "string"},
            "payment_status": {"type": "string"},
            "order_status": {"type": "string"},
            "total_amount": {"type": "number"},
            "currency": {"type": "string"},
        },
    },
    "allowed_skills_json": [],
    "enabled": True,
}

ORDER_ADD_TOOL = {
    "name": "order.add",
    "display_name": "订单添加",
    "description": "模拟新增一笔订单，返回订单号、商品、金额和订单状态。",
    "method": "POST",
    "url": "http://localhost:8000/api/mock/order/add",
    "headers_json": {},
    "auth_json": {},
    "input_schema": {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户 ID"},
            "order_id": {"type": "string", "description": "可选自定义订单号"},
            "product_id": {"type": "string", "description": "商品 ID，如 SKU-001"},
            "sku_id": {"type": "string", "description": "可选 SKU ID"},
            "quantity": {"type": "integer", "minimum": 1, "maximum": 99, "description": "商品数量"},
            "status": {"type": "string", "description": "订单初始状态"},
        },
        "required": ["product_id"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "user_id": {"type": "string"},
            "product_id": {"type": "string"},
            "quantity": {"type": "integer"},
            "status": {"type": "string"},
            "total_amount": {"type": "number"},
            "currency": {"type": "string"},
        },
    },
    "allowed_skills_json": [],
    "enabled": True,
}

DEMO_TOOLS = (
    ORDER_QUERY_TOOL,
    ORDER_ARCHIVE_QUERY_TOOL,
    PRODUCT_PURCHASE_TOOL,
    ORDER_ADD_TOOL,
)
DEFAULT_PERSONA_PROMPT = (
    "你是面壁智能的智能客服，语气专业、清晰、友好。"
    "你需要先理解用户诉求，再基于已配置的技能和工具帮助用户完成业务办理。"
    "不要暴露内部路由、技能 ID、步骤 ID 或工具实现细节。"
)


def seed_demo_data(session: Session) -> None:
    settings = get_settings()
    if not session.get(Tenant, "tenant_demo"):
        session.add(Tenant(id="tenant_demo", name="Demo Enterprise"))

    if not session.get(PersonaConfig, "tenant_demo"):
        session.add(PersonaConfig(tenant_id="tenant_demo", system_prompt=DEFAULT_PERSONA_PROMPT))

    demo_user = session.exec(
        select(User).where(User.tenant_id == "tenant_demo", User.username == "user_demo")
    ).first()
    if not demo_user:
        session.add(
            User(
                id="user_demo",
                tenant_id="tenant_demo",
                username="user_demo",
                display_name="Demo User",
                password_hash=hash_password("demo"),
            )
        )

    for content in (REFUND_SKILL, EXCHANGE_SKILL):
        existing = session.exec(
            select(Skill).where(
                Skill.tenant_id == "tenant_demo", Skill.skill_id == content["skill_id"]
            )
        ).first()
        if not existing:
            session.add(
                Skill(
                    tenant_id="tenant_demo",
                    skill_id=content["skill_id"],
                    version=content["version"],
                    name=content["name"],
                    business_domain=content["business_domain"],
                    description=content["description"],
                    content_json=content,
                    status="published",
                )
            )

    for tool_config in DEMO_TOOLS:
        tool = session.exec(
            select(Tool).where(Tool.tenant_id == "tenant_demo", Tool.name == tool_config["name"])
        ).first()
        if not tool:
            session.add(Tool(tenant_id="tenant_demo", **tool_config))

    default_model = session.exec(
        select(ModelConfig).where(ModelConfig.tenant_id == "tenant_demo", ModelConfig.is_default == True)  # noqa: E712
    ).first()
    if not default_model and settings.demo_model_api_key:
        session.add(
            ModelConfig(
                tenant_id="tenant_demo",
                name="Demo Qwen Compatible",
                provider="openai_compatible",
                base_url=settings.demo_model_base_url,
                api_key_encrypted=encrypt_secret(settings.demo_model_api_key),
                model=settings.demo_model_name,
                temperature=0.2,
                max_output_tokens=2048,
                is_default=True,
                enabled=True,
            )
        )

    session.commit()
