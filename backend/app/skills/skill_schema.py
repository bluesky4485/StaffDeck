from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SkillStep(BaseModel):
    step_id: str
    name: str
    instruction: str
    expected_user_info: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)


class SkillCard(BaseModel):
    skill_id: str
    name: str
    version: str = "1.0.0"
    business_domain: Optional[str] = None
    description: str = ""
    trigger_intents: list[str] = Field(default_factory=list)
    user_utterance_examples: list[str] = Field(default_factory=list)
    goal: list[str] = Field(default_factory=list)
    required_info: list[str] = Field(default_factory=list)
    slot_filling_policy: dict[str, Any] = Field(default_factory=dict)
    response_rules: list[str] = Field(default_factory=list)
    steps: list[SkillStep] = Field(default_factory=list)
    interruption_policy: dict[str, str] = Field(default_factory=dict)


class ToolSuggestion(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    sample_arguments: dict[str, Any] = Field(default_factory=dict)
    source_excerpt: Optional[str] = None
    probe_result: Optional[dict[str, Any]] = None
    reason: str = ""
    resolution_status: Literal["existing", "new_candidate", "incomplete"] = "new_candidate"
    matched_tool_id: Optional[str] = None
    matched_tool_name: Optional[str] = None
    matched_tool_display_name: Optional[str] = None
    missing_reason: Optional[str] = None


class SkillCreateRequest(BaseModel):
    tenant_id: str
    content: SkillCard
    status: Literal["draft", "published", "archived"] = "draft"


class SkillUpdateRequest(BaseModel):
    tenant_id: str
    content: SkillCard
    status: Optional[Literal["draft", "published", "archived"]] = None


class SkillRead(BaseModel):
    id: str
    tenant_id: str
    skill_id: str
    version: str
    name: str
    business_domain: Optional[str]
    description: Optional[str]
    content: SkillCard
    status: str
    call_count: int = 0
    positive_feedback_count: int = 0
    negative_feedback_count: int = 0
    positive_rate: float = 0.0
    negative_rate: float = 0.0
    total_call_count: int = 0
    total_positive_feedback_count: int = 0
    total_negative_feedback_count: int = 0
    total_positive_rate: float = 0.0
    total_negative_rate: float = 0.0
    recent_versions: list[str] = Field(default_factory=list)
    recent_call_count: int = 0
    recent_positive_feedback_count: int = 0
    recent_negative_feedback_count: int = 0
    recent_positive_rate: float = 0.0
    recent_negative_rate: float = 0.0
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class SkillVersionRead(BaseModel):
    id: str
    tenant_id: str
    skill_id: str
    version: str
    name: str
    business_domain: Optional[str]
    description: Optional[str]
    content: SkillCard
    status: str
    call_count: int = 0
    positive_feedback_count: int = 0
    negative_feedback_count: int = 0
    positive_rate: float = 0.0
    negative_rate: float = 0.0
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class SkillDistillRequest(BaseModel):
    tenant_id: str
    title: str
    raw_content: str
    business_domain: Optional[str] = None
    available_tools: list[dict[str, Any]] = Field(default_factory=list)


class SkillDistillResponse(BaseModel):
    draft_skill: SkillCard
    warnings: list[str] = Field(default_factory=list)
    tool_suggestions: list[ToolSuggestion] = Field(default_factory=list)


class SkillRewriteRequest(BaseModel):
    tenant_id: str
    current_skill: SkillCard
    instruction: str
    target_path: str = "all"
    target_paths: list[str] = Field(default_factory=list)
    target_label: Optional[str] = None
    conversation: list[dict[str, str]] = Field(default_factory=list)
    available_tools: list[dict[str, Any]] = Field(default_factory=list)


class SkillRewriteResponse(BaseModel):
    draft_skill: SkillCard
    assistant_message: str
    changed_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tool_suggestions: list[ToolSuggestion] = Field(default_factory=list)


class SkillFileExtractRequest(BaseModel):
    filename: str
    content_base64: str


class SkillFileExtractResponse(BaseModel):
    filename: str
    text: str
