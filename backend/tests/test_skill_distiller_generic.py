from app.skills.skill_distiller import SkillDistiller
from app.skills.skill_schema import SkillDistillRequest


def test_fallback_card_is_not_domain_hardcoded_for_commerce_text() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="购买商品",
        raw_content="获取用户姓名，查询商品是否存在，生成对应订单号，反馈给用户",
        available_tools=[
            {"name": "product.purchase", "input_schema": {"required": ["product_id"]}, "requires_confirmation": True},
            {"name": "order.add", "input_schema": {"required": ["product_id"]}, "requires_confirmation": True},
        ],
    )

    card = SkillDistiller()._fallback_card(request)  # noqa: SLF001

    assert card.skill_id != "purchase_product"
    assert card.required_info == []
    assert all("operation_confirmed" not in step.expected_user_info for step in card.steps)
    assert all(
        not any(action.startswith("call_tool:") for action in step.allowed_actions)
        for step in card.steps
    )


def test_slot_policy_targets_model_generated_fields() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="设备报修",
        raw_content="收集设备编号和问题描述，创建维修工单",
    )
    raw = {
        "draft_skill": {
            "skill_id": "repair_ticket",
            "name": "设备报修",
            "required_info": ["asset_id"],
            "steps": [
                {
                    "step_id": "collect_repair_info",
                    "name": "收集报修信息",
                    "instruction": "同时抽取设备编号和问题描述。",
                    "expected_user_info": ["asset_id", "issue_desc"],
                    "allowed_actions": ["ask_user", "continue_flow"],
                }
            ],
        }
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001

    assert response.draft_skill.slot_filling_policy["target_info"] == ["asset_id", "issue_desc"]


def test_normalize_response_does_not_infer_tool_or_confirmation_from_raw_words() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="退款处理",
        raw_content="获取订单号，核实订单是否符合退款条件，处理退款并反馈给用户",
        available_tools=[
            {
                "name": "order.query",
                "input_schema": {"required": ["order_id"]},
            }
        ],
    )
    raw = {
        "draft_skill": {
            "skill_id": "refund",
            "name": "退款处理",
            "required_info": ["order_id"],
            "steps": [
                {
                    "step_id": "collect_order",
                    "name": "收集订单",
                    "instruction": "收集订单号。",
                    "expected_user_info": ["order_id"],
                    "allowed_actions": ["ask_user", "continue_flow"],
                }
            ],
            "response_rules": [],
        }
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001
    steps = response.draft_skill.steps

    assert all(
        not any(action.startswith("call_tool:") for action in step.allowed_actions)
        for step in steps
    )
    assert all("operation_confirmed" not in step.expected_user_info for step in steps)
    assert "answer_user" in steps[-1].allowed_actions
    assert any("不得把" in rule and "请稍候" in rule for rule in response.draft_skill.response_rules)
    assert any("自适应推进" in rule for rule in response.draft_skill.response_rules)
    assert not any("确认关键对象" in rule for rule in response.draft_skill.response_rules)
    assert all("目标而不是固定话术" in step.instruction for step in steps)


def test_normalize_response_preserves_model_declared_tool_and_confirmation() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="退款处理",
        raw_content="获取订单号，核实订单是否符合退款条件，处理退款并反馈给用户",
        available_tools=[
            {
                "name": "order.query",
                "input_schema": {"required": ["order_id"]},
            }
        ],
    )
    raw = {
        "draft_skill": {
            "skill_id": "refund",
            "name": "退款处理",
            "required_info": ["order_id"],
            "steps": [
                {
                    "step_id": "collect_order",
                    "name": "收集订单",
                    "instruction": "收集订单号。",
                    "expected_user_info": ["order_id"],
                    "allowed_actions": ["ask_user", "continue_flow"],
                },
                {
                    "step_id": "confirm_operation",
                    "name": "确认操作",
                    "instruction": "确认关键对象和操作内容。",
                    "expected_user_info": ["operation_confirmed"],
                    "allowed_actions": ["ask_user", "continue_flow"],
                },
                {
                    "step_id": "query_order",
                    "name": "查询订单",
                    "instruction": "调用工具查询订单状态。",
                    "expected_user_info": [],
                    "allowed_actions": ["continue_flow", "call_tool:order.query"],
                }
            ],
            "response_rules": [],
        }
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001
    steps = response.draft_skill.steps

    assert any("call_tool:order.query" in step.allowed_actions for step in steps)
    confirm_index = next(
        index for index, step in enumerate(steps) if "operation_confirmed" in step.expected_user_info
    )
    tool_index = next(
        index
        for index, step in enumerate(steps)
        if any(action.startswith("call_tool:") for action in step.allowed_actions)
    )
    assert confirm_index < tool_index
    assert "operation_confirmed=true" in steps[tool_index].instruction
    assert "answer_user" in steps[-1].allowed_actions
    assert any("不得把" in rule and "请稍候" in rule for rule in response.draft_skill.response_rules)
    assert any("自适应推进" in rule for rule in response.draft_skill.response_rules)
    assert any("确认关键对象" in rule for rule in response.draft_skill.response_rules)
    assert all("目标而不是固定话术" in step.instruction for step in steps)


def test_normalize_response_makes_duplicate_step_ids_unique() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="购买商品",
        raw_content="获取用户姓名，生成订单号，反馈给用户",
    )
    raw = {
        "draft_skill": {
            "skill_id": "purchase",
            "name": "购买商品",
            "required_info": ["user_name"],
            "steps": [
                {
                    "step_id": "reply_result",
                    "name": "创建订单",
                    "instruction": "创建订单。",
                    "expected_user_info": ["user_name"],
                    "allowed_actions": ["continue_flow"],
                },
                {
                    "step_id": "reply_result",
                    "name": "反馈订单",
                    "instruction": "反馈订单结果。",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
        }
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001
    step_ids = [step.step_id for step in response.draft_skill.steps]

    assert len(step_ids) == len(set(step_ids))
    assert "reply_result" in step_ids
    assert "reply_result_2" in step_ids
    assert any("step_id" in warning for warning in response.warnings)


def test_normalize_response_turns_steps_into_adaptive_goals() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="资料审核",
        raw_content="收集姓名和资料编号，审核资料状态，反馈给用户",
    )
    raw = {
        "draft_skill": {
            "skill_id": "document_review",
            "name": "资料审核",
            "required_info": ["user_name", "document_id"],
            "steps": [
                {
                    "step_id": "collect_info",
                    "name": "收集信息",
                    "instruction": "询问用户姓名和资料编号。",
                    "expected_user_info": ["user_name", "document_id"],
                    "allowed_actions": ["ask_user", "continue_flow"],
                },
                {
                    "step_id": "reply_result",
                    "name": "反馈结果",
                    "instruction": "反馈审核结果。",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
            "response_rules": [],
        }
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001

    assert response.draft_skill.slot_filling_policy["multi_slot_per_turn"] is True
    assert response.draft_skill.slot_filling_policy["skip_satisfied_steps"] is True
    assert all("目标而不是固定话术" in step.instruction for step in response.draft_skill.steps)


def test_fallback_card_uses_conservative_adaptive_steps() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="预约服务",
        raw_content="获取用户姓名，确认预约人数，创建预约记录并反馈给用户",
    )

    card = SkillDistiller()._fallback_card(request)  # noqa: SLF001

    assert card.required_info == []
    assert all(
        not any(action.startswith("call_tool:") for action in step.allowed_actions)
        for step in card.steps
    )
    assert any("目标而不是固定话术" in step.instruction for step in card.steps)


def test_normalize_response_removes_unknown_actions_without_default_tool_suggestion() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="商品比价",
        raw_content="用户提供两个商品名称，调用 product.compare 工具查询价格并反馈比价结果",
        available_tools=[],
    )
    raw = {
        "draft_skill": {
            "skill_id": "compare_products",
            "name": "商品比价",
            "required_info": ["product_name_1", "product_name_2"],
            "steps": [
                {
                    "step_id": "compare",
                    "name": "查询比价",
                    "instruction": "调用工具查询两个商品价格。",
                    "expected_user_info": ["product_name_1", "product_name_2"],
                    "allowed_actions": ["continue_flow", "call_tool:product.compare"],
                },
                {
                    "step_id": "reply_result",
                    "name": "反馈结果",
                    "instruction": "反馈比价结果。",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
            "response_rules": [],
        }
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001

    assert all(
        "call_tool:product.compare" not in step.allowed_actions
        for step in response.draft_skill.steps
    )
    assert response.tool_suggestions == []
    assert any("未配置工具 product.compare" in warning for warning in response.warnings)


def test_normalize_response_resolves_tool_mentions_as_new_candidates() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="商品比价",
        raw_content="用户提供两个商品名称，POST /api/mock/product/compare 使用两个商品名返回比价信息。",
        available_tools=[],
    )
    raw = {
        "draft_skill": {
            "skill_id": "compare_products",
            "name": "商品比价",
            "required_info": ["product_name_1", "product_name_2"],
            "steps": [
                {
                    "step_id": "compare",
                    "name": "查询比价",
                    "instruction": "调用工具查询两个商品价格。",
                    "expected_user_info": ["product_name_1", "product_name_2"],
                    "allowed_actions": ["continue_flow", "call_tool:product.compare"],
                },
                {
                    "step_id": "reply_result",
                    "name": "反馈结果",
                    "instruction": "反馈比价结果。",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
            "response_rules": [],
        },
        "tool_mentions": [
            {
                "name": "product.compare",
                "display_name": "商品比价查询",
                "description": "根据两个商品名称查询价格并返回对比信息。",
                "method": "POST",
                "url": "/api/mock/product/compare",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "product_name_1": {"type": "string"},
                        "product_name_2": {"type": "string"},
                    },
                    "required": ["product_name_1", "product_name_2"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"success": {"type": "boolean"}, "data": {"type": "object"}},
                },
                "sample_arguments": {"product_name_1": "A1", "product_name_2": "A3"},
                "source_excerpt": "POST /api/mock/product/compare 使用两个商品名返回比价信息。",
                "reason": "原始流程需要商品比价能力，但当前没有对应工具。",
            }
        ],
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001

    assert all(
        "call_tool:product.compare" not in step.allowed_actions
        for step in response.draft_skill.steps
    )
    assert [item.name for item in response.tool_suggestions] == ["product.compare"]
    assert response.tool_suggestions[0].resolution_status == "new_candidate"
    assert response.tool_suggestions[0].input_schema["required"] == ["product_name_1", "product_name_2"]
    assert response.tool_suggestions[0].sample_arguments == {"product_name_1": "A1", "product_name_2": "A3"}
    assert response.tool_suggestions[0].source_excerpt == "POST /api/mock/product/compare 使用两个商品名返回比价信息。"
    assert any("未配置工具 product.compare" in warning for warning in response.warnings)


def test_normalize_response_resolves_tool_mentions_as_existing_tools() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="商品比价",
        raw_content="用户提供两个商品名称，POST /api/mock/product/compare 使用两个商品名返回比价信息。",
        available_tools=[
            {
                "id": "tool_1",
                "name": "product.compare",
                "display_name": "商品比价查询",
                "description": "根据两个商品名称查询价格并返回对比信息。",
                "method": "POST",
                "url": "/api/mock/product/compare",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "product_name_1": {"type": "string"},
                        "product_name_2": {"type": "string"},
                    },
                    "required": ["product_name_1", "product_name_2"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"success": {"type": "boolean"}, "data": {"type": "object"}},
                },
            }
        ],
    )
    raw = {
        "draft_skill": {
            "skill_id": "compare_products",
            "name": "商品比价",
            "required_info": ["product_name_1", "product_name_2"],
            "steps": [
                {
                    "step_id": "compare",
                    "name": "查询比价",
                    "instruction": "调用工具查询两个商品价格。",
                    "expected_user_info": ["product_name_1", "product_name_2"],
                    "allowed_actions": ["continue_flow", "call_tool:product.compare"],
                },
                {
                    "step_id": "reply_result",
                    "name": "反馈结果",
                    "instruction": "反馈比价结果。",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
            "response_rules": [],
        },
        "tool_mentions": [
            {
                "name": "product.compare",
                "display_name": "商品比价查询",
                "description": "根据两个商品名称查询价格并返回对比信息。",
                "method": "POST",
                "url": "/api/mock/product/compare",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "product_name_1": {"type": "string"},
                        "product_name_2": {"type": "string"},
                    },
                    "required": ["product_name_1", "product_name_2"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"success": {"type": "boolean"}, "data": {"type": "object"}},
                },
                "sample_arguments": {"product_name_1": "A1", "product_name_2": "A3"},
                "source_excerpt": "POST /api/mock/product/compare 使用两个商品名返回比价信息。",
                "reason": "原始流程明确提到商品比价接口。",
            }
        ],
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001

    assert response.tool_suggestions[0].resolution_status == "existing"
    assert response.tool_suggestions[0].matched_tool_name == "product.compare"
    assert response.tool_suggestions[0].matched_tool_id == "tool_1"
    assert response.tool_suggestions[0].url == "/api/mock/product/compare"
    assert "call_tool:product.compare" in response.draft_skill.steps[0].allowed_actions


def test_normalize_response_drops_tool_suggestion_when_url_not_in_source() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="会员权益补发",
        raw_content="核对会员权益差异，必要时补发权益并反馈处理结果。",
        available_tools=[],
    )
    raw = {
        "draft_skill": {
            "skill_id": "member_benefit",
            "name": "会员权益补发",
            "required_info": ["user_id", "order_id"],
            "steps": [
                {
                    "step_id": "issue_benefit",
                    "name": "补发权益",
                    "instruction": "补发会员权益。",
                    "expected_user_info": ["user_id", "order_id"],
                    "allowed_actions": ["call_tool:member.issue_benefit"],
                },
                {
                    "step_id": "reply_result",
                    "name": "反馈结果",
                    "instruction": "反馈处理结果。",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
            "response_rules": [],
        },
        "tool_mentions": [
            {
                "name": "member.issue_benefit",
                "display_name": "补发会员权益",
                "description": "补发会员权益。",
                "method": "POST",
                "url": "/api/member/issue-benefit",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string"},
                        "order_id": {"type": "string"},
                    },
                    "required": ["user_id", "order_id"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"success": {"type": "boolean"}},
                },
                "sample_arguments": {"user_id": "user_demo", "order_id": "A12345"},
                "source_excerpt": "补发会员权益。",
                "reason": "文档描述了补发权益动作。",
            }
        ],
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001

    assert all(
        "call_tool:member.issue_benefit" not in step.allowed_actions
        for step in response.draft_skill.steps
    )
    assert response.tool_suggestions == []
    assert any("未配置工具 member.issue_benefit" in warning for warning in response.warnings)
    assert any("当前不能新增" in warning for warning in response.warnings)


def test_normalize_response_does_not_suggest_tool_from_raw_text_only() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="商品比价",
        raw_content="用户提供两个商品名称，使用 product.compare 工具查询价格并反馈比价结果",
        available_tools=[],
    )
    raw = {
        "draft_skill": {
            "skill_id": "compare_products",
            "name": "商品比价",
            "required_info": ["product_name_1", "product_name_2"],
            "steps": [
                {
                    "step_id": "collect",
                    "name": "收集商品",
                    "instruction": "收集两个商品名称。",
                    "expected_user_info": ["product_name_1", "product_name_2"],
                    "allowed_actions": ["ask_user"],
                },
                {
                    "step_id": "reply_result",
                    "name": "反馈结果",
                    "instruction": "反馈比价结果。",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
            "response_rules": [],
        }
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001

    assert response.tool_suggestions == []


def test_normalize_response_drops_incomplete_model_tool_suggestion() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="商品比价",
        raw_content="用户提供两个商品名称，访问接口查询价格并反馈比价结果",
        available_tools=[],
    )
    raw = {
        "draft_skill": {
            "skill_id": "compare_products",
            "name": "商品比价",
            "required_info": ["product_name_1", "product_name_2"],
            "steps": [
                {
                    "step_id": "reply_result",
                    "name": "反馈结果",
                    "instruction": "反馈比价结果。",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
            "response_rules": [],
        },
        "tool_suggestions": [{"name": "product.compare"}],
    }

    response = SkillDistiller()._normalize_response(raw, request)  # noqa: SLF001

    assert response.tool_suggestions == []


def test_skill_card_serializes_response_rules_before_steps() -> None:
    request = SkillDistillRequest(
        tenant_id="tenant_demo",
        title="资料审核",
        raw_content="收集资料编号，审核状态，反馈给用户",
    )

    card = SkillDistiller()._fallback_card(request)  # noqa: SLF001
    keys = list(card.model_dump().keys())

    assert keys.index("response_rules") < keys.index("steps")
