from __future__ import annotations

from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import ModelConfig, PersonaConfig, Skill, Tenant, Tool, User, utc_now
from app.security.encryption import encrypt_secret
from app.security.auth import hash_password


ADAPTIVE_FLOW_RULE = (
    "步骤是可自适应推进的目标，不是固定问答脚本；已由当前用户消息、历史信息或路由意图满足的内容"
    "不得重复追问，应直接推进到下一缺失信息、工具调用或最终回复。"
)


REFUND_SKILL = {
    "skill_id": "after_sales_refund",
    "name": "售后退款流程",
    "version": "1.0.0",
    "business_domain": "after_sales",
    "description": "处理用户退款、退货、取消订单等诉求。",
    "trigger_intents": ["退款", "退货", "取消订单", "不想要了"],
    "user_utterance_examples": ["我想退货", "这个不要了", "买错了能退吗", "给我退钱"],
    "goal": ["确认用户退款诉求", "收集订单号", "确认处理对象", "查询订单状态", "说明退款政策", "引导用户继续处理或转人工"],
    "required_info": ["order_id", "refund_reason"],
    "slot_filling_policy": {
        "enabled": True,
        "multi_slot_per_turn": True,
        "extract_scope": "all_skill_expected_user_info",
        "skip_satisfied_steps": True,
        "description": "每轮同时抽取用户已表达的退款类型、订单号、退款原因和确认意愿等信息，已满足的信息不再追问。",
        "target_info": ["refund_type", "order_id", "order_confirmed", "refund_reason"],
    },
    "steps": [
        {
            "step_id": "identify_refund_intent",
            "name": "确认退款诉求",
            "instruction": "将本步骤作为目标而不是固定话术；仅当用户诉求不明确时确认用户是否要退款、退货或取消订单；如果用户已明确说退货/退款/取消订单，写入 refund_type 并直接进入下一缺失信息收集，不要反问类型。",
            "expected_user_info": ["refund_type"],
            "allowed_actions": ["ask_clarification", "continue_flow"],
        },
        {
            "step_id": "collect_order_info",
            "name": "收集订单信息",
            "instruction": "将本步骤作为目标而不是固定话术；如果用户未提供订单号，直接询问订单号；如果用户明确提供订单号，写入 order_id 并进入确认步骤；如果 order_id 是根据 recent_messages、上一笔订单或上下文推断出来的，必须进入确认步骤，不得直接调用工具。不要再询问用户是退货还是退款。",
            "expected_user_info": ["order_id"],
            "allowed_actions": ["ask_user", "continue_flow"],
        },
        {
            "step_id": "confirm_refund_order",
            "name": "确认售后订单",
            "instruction": "在查询或处理退款/退货/取消订单前，必须向用户确认本次要处理的订单号和诉求类型。只有用户明确确认后，才能写入 order_confirmed=true 并继续；如果用户说不是、另一个、换一个，应清空或更新 order_id 并回到订单信息收集。",
            "expected_user_info": ["order_confirmed"],
            "allowed_actions": ["ask_user", "continue_flow"],
        },
        {
            "step_id": "check_refund_eligibility",
            "name": "查询退款资格",
            "instruction": "将本步骤作为目标而不是固定话术；仅当 order_id 已存在且 order_confirmed=true 时调用 order.query；根据订单查询结果说明是否可能支持退款/退货，不要承诺一定成功；如还缺原因则继续收集，已满足时给出明确下一步。",
            "expected_user_info": [],
            "allowed_actions": ["continue_flow", "call_tool:order.query", "handoff_human"],
        },
        {
            "step_id": "collect_refund_reason",
            "name": "收集退款原因",
            "instruction": "将本步骤作为目标而不是固定话术；如果用户已说明退款原因，写入 refund_reason 并继续推进；否则只追问退款原因，不重复追问退款类型或订单号。",
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
    "response_rules": [
        "不要承诺一定能退款。",
        "未查询订单前，不要判断是否符合退款条件。",
        "退款、退货或取消订单前必须先向用户确认订单号和诉求类型。",
        "如果用户要求人工，应转人工。",
        ADAPTIVE_FLOW_RULE,
    ],
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
    "slot_filling_policy": {
        "enabled": True,
        "multi_slot_per_turn": True,
        "extract_scope": "all_skill_expected_user_info",
        "skip_satisfied_steps": True,
        "description": "每轮同时抽取用户已表达的换货类型、订单号、换货原因等信息，已满足的信息不再追问。",
        "target_info": ["exchange_type", "order_id", "exchange_reason"],
    },
    "steps": [
        {
            "step_id": "identify_exchange_intent",
            "name": "确认换货诉求",
            "instruction": "将本步骤作为目标而不是固定话术；如果用户已表达换货商品或换货类型，写入 exchange_type 并继续推进；仅在诉求不明确时追问。",
            "expected_user_info": ["exchange_type"],
            "allowed_actions": ["ask_clarification", "continue_flow"],
        },
        {
            "step_id": "collect_exchange_order_info",
            "name": "收集订单信息",
            "instruction": "将本步骤作为目标而不是固定话术；如果用户已提供订单号，写入 order_id 并调用 order.query；否则询问订单号，并只追问真正缺失的换货信息。",
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
    "response_rules": ["不要承诺一定能换货。", "如政策不确定，应转人工确认。", ADAPTIVE_FLOW_RULE],
}

PURCHASE_SKILL = {
    "skill_id": "skill_purchase_001",
    "name": "购买商品流程",
    "version": "1.0.0",
    "business_domain": "commerce",
    "description": "引导用户完成商品购买流程，包括收集用户信息、确认商品、生成订单并反馈结果。",
    "trigger_intents": ["购买商品", "下单", "买东西", "购买", "place_order"],
    "user_utterance_examples": ["我想买这个商品", "帮我下单", "我要购买 A1", "我要买一个a1"],
    "goal": ["获取用户身份信息", "确认购买的商品及数量", "确认下单意愿", "生成有效订单", "向用户反馈订单号及状态"],
    "required_info": ["user_name", "product_id", "quantity"],
    "slot_filling_policy": {
        "enabled": True,
        "multi_slot_per_turn": True,
        "extract_scope": "all_skill_expected_user_info",
        "skip_satisfied_steps": True,
        "description": "每轮同时抽取用户已表达的姓名、商品 ID、购买数量和下单确认等信息；数量需理解口语数字和量词表达，已满足的信息不再追问。",
        "target_info": ["user_name", "product_id", "quantity", "purchase_confirmed"],
    },
    "steps": [
        {
            "step_id": "collect_user_name",
            "name": "收集用户信息与商品详情",
            "instruction": (
                "将本步骤作为目标而不是固定话术；同时收集用户姓名、商品 ID 和数量。"
                "用户一句话提供多个信息时必须一次性写入 slot_updates；"
                "数值字段需要理解口语数字和量词表达，例如“一个/一件/一台”表示 1，“两个/两件”表示 2，“三份/3个”表示 3。"
                "已提供的信息不再追问，只追问真正缺失的信息；全部满足后进入下单确认，不要直接创建订单。"
            ),
            "expected_user_info": ["user_name", "product_id", "quantity"],
            "allowed_actions": ["ask_user", "continue_flow"],
        },
        {
            "step_id": "confirm_purchase",
            "name": "确认下单信息",
            "instruction": "创建订单前必须向用户确认姓名、商品 ID 和数量。只有用户明确确认后，才能写入 purchase_confirmed=true 并继续；如果用户修改商品、数量或姓名，应更新对应 slot 并重新确认。",
            "expected_user_info": ["purchase_confirmed"],
            "allowed_actions": ["ask_user", "continue_flow"],
        },
        {
            "step_id": "confirm_product",
            "name": "执行购买/创建订单",
            "instruction": (
                "将本步骤作为目标而不是固定话术；仅当 user_name、product_id、quantity 已满足且 purchase_confirmed=true 时，"
                "直接调用 product.purchase 或 order.add 创建订单，不要重复确认商品或数量。"
                "如果工具需要 user_id 且只有 user_name，可将 user_name 作为 user_id。"
            ),
            "expected_user_info": ["product_id", "quantity", "purchase_confirmed"],
            "allowed_actions": ["continue_flow", "call_tool:product.purchase", "call_tool:order.add"],
        },
        {
            "step_id": "create_order",
            "name": "反馈订单结果",
            "instruction": "将工具返回的订单号、商品信息、数量、金额和状态告知用户，确认购买结果；不要只说请稍候。",
            "expected_user_info": [],
            "allowed_actions": ["answer_user"],
        },
    ],
    "interruption_policy": {
        "related_question": "可以临时回答，回答后回到当前购买流程。",
        "unrelated_business": "可以切换到新技能，并保存当前流程进度。",
        "chitchat": "简短回应后，引导用户继续购买流程。",
        "user_wants_human": "直接转人工。",
    },
    "response_rules": [
        "保持语气友好、专业。",
        "明确告知用户订单号。",
        "创建订单前必须先向用户确认姓名、商品 ID 和数量。",
        "若商品不存在或库存不足，需明确告知用户并建议其他操作。",
        ADAPTIVE_FLOW_RULE,
    ],
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
            "found": {"type": "boolean"},
            "order_id": {"type": "string"},
            "purchase_id": {"type": "string"},
            "product_id": {"type": "string"},
            "display_name": {"type": "string"},
            "quantity": {"type": "integer"},
            "unit_price": {"type": "number"},
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
            "found": {"type": "boolean"},
            "order_id": {"type": "string"},
            "user_id": {"type": "string"},
            "product_id": {"type": "string"},
            "display_name": {"type": "string"},
            "quantity": {"type": "integer"},
            "unit_price": {"type": "number"},
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

    for content in (REFUND_SKILL, EXCHANGE_SKILL, PURCHASE_SKILL):
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
        else:
            _sync_demo_skill_if_stale(existing, content)

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


def _sync_demo_skill_if_stale(existing: Skill, desired: dict) -> None:
    content = dict(existing.content_json or {})
    changed = False
    current_steps = [step for step in content.get("steps", []) if isinstance(step, dict)]
    desired_steps = [step for step in desired.get("steps", []) if isinstance(step, dict)]
    current_steps_by_id = {str(step.get("step_id") or ""): step for step in current_steps}
    merged_steps: list[dict] = []
    used_step_ids: set[str] = set()

    for desired_step in desired_steps:
        step_id = str(desired_step.get("step_id") or "")
        current_step = current_steps_by_id.get(step_id)
        if not current_step:
            merged_steps.append(dict(desired_step))
            used_step_ids.add(step_id)
            changed = True
            continue
        desired_instruction = str(desired_step.get("instruction") or "")
        current_instruction = str(current_step.get("instruction") or "")
        if desired_instruction and not current_instruction:
            current_step["instruction"] = desired_instruction
            changed = True
        for key in ("expected_user_info", "allowed_actions"):
            if key in desired_step and current_step.get(key) != desired_step.get(key):
                current_step[key] = desired_step[key]
                changed = True
        merged_steps.append(current_step)
        used_step_ids.add(step_id)

    for current_step in current_steps:
        step_id = str(current_step.get("step_id") or "")
        if step_id and step_id not in used_step_ids:
            merged_steps.append(current_step)
            used_step_ids.add(step_id)

    if desired_steps and content.get("steps") != merged_steps:
        content["steps"] = merged_steps
        changed = True

    if desired.get("required_info") and content.get("required_info") != desired.get("required_info"):
        content["required_info"] = desired["required_info"]
        changed = True

    if desired.get("interruption_policy") and content.get("interruption_policy") != desired.get(
        "interruption_policy"
    ):
        content["interruption_policy"] = desired["interruption_policy"]
        changed = True
    if desired.get("slot_filling_policy"):
        merged_policy = _merge_slot_filling_policy(
            content.get("slot_filling_policy"), desired["slot_filling_policy"]
        )
        if content.get("slot_filling_policy") != merged_policy:
            content["slot_filling_policy"] = merged_policy
            changed = True
    if desired.get("response_rules"):
        merged_rules = _append_missing_rules(content.get("response_rules"), desired["response_rules"])
        if content.get("response_rules") != merged_rules:
            content["response_rules"] = merged_rules
            changed = True

    if changed:
        existing.content_json = content
        existing.updated_at = utc_now()


def _merge_slot_filling_policy(current: object, desired: dict) -> dict:
    current_policy = dict(current) if isinstance(current, dict) else {}
    merged = {**current_policy, **desired}
    target_info = {
        str(item)
        for item in current_policy.get("target_info", [])
        if str(item).strip()
    }
    target_info.update(str(item) for item in desired.get("target_info", []) if str(item).strip())
    merged["target_info"] = sorted(target_info)
    return merged


def _append_missing_rules(current: object, desired: list[str]) -> list[str]:
    rules = [str(item) for item in current] if isinstance(current, list) else []
    for rule in desired:
        if rule not in rules:
            rules.append(rule)
    return rules
