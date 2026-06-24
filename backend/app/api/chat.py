from __future__ import annotations

import json
import threading
from datetime import timedelta
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.agents.branching import model_for_agent
from app.core import AgentLoop
from app.db import engine, get_session
from app.db.models import (
    AgentEvent,
    AgentProfile,
    ChatSession,
    Message,
    MessageFeedback,
    Skill,
    SkillFeedback,
    User,
    new_id,
    utc_now,
)
from app.feedback import enqueue_feedback_analysis
from app.llm import LLMClient, LLMError
from app.security.auth import get_current_user
from app.security.tenant import ensure_tenant
from app.scheduled_tasks.schema import ScheduledTaskDraftRead
from app.scheduled_tasks.service import detect_scheduled_task_draft
from app.session.helpers import public_session
from app.session.session_schema import (
    ChatSessionCreateRequest,
    ChatSessionRead,
    ChatSessionUpdateRequest,
    ChatTurnRequest,
    ChatTurnResponse,
    MessageFeedbackRequest,
    MessageRead,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
STREAM_REPLY_CHUNK_SIZE = 96
SESSION_TITLE_SUMMARY_EVENT = "session_title_summarized"
SESSION_TITLE_PROMPT = """你是任务派发台的会话标题编辑器。

根据首轮用户需求和员工回复，生成一个简短、可读、具体的中文标题。

要求：
- 输出 JSON object，格式为 {"title": "..."}。
- 标题 4 到 18 个中文字符优先，最多 24 个字符。
- 不要使用“新任务”“任务记录”“用户咨询”等空泛标题。
- 不要包含标点符号、引号、编号、员工名或用户称呼。
- 如果无法判断，就返回最能概括用户需求的短语。
"""


def session_read(row: ChatSession) -> ChatSessionRead:
    return ChatSessionRead(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        title=row.title,
        active_skill_id=row.active_skill_id,
        active_step_id=row.active_step_id,
        status=row.status,
        summary=row.summary,
        last_agent_question=row.last_agent_question,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def message_read(row: Message, feedback_rating: str | None = None) -> MessageRead:
    return MessageRead(
        id=row.id,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        role=row.role,
        content=row.content,
        metadata=row.metadata_json or {},
        created_at=row.created_at.isoformat(),
        feedback_rating=feedback_rating,
    )


def _user_message_metadata(request: ChatTurnRequest) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if request.interaction_mode == "scheduled_task":
        metadata["interaction_mode"] = "scheduled_task"
    if request.model_config_id:
        metadata["model_config_id"] = request.model_config_id
    return metadata


def _schedule_session_title_summary(
    tenant_id: str,
    user_id: str,
    session_id: str,
    agent_id: str | None,
) -> None:
    if not session_id:
        return
    thread = threading.Thread(
        target=_summarize_session_title_once,
        args=(tenant_id, user_id, session_id, agent_id),
        daemon=True,
    )
    thread.start()


def _summarize_session_title_once(
    tenant_id: str,
    user_id: str,
    session_id: str,
    agent_id: str | None,
) -> None:
    try:
        with Session(engine) as db:
            session = db.exec(
                select(ChatSession).where(
                    ChatSession.id == session_id,
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.user_id == user_id,
                )
            ).first()
            if not session:
                return
            existing = db.exec(
                select(AgentEvent).where(
                    AgentEvent.tenant_id == tenant_id,
                    AgentEvent.session_id == session_id,
                    AgentEvent.event_type == SESSION_TITLE_SUMMARY_EVENT,
                )
            ).first()
            if existing:
                return
            messages = db.exec(
                select(Message)
                .where(Message.tenant_id == tenant_id, Message.session_id == session_id)
                .order_by(Message.created_at)
                .limit(6)
            ).all()
            if not any(row.role == "user" for row in messages):
                return
            payload = {
                "current_title": session.title or "",
                "messages": [
                    {"role": row.role, "content": row.content[:1200]}
                    for row in messages
                    if row.role in {"user", "assistant"}
                ],
            }
            title = ""
            title_source = "first_user_fallback"
            model_config = model_for_agent(db, tenant_id, agent_id or session.agent_id)
            if model_config:
                try:
                    raw = LLMClient(model_config).generate_json(SESSION_TITLE_PROMPT, payload)
                    title = _normalize_auto_title(str(raw.get("title") or ""))
                    if title:
                        title_source = "first_turn_summary"
                except LLMError:
                    title = ""
            if not title:
                title = _fallback_session_title(messages)
            if not title:
                return
            session.title = title
            db.add(session)
            db.add(
                AgentEvent(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    event_type=SESSION_TITLE_SUMMARY_EVENT,
                    payload_json={"title": title, "source": title_source},
                )
            )
            db.commit()
    except (LLMError, Exception):
        return


def _normalize_auto_title(value: str) -> str:
    title = value.strip().strip("\"'“”‘’`")
    for token in ("\n", "\r", "\t", "：", ":", "。", "，", ",", "；", ";"):
        title = title.replace(token, " ")
    title = " ".join(part for part in title.split() if part)
    return title[:24]


def _fallback_session_title(messages: list[Message]) -> str:
    first_user = next((row.content for row in messages if row.role == "user" and row.content.strip()), "")
    if not first_user:
        return ""
    return _normalize_auto_title(first_user)


def _maybe_handle_scheduled_task_request(
    db: Session,
    request: ChatTurnRequest,
    chat_session: ChatSession,
) -> tuple[ChatTurnResponse, ScheduledTaskDraftRead] | None:
    if request.interaction_mode != "scheduled_task" or not request.agent_id:
        return None
    draft = detect_scheduled_task_draft(
        db,
        request.tenant_id,
        request.agent_id,
        request.user_id,
        request.message,
        chat_session.id,
    )
    if not draft or not draft.should_create:
        return None

    reply = _scheduled_task_draft_reply(draft)
    now = utc_now()
    event_time = now + timedelta(microseconds=1)
    assistant_time = now + timedelta(microseconds=2)
    state_time = now + timedelta(microseconds=3)
    chat_session.updated_at = assistant_time
    chat_session.summary = f"最近回复：{reply[:120]}"
    db.add(
        Message(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            role="user",
            content=request.message,
            metadata_json=_user_message_metadata(request),
            created_at=now,
        )
    )
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="user_message_received",
            payload_json={"message": request.message, "channel": request.channel, "user_id": request.user_id},
            created_at=now,
        )
    )
    db.add(
        Message(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            role="assistant",
            content=reply,
            metadata_json={"scheduled_task_draft": draft.model_dump(mode="json")},
            created_at=assistant_time,
        )
    )
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="scheduled_task_draft_created",
            payload_json=draft.model_dump(mode="json"),
            created_at=event_time,
        )
    )
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="assistant_message_created",
            payload_json={"reply": reply, "scheduled_task_draft": draft.model_dump(mode="json")},
            created_at=assistant_time,
        )
    )
    state = public_session(chat_session)
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="session_state_changed",
            payload_json=state.model_dump(),
            created_at=state_time,
        )
    )
    db.commit()
    db.refresh(chat_session)
    response = ChatTurnResponse(
        reply=reply,
        session_id=chat_session.id,
        session_state=public_session(chat_session),
    )
    return response, draft


def _scheduled_task_draft_reply(draft: ScheduledTaskDraftRead) -> str:
    lines = [
        "我已按你选择的定时项目整理成自动任务草案。",
        f"任务：{draft.title}",
        f"计划：{_format_draft_schedule(draft)}",
        f"执行内容：{draft.prompt}",
        "确认下方卡片后才会启用；确认前不会创建自动任务。",
    ]
    return "\n".join(lines)


def _format_draft_schedule(draft: ScheduledTaskDraftRead) -> str:
    schedule = draft.schedule or {}
    if draft.schedule_type == "once":
        return f"一次性 {schedule.get('run_at') or '待确认时间'}"
    if draft.schedule_type == "weekly":
        weekdays = schedule.get("weekdays")
        labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        if isinstance(weekdays, list):
            days = "、".join(labels[int(day)] for day in weekdays if str(day).isdigit() and 0 <= int(day) <= 6)
        else:
            days = "周一"
        return f"每周 {days or '周一'} {schedule.get('time') or '09:00'}"
    if draft.schedule_type == "monthly":
        return f"每月 {schedule.get('day_of_month') or 1} 号 {schedule.get('time') or '09:00'}"
    return f"每天 {schedule.get('time') or '09:00'}"


def _persist_scheduled_task_draft(
    db: Session,
    tenant_id: str,
    session_id: str,
    draft: ScheduledTaskDraftRead,
) -> None:
    if not session_id:
        return
    payload = draft.model_dump(mode="json")
    latest_assistant = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == session_id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
    ).first()
    if latest_assistant:
        metadata = dict(latest_assistant.metadata_json or {})
        metadata["scheduled_task_draft"] = payload
        latest_assistant.metadata_json = metadata
        db.add(latest_assistant)
    db.add(
        AgentEvent(
            tenant_id=tenant_id,
            session_id=session_id,
            event_type="scheduled_task_draft_created",
            payload_json=payload,
            created_at=utc_now(),
        )
    )
    db.commit()


def _reply_chunks(reply: str) -> Iterator[str]:
    for index in range(0, len(reply), STREAM_REPLY_CHUNK_SIZE):
        yield reply[index : index + STREAM_REPLY_CHUNK_SIZE]


@router.post("/turn", response_model=ChatTurnResponse)
def chat_turn(
    request: ChatTurnRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ChatTurnResponse:
    _ensure_request_tenant(request.tenant_id, current_user)
    request = request.model_copy(update={"user_id": current_user.id})
    if request.session_id:
        chat_session = _ensure_chat_session_available(db, request.tenant_id, current_user.id, request.session_id)
        request = _bind_request_to_session_agent(db, request, chat_session, current_user)
    else:
        _ensure_chat_agent_available(db, request.tenant_id, request.agent_id, current_user)
    ensure_tenant(db, request.tenant_id)
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if request.session_id:
        scheduled_response = _maybe_handle_scheduled_task_request(db, request, chat_session)
        if scheduled_response:
            response, _draft = scheduled_response
            _schedule_session_title_summary(request.tenant_id, request.user_id, response.session_id, request.agent_id)
            return response
    response = AgentLoop(db).handle_turn(request)
    _schedule_session_title_summary(request.tenant_id, request.user_id, response.session_id, request.agent_id)
    if request.interaction_mode == "scheduled_task" and request.agent_id:
        draft = detect_scheduled_task_draft(
            db,
            request.tenant_id,
            request.agent_id,
            request.user_id,
            request.message,
            response.session_id,
        )
        if draft and draft.should_create:
            _persist_scheduled_task_draft(db, request.tenant_id, response.session_id, draft)
    return response


@router.post("/stream")
def chat_stream(
    request: ChatTurnRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    _ensure_request_tenant(request.tenant_id, current_user)
    request = request.model_copy(update={"user_id": current_user.id})
    ensure_tenant(db, request.tenant_id)
    if request.session_id:
        chat_session = _ensure_chat_session_available(db, request.tenant_id, current_user.id, request.session_id)
        request = _bind_request_to_session_agent(db, request, chat_session, current_user)
    else:
        _ensure_chat_agent_available(db, request.tenant_id, request.agent_id, current_user)
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    def stream_events() -> Iterator[str]:
        with Session(engine) as db:
            ensure_tenant(db, request.tenant_id)
            if request.session_id:
                chat_session = _ensure_chat_session_available(db, request.tenant_id, request.user_id, request.session_id)
                scheduled_response = _maybe_handle_scheduled_task_request(db, request, chat_session)
                if scheduled_response:
                    response, draft = scheduled_response
                    yield _sse("status", {"phase": "scheduled_task_draft", "text": "生成自动任务草案"})
                    for chunk in _reply_chunks(response.reply):
                        yield _sse("stream_delta", {"content": chunk})
                    yield _sse("stream_end", {})
                    yield _sse("complete", response.model_dump(mode="json"))
                    yield _sse("scheduled_task_draft", draft.model_dump(mode="json"))
                    _schedule_session_title_summary(request.tenant_id, request.user_id, response.session_id, request.agent_id)
                    return
            for item in AgentLoop(db).handle_turn_stream(request):
                yield _sse(item["event"], item["data"])
                if item["event"] == "complete":
                    source_session_id = str(item["data"].get("sessionId") or request.session_id or "")
                    _schedule_session_title_summary(
                        request.tenant_id,
                        request.user_id,
                        source_session_id,
                        request.agent_id,
                    )
                    if request.interaction_mode != "scheduled_task" or not request.agent_id:
                        continue
                    draft = detect_scheduled_task_draft(
                        db,
                        request.tenant_id,
                        request.agent_id,
                        request.user_id,
                        request.message,
                        source_session_id or None,
                    )
                    if draft and draft.should_create:
                        _persist_scheduled_task_draft(db, request.tenant_id, source_session_id, draft)
                        yield _sse("scheduled_task_draft", draft.model_dump(mode="json"))

    return StreamingResponse(stream_events(), media_type="text/event-stream")


def _sse(event: object, data: object) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/sessions", response_model=ChatSessionRead)
def create_chat_session(
    request: ChatSessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ChatSessionRead:
    _ensure_request_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    _ensure_chat_agent_available(db, request.tenant_id, request.agent_id, current_user)
    title = _normalize_title(request.title)
    row = ChatSession(
        id=new_id("session"),
        tenant_id=request.tenant_id,
        user_id=current_user.id,
        agent_id=request.agent_id,
        title=title,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return session_read(row)


@router.get("/sessions", response_model=list[ChatSessionRead])
def list_chat_sessions(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ChatSessionRead]:
    _ensure_request_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    rows = db.exec(
        select(ChatSession)
        .where(ChatSession.tenant_id == tenant_id, ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
    ).all()
    _cleanup_stale_completed_sessions(db, tenant_id, rows)
    return [session_read(row) for row in rows]


@router.put("/sessions/{session_id}", response_model=ChatSessionRead)
def rename_chat_session(
    session_id: str,
    request: ChatSessionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ChatSessionRead:
    _ensure_request_tenant(request.tenant_id, current_user)
    row = _get_user_chat_session(db, request.tenant_id, current_user.id, session_id)
    row.title = _normalize_title(request.title)
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return session_read(row)


@router.delete("/sessions/{session_id}")
def delete_chat_session(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    _ensure_request_tenant(tenant_id, current_user)
    row = _get_user_chat_session(db, tenant_id, current_user.id, session_id)
    messages = db.exec(
        select(Message).where(Message.tenant_id == tenant_id, Message.session_id == session_id)
    ).all()
    events = db.exec(
        select(AgentEvent).where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == session_id)
    ).all()
    feedback_rows = db.exec(
        select(MessageFeedback).where(MessageFeedback.tenant_id == tenant_id, MessageFeedback.session_id == session_id)
    ).all()
    skill_feedback_rows = db.exec(
        select(SkillFeedback).where(SkillFeedback.tenant_id == tenant_id, SkillFeedback.session_id == session_id)
    ).all()
    for message in messages:
        db.delete(message)
    for event in events:
        db.delete(event)
    for feedback in feedback_rows:
        db.delete(feedback)
    for feedback in skill_feedback_rows:
        db.delete(feedback)
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/messages", response_model=list[MessageRead])
def list_chat_messages(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[MessageRead]:
    _ensure_request_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    chat_session = db.get(ChatSession, session_id)
    if not chat_session or chat_session.tenant_id != tenant_id or chat_session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    _cleanup_stale_completed_sessions(db, tenant_id, [chat_session])
    rows = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == session_id)
        .order_by(Message.created_at)
    ).all()
    feedback_by_message = _feedback_by_message(db, tenant_id, current_user.id, [row.id for row in rows])
    return [message_read(row, feedback_by_message.get(row.id)) for row in rows]


@router.get("/sessions/{session_id}/events")
def list_chat_session_events(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict]:
    _ensure_request_tenant(tenant_id, current_user)
    _get_user_chat_session(db, tenant_id, current_user.id, session_id)
    rows = db.exec(
        select(AgentEvent)
        .where(
            AgentEvent.tenant_id == tenant_id,
            AgentEvent.session_id == session_id,
            AgentEvent.event_type == "scheduled_task_stream_event",
        )
        .order_by(AgentEvent.created_at)
        .limit(500)
    ).all()
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            **(row.payload_json or {}),
        }
        for row in rows
    ]


@router.post("/messages/{message_id}/feedback")
def upsert_message_feedback(
    message_id: str,
    request: MessageFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    _ensure_request_tenant(request.tenant_id, current_user)
    message_row = _get_feedback_target_message(db, request.tenant_id, current_user.id, message_id)
    existing = db.exec(
        select(MessageFeedback).where(
            MessageFeedback.tenant_id == request.tenant_id,
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == current_user.id,
        )
    ).first()
    now = utc_now()
    if existing:
        existing.rating = request.rating
        existing.analysis_status = "pending"
        existing.analysis_bucket = None
        existing.analysis_reason = None
        existing.analysis_summary = None
        existing.analysis_confidence = None
        existing.analysis_json = {}
        existing.analyzed_at = None
        existing.updated_at = now
        row = existing
    else:
        row = MessageFeedback(
            tenant_id=request.tenant_id,
            session_id=message_row.session_id,
            message_id=message_row.id,
            user_id=current_user.id,
            rating=request.rating,
            analysis_status="pending",
            analysis_json={},
            created_at=now,
            updated_at=now,
        )
    db.add(row)
    _upsert_skill_feedback_for_message(db, request.tenant_id, current_user.id, message_row, request.rating, now)
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=message_row.session_id,
            event_type="message_feedback_changed",
            payload_json={"message_id": message_row.id, "rating": request.rating, "user_id": current_user.id},
        )
    )
    db.commit()
    db.refresh(row)
    enqueue_feedback_analysis(row.tenant_id, row.id, row.session_id)
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "session_id": row.session_id,
        "message_id": row.message_id,
        "rating": row.rating,
        "analysis_status": row.analysis_status,
        "updated_at": row.updated_at.isoformat(),
    }


@router.delete("/messages/{message_id}/feedback")
def delete_message_feedback(
    message_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    _ensure_request_tenant(tenant_id, current_user)
    message_row = _get_feedback_target_message(db, tenant_id, current_user.id, message_id)
    existing = db.exec(
        select(MessageFeedback).where(
            MessageFeedback.tenant_id == tenant_id,
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == current_user.id,
        )
    ).first()
    if existing:
        db.delete(existing)
        _delete_skill_feedback_for_message(db, tenant_id, current_user.id, message_row)
        db.add(
            AgentEvent(
                tenant_id=tenant_id,
                session_id=message_row.session_id,
                event_type="message_feedback_changed",
                payload_json={"message_id": message_row.id, "rating": None, "user_id": current_user.id},
            )
        )
        db.commit()
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/trace")
def list_chat_session_trace(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict]:
    _ensure_request_tenant(tenant_id, current_user)
    _get_user_chat_session(db, tenant_id, current_user.id, session_id)
    messages = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == session_id)
        .order_by(Message.created_at)
    ).all()
    events = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == session_id)
        .order_by(AgentEvent.created_at)
    ).all()
    skills = db.exec(select(Skill).where(Skill.tenant_id == tenant_id)).all()
    skill_names = {skill.skill_id: skill.name for skill in skills}
    return _build_turn_traces(messages, events, skill_names)


def _get_user_chat_session(db: Session, tenant_id: str, user_id: str, session_id: str) -> ChatSession:
    ensure_tenant(db, tenant_id)
    row = db.get(ChatSession, session_id)
    if not row or row.tenant_id != tenant_id or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return row


def _ensure_chat_agent_available(
    db: Session,
    tenant_id: str,
    agent_id: str | None,
    current_user: User,
) -> AgentProfile:
    if not agent_id:
        raise HTTPException(status_code=400, detail="Agent is required")
    ensure_tenant(db, tenant_id)
    row = db.get(AgentProfile, agent_id)
    if not row or row.tenant_id != tenant_id or row.status != "active" or row.is_overall:
        raise HTTPException(status_code=404, detail="Agent not available")
    if not _chat_agent_visible_to_user(row, current_user):
        raise HTTPException(status_code=403, detail="Agent not available")
    return row


def _bind_request_to_session_agent(
    db: Session,
    request: ChatTurnRequest,
    chat_session: ChatSession,
    current_user: User,
) -> ChatTurnRequest:
    if chat_session.agent_id:
        if request.agent_id and request.agent_id != chat_session.agent_id:
            raise HTTPException(status_code=409, detail="Session is already bound to another agent")
        return request.model_copy(update={"agent_id": chat_session.agent_id})

    agent = _ensure_chat_agent_available(db, request.tenant_id, request.agent_id, current_user)
    chat_session.agent_id = agent.id
    chat_session.updated_at = utc_now()
    db.add(chat_session)
    db.commit()
    return request.model_copy(update={"agent_id": agent.id})


def _ensure_chat_session_available(db: Session, tenant_id: str, user_id: str, session_id: str) -> ChatSession:
    ensure_tenant(db, tenant_id)
    row = db.get(ChatSession, session_id)
    if not row or row.tenant_id != tenant_id or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return row


def _get_feedback_target_message(db: Session, tenant_id: str, user_id: str, message_id: str) -> Message:
    ensure_tenant(db, tenant_id)
    row = db.get(Message, message_id)
    if not row or row.tenant_id != tenant_id or row.role != "assistant":
        raise HTTPException(status_code=404, detail="Message not found")
    chat_session = db.get(ChatSession, row.session_id)
    if not chat_session or chat_session.tenant_id != tenant_id or chat_session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Message not found")
    return row


def _feedback_by_message(
    db: Session,
    tenant_id: str,
    user_id: str,
    message_ids: list[str],
) -> dict[str, str]:
    if not message_ids:
        return {}
    rows = db.exec(
        select(MessageFeedback).where(
            MessageFeedback.tenant_id == tenant_id,
            MessageFeedback.user_id == user_id,
            MessageFeedback.message_id.in_(message_ids),  # type: ignore[attr-defined]
        )
    ).all()
    return {row.message_id: row.rating for row in rows}


def _cleanup_stale_completed_sessions(
    db: Session,
    tenant_id: str,
    rows: list[ChatSession],
) -> None:
    candidates = [row for row in rows if row.active_skill_id]
    if not candidates:
        return
    skills = list(
        db.exec(
            select(Skill).where(Skill.tenant_id == tenant_id, Skill.status == "published")
        ).all()
    )
    if not skills:
        return
    loop = AgentLoop(db)
    changed = False
    for row in candidates:
        before = (
            row.active_skill_id,
            row.active_step_id,
            json.dumps(row.slots_json or {}, sort_keys=True, ensure_ascii=False),
        )
        loop._finish_stale_completed_skill(tenant_id, row, skills)
        after = (
            row.active_skill_id,
            row.active_step_id,
            json.dumps(row.slots_json or {}, sort_keys=True, ensure_ascii=False),
        )
        changed = changed or before != after
    if changed:
        db.commit()
        for row in candidates:
            db.refresh(row)


def _upsert_skill_feedback_for_message(
    db: Session,
    tenant_id: str,
    user_id: str,
    message_row: Message,
    rating: str,
    now,
) -> None:
    skill_context = _active_skill_context_for_assistant_message(db, tenant_id, message_row)
    if not skill_context:
        return
    skill_id = skill_context["skill_id"]
    skill_version = skill_context.get("skill_version")
    step_id = skill_context.get("node_id") or skill_context.get("step_id")
    existing = db.exec(
        select(SkillFeedback).where(
            SkillFeedback.tenant_id == tenant_id,
            SkillFeedback.message_id == message_row.id,
            SkillFeedback.user_id == user_id,
        )
    ).first()
    if existing:
        existing.skill_id = skill_id
        existing.skill_version = skill_version
        existing.step_id = step_id
        existing.rating = rating
        existing.updated_at = now
        db.add(existing)
        return
    db.add(
        SkillFeedback(
            tenant_id=tenant_id,
            skill_id=skill_id,
            skill_version=skill_version,
            step_id=step_id,
            session_id=message_row.session_id,
            message_id=message_row.id,
            user_id=user_id,
            rating=rating,
            created_at=now,
            updated_at=now,
        )
    )


def _delete_skill_feedback_for_message(
    db: Session,
    tenant_id: str,
    user_id: str,
    message_row: Message,
) -> None:
    existing = db.exec(
        select(SkillFeedback).where(
            SkillFeedback.tenant_id == tenant_id,
            SkillFeedback.message_id == message_row.id,
            SkillFeedback.user_id == user_id,
        )
    ).first()
    if existing:
        db.delete(existing)


def _active_skill_for_assistant_message(db: Session, tenant_id: str, message_row: Message) -> str | None:
    context = _active_skill_context_for_assistant_message(db, tenant_id, message_row)
    return context["skill_id"] if context else None


def _active_skill_context_for_assistant_message(
    db: Session, tenant_id: str, message_row: Message
) -> dict[str, str | None] | None:
    messages = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == message_row.session_id)
        .order_by(Message.created_at)
    ).all()
    target_index = next((index for index, item in enumerate(messages) if item.id == message_row.id), -1)
    if target_index < 0:
        return None
    user_message = next(
        (item for item in reversed(messages[:target_index]) if item.role == "user"),
        None,
    )
    if not user_message:
        return None

    events = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == message_row.session_id)
        .order_by(AgentEvent.created_at)
    ).all()
    collecting = False
    last_context: dict[str, str | None] | None = None
    skill_hint: str | None = None
    for event in events:
        payload = event.payload_json or {}
        if event.event_type == "user_message_received":
            collecting = str(payload.get("message") or "") == user_message.content
            last_context = None if collecting else last_context
            skill_hint = None if collecting else skill_hint
            continue
        if not collecting:
            continue
        if event.event_type == "router_decision_created":
            target_skill_id = str(payload.get("target_skill_id") or "").strip()
            if target_skill_id:
                skill_hint = target_skill_id
        event_context = _skill_context_from_event(event, skill_hint=skill_hint)
        if event_context:
            last_context = event_context
            if event_context.get("skill_id"):
                skill_hint = event_context["skill_id"]
        if event.event_type == "assistant_message_created" and str(payload.get("reply") or "") == message_row.content:
            return _fill_skill_context_version(db, tenant_id, last_context)
    return _fill_skill_context_version(db, tenant_id, last_context)


def _skill_id_from_event(event: AgentEvent) -> str | None:
    context = _skill_context_from_event(event)
    return context["skill_id"] if context else None


def _skill_context_from_event(event: AgentEvent, skill_hint: str | None = None) -> dict[str, str | None] | None:
    payload = event.payload_json or {}
    if event.event_type in {"skill_started", "skill_suspended", "skill_resumed", "skill_step_changed"}:
        skill_id = str(payload.get("to_skill_id") or payload.get("from_skill_id") or skill_hint or "") or None
        if not skill_id:
            return None
        skill_version = str(payload.get("to_skill_version") or payload.get("from_skill_version") or "") or None
        node_id = str(
            payload.get("to_node_id")
            or payload.get("from_node_id")
            or payload.get("to_step_id")
            or payload.get("from_step_id")
            or ""
        ) or None
        return {"skill_id": skill_id, "skill_version": skill_version, "node_id": node_id}
    if event.event_type == "skill_completed":
        skill_id = str(payload.get("skill_id") or "") or None
        if not skill_id:
            return None
        return {
            "skill_id": skill_id,
            "skill_version": str(payload.get("skill_version") or "") or None,
            "node_id": str(payload.get("node_id") or payload.get("step_id") or "") or None,
        }
    if event.event_type == "reflection_decision_created":
        skill_id = str(payload.get("target_skill_id") or "") or None
        if not skill_id:
            return None
        return {
            "skill_id": skill_id,
            "skill_version": str(payload.get("target_skill_version") or "") or None,
            "node_id": str(payload.get("target_node_id") or payload.get("target_step_id") or "") or None,
        }
    return None


def _fill_skill_context_version(
    db: Session, tenant_id: str, context: dict[str, str | None] | None
) -> dict[str, str | None] | None:
    if not context or context.get("skill_version"):
        return context
    skill_id = context.get("skill_id")
    if not skill_id:
        return context
    skill = db.exec(select(Skill).where(Skill.tenant_id == tenant_id, Skill.skill_id == skill_id)).first()
    if skill:
        return {**context, "skill_version": skill.version}
    return context


def _trace_payload_text(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
        except Exception:
            return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _trace_payload_language(value: str) -> str:
    if not value.strip():
        return "text"
    try:
        json.loads(value)
        return "json"
    except Exception:
        return "text"


def _general_skill_trace_detail(payload: dict, phase: str) -> str | None:
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    if phase.startswith("reflection_"):
        parts = [
            str(review.get("reason") or "").strip(),
            str(review.get("repair_hint") or "").strip(),
        ]
        text = " · ".join(part for part in parts if part)
        return text or None
    detail = str(payload.get("rationale") or payload.get("text") or "").strip()
    return detail or None


def _general_skill_trace_output(payload: dict, phase: str) -> dict[str, str]:
    if phase == "stdout_chunk":
        output = _trace_payload_text(payload.get("stdout_preview") or payload.get("text"))
        return {
            "output": output,
            "outputLanguage": _trace_payload_language(output),
            "outputTitle": "查看运行输出",
        } if output else {}
    if phase == "stderr_chunk":
        output = _trace_payload_text(payload.get("stderr_preview") or payload.get("text"))
        return {
            "output": output,
            "outputLanguage": _trace_payload_language(output),
            "outputTitle": "查看错误输出",
        } if output else {}
    if phase in {"code_finished", "code_timeout"}:
        result: dict[str, object] = {}
        if "return_code" in payload:
            result["return_code"] = payload.get("return_code")
        if "structured_result" in payload:
            result["structured_result"] = payload.get("structured_result")
        if str(payload.get("stdout_preview") or "").strip():
            result["stdout"] = payload.get("stdout_preview")
        if str(payload.get("stderr_preview") or "").strip():
            result["stderr"] = payload.get("stderr_preview")
        output = _trace_payload_text(result if result else payload.get("stdout_preview") or payload.get("stderr_preview"))
        return {
            "output": output,
            "outputLanguage": _trace_payload_language(output),
            "outputTitle": "查看超时结果" if phase == "code_timeout" else "查看执行结果",
        } if output else {}
    if phase.startswith("reflection_"):
        result: dict[str, object] = {}
        if "structured_result" in payload:
            result["structured_result"] = payload.get("structured_result")
        if "review" in payload:
            result["review"] = payload.get("review")
        if str(payload.get("stdout_preview") or "").strip():
            result["stdout"] = payload.get("stdout_preview")
        if str(payload.get("stderr_preview") or "").strip():
            result["stderr"] = payload.get("stderr_preview")
        output = _trace_payload_text(result)
        return {
            "output": output,
            "outputLanguage": _trace_payload_language(output),
            "outputTitle": "查看校验详情",
        } if result and output else {}
    return {}


def _ensure_request_tenant(tenant_id: str, current_user: User) -> None:
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")


def _chat_agent_visible_to_user(row: AgentProfile, user: User) -> bool:
    if user.username in {"admin", "admin_demo"}:
        return True
    metadata = row.metadata_json or {}
    return (
        metadata.get("owner_user_id") == user.id
        or metadata.get("owner_username") == user.username
        or metadata.get("created_by_user_id") == user.id
        or metadata.get("created_by_username") == user.username
        or metadata.get("published_to_gallery") is True
    )


def _normalize_title(value: str | None) -> str | None:
    if value is None:
        return None
    title = value.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Session title cannot be empty")
    return title[:80]


def _build_turn_traces(
    messages: list[Message],
    events: list[AgentEvent],
    skill_names: dict[str, str],
) -> list[dict]:
    if not events:
        return _fallback_knowledge_citation_traces(messages)

    user_messages = [message for message in messages if message.role == "user"]
    traces: list[dict] = []
    current: dict | None = None
    user_index = 0
    skill_hint: str | None = None

    for event in events:
        if event.event_type == "user_message_received":
            if current:
                _ensure_knowledge_query_line(current["lines"], current.get("_user_message_content"))
                _finish_trace_if_needed(current, event.created_at)
                traces.append(current)
            skill_hint = None
            text = str((event.payload_json or {}).get("message") or "")
            user_message = _matching_user_message(user_messages, user_index, event.payload_json)
            if user_message:
                user_index = user_messages.index(user_message) + 1
            current = {
                "turn_id": user_message.id if user_message else event.id,
                "user_message_id": user_message.id if user_message else None,
                "_user_message_content": user_message.content if user_message else text,
                "started_at": event.created_at.isoformat(),
                "completed_at": None,
                "lines": [
                    {
                        "id": "thinking",
                        "kind": "thinking",
                        "text": "已完成思考",
                        "state": "completed",
                    }
                ],
            }
            continue

        if not current:
            continue
        if current.get("completed_at"):
            continue

        if event.event_type == "router_decision_created":
            target_skill_id = str((event.payload_json or {}).get("target_skill_id") or "").strip()
            if target_skill_id:
                skill_hint = target_skill_id

        lines = _event_trace_lines(event, skill_names, skill_hint)
        for line in lines:
            _upsert_trace_line(current["lines"], line)
        event_context = _skill_context_from_event(event, skill_hint=skill_hint)
        if event_context and event_context.get("skill_id"):
            skill_hint = event_context["skill_id"]
        if event.event_type == "assistant_message_created":
            current["completed_at"] = event.created_at.isoformat()
            _ensure_knowledge_query_line(current["lines"], current.get("_user_message_content"))
            _complete_trace_lines(current["lines"])

    if current:
        _ensure_knowledge_query_line(current["lines"], current.get("_user_message_content"))
        _finish_trace_if_needed(current, events[-1].created_at if events else None)
        traces.append(current)

    for trace in traces:
        trace.pop("_user_message_content", None)
    return _with_scheduled_draft_message_traces(traces, messages)


def _with_scheduled_draft_message_traces(traces: list[dict], messages: list[Message]) -> list[dict]:
    traced_turn_ids = {str(trace.get("turn_id") or "") for trace in traces}
    next_traces = list(traces)
    previous_user: Message | None = None
    for message in messages:
        if message.role == "user":
            previous_user = message
            continue
        if message.role != "assistant" or not previous_user:
            continue
        metadata = message.metadata_json or {}
        draft = metadata.get("scheduled_task_draft") if isinstance(metadata, dict) else None
        if not isinstance(draft, dict) or previous_user.id in traced_turn_ids:
            continue
        title = str(draft.get("title") or "").strip()
        detail = " · ".join(part for part in (title, "等待确认后启用") if part)
        next_traces.append(
            {
                "turn_id": previous_user.id,
                "user_message_id": previous_user.id,
                "started_at": previous_user.created_at.isoformat(),
                "completed_at": message.created_at.isoformat(),
                "lines": [
                    {
                        "id": "thinking",
                        "kind": "thinking",
                        "text": "已完成思考",
                        "state": "completed",
                    },
                    {
                        "id": f"scheduled_task_draft_{message.id}",
                        "kind": "decision",
                        "text": "生成自动任务草案",
                        "detail": detail or None,
                        "state": "completed",
                    },
                ],
            }
        )
        traced_turn_ids.add(previous_user.id)
    next_traces.sort(key=lambda item: str(item.get("started_at") or ""))
    return next_traces


def _fallback_knowledge_citation_traces(messages: list[Message]) -> list[dict]:
    traces: list[dict] = []
    current_user: Message | None = None

    for message in messages:
        if message.role == "user":
            current_user = message
            continue
        if message.role != "assistant":
            continue
        metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
        citations = metadata.get("knowledge_citations") if isinstance(metadata, dict) else None
        if not isinstance(citations, list) or not citations:
            continue

        citation_titles = [
            str(item.get("title") or item.get("source_title") or item.get("concept_id") or "").strip()
            for item in citations
            if isinstance(item, dict)
        ]
        citation_titles = [title for title in citation_titles if title]
        citation_summary = "、".join(citation_titles[:3])
        if len(citation_titles) > 3:
            citation_summary = f"{citation_summary} 等"

        traces.append(
            {
                "turn_id": current_user.id if current_user else message.id,
                "user_message_id": current_user.id if current_user else None,
                "started_at": (current_user.created_at if current_user else message.created_at).isoformat(),
                "completed_at": message.created_at.isoformat(),
                "lines": [
                    {
                        "id": "thinking",
                        "kind": "thinking",
                        "text": "执行记录",
                        "state": "completed",
                    },
                    {
                        "id": "knowledge_intent",
                        "kind": "decision",
                        "text": "识别为业务资料问答",
                        "detail": "回答需要引用业务资料库，进入知识检索链路。",
                        "state": "completed",
                    },
                    {
                        "id": "knowledge_query",
                        "kind": "knowledge",
                        "text": "查询业务资料",
                        "detail": current_user.content if current_user else None,
                        "state": "completed",
                    },
                    {
                        "id": "knowledge_retrieval",
                        "kind": "knowledge",
                        "text": "读取业务资料",
                        "detail": (
                            f"命中 {len(citations)} 条知识引用"
                            + (f"：{citation_summary}" if citation_summary else "")
                        ),
                        "state": "completed",
                    },
                    {
                        "id": "knowledge_answer",
                        "kind": "decision",
                        "text": "生成带引用回答",
                        "detail": "已将知识引用附加到回复下方，可点击查看来源、章节和证据片段。",
                        "state": "completed",
                    },
                ],
            }
        )

    return traces


def _ensure_knowledge_query_line(lines: list[dict], user_message: object | None = None) -> None:
    has_query = any(line.get("text") == "查询业务资料" for line in lines)
    read_index = next(
        (index for index, line in enumerate(lines) if line.get("text") == "读取业务资料"),
        None,
    )
    if has_query or read_index is None:
        return
    detail = str(user_message or "").strip()
    lines.insert(
        read_index,
        {
            "id": "knowledge_query_synthetic",
            "kind": "knowledge",
            "text": "查询业务资料",
            "detail": detail or None,
            "state": "completed",
        },
    )


def _matching_user_message(
    user_messages: list[Message],
    start_index: int,
    payload: dict,
) -> Message | None:
    text = str(payload.get("message") or "")
    for index in range(start_index, len(user_messages)):
        if user_messages[index].content == text:
            return user_messages[index]
    if start_index < len(user_messages):
        return user_messages[start_index]
    return None


def _event_trace_lines(event: AgentEvent, skill_names: dict[str, str], skill_hint: str | None = None) -> list[dict]:
    line = _event_trace_line(event, skill_names, skill_hint)
    if not line:
        return []
    if isinstance(line, list):
        return line
    return [line]


def _event_trace_line(
    event: AgentEvent, skill_names: dict[str, str], skill_hint: str | None = None
) -> dict | list[dict] | None:
    payload = event.payload_json or {}
    if event.event_type == "general_skill_selected":
        skill_name = str(payload.get("skill_name") or payload.get("skill_slug") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        return {
            "id": f"general_skill_selected_{event.id}",
            "kind": "skill",
            "text": f"选择通用技能 {skill_name}" if skill_name else "选择通用技能",
            "detail": reason or None,
            "state": "completed",
        }
    if event.event_type == "general_skill_intent_checked":
        skill_name = str(payload.get("skill_name") or payload.get("skill_slug") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        return {
            "id": f"general_skill_intent_{event.id}",
            "kind": "decision",
            "text": "判断意图" if not skill_name else f"判断意图 {skill_name}",
            "detail": reason or None,
            "state": "completed",
        }
    if event.event_type == "general_skill_trace":
        message = str(payload.get("message") or "").strip()
        phase = str(payload.get("phase") or "").strip()
        detail = _general_skill_trace_detail(payload, phase)
        output = _general_skill_trace_output(payload, phase)
        code = str(payload.get("code") or "").strip()
        runtime = str(payload.get("runtime") or "").strip().lower()
        code_phases = {
            "plan_created",
            "attempt_started",
            "running_code",
            "stdout_chunk",
            "stderr_chunk",
            "code_finished",
            "code_timeout",
        }
        return {
            "id": f"general_skill_trace_{event.id}",
            "kind": "code" if code or phase in code_phases else "decision",
            "text": message or phase or "执行通用技能",
            "detail": detail or None,
            "code": code or None,
            "language": "bash" if code and runtime == "bash" else "python" if code else None,
            "state": "completed",
            "collapsible": bool(code or output.get("output")),
            **output,
        }
    if event.event_type == "general_skill_run_finished":
        success = bool(payload.get("success"))
        return {
            "id": f"general_skill_finished_{event.id}",
            "kind": "skill",
            "text": "通用技能运行完成" if success else "通用技能运行失败",
            "detail": str(payload.get("skill_slug") or "") or None,
            "state": "completed" if success else "failed",
        }
    if event.event_type == "scheduled_task_draft_created":
        title = str(payload.get("title") or "").strip()
        schedule = str(payload.get("schedule_label") or "").strip()
        detail = " · ".join(part for part in (title, schedule, "等待确认后启用") if part)
        return {
            "id": f"scheduled_task_draft_{event.id}",
            "kind": "decision",
            "text": "生成自动任务草案",
            "detail": detail or None,
            "state": "completed",
        }
    if event.event_type == "router_decision_created":
        intent = str(payload.get("user_intent") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        return {
            "id": f"decision_{event.id}",
            "kind": "decision",
            "text": f"判断意图 {intent}" if intent else "完成技能判断",
            "detail": reason or None,
            "state": "completed",
        }
    if event.event_type in {"skill_started", "skill_suspended", "skill_resumed", "skill_step_changed"}:
        to_skill_id = str(payload.get("to_skill_id") or "")
        from_skill_id = str(payload.get("from_skill_id") or "")
        skill_id = to_skill_id or from_skill_id or (skill_hint or "")
        if not skill_id:
            return None
        decision = str(payload.get("decision") or "")
        is_interrupt_switch = (
            decision in {"answer_related_question_then_resume", "answer_chitchat_then_resume"}
            and from_skill_id
            and to_skill_id
            and from_skill_id != to_skill_id
        )
        label = {
            "skill_started": "选择技能",
            "skill_suspended": "切换技能",
            "skill_resumed": "恢复技能",
            "skill_step_changed": "推进技能",
        }[event.event_type]
        if is_interrupt_switch:
            label = "切换技能"
        detail_parts = []
        if from_skill_id and from_skill_id != to_skill_id:
            detail_parts.append(f"from {skill_names.get(from_skill_id, from_skill_id)}")
        if payload.get("to_step_id"):
            detail_parts.append(f"step {payload['to_step_id']}")
        line = {
            "id": f"skill_{event.id}",
            "kind": "skill",
            "text": f"{label} {skill_names.get(skill_id, skill_id)}",
            "detail": " · ".join(detail_parts) or None,
            "state": "completed",
        }
        if event.event_type != "skill_suspended" and not is_interrupt_switch:
            return line
        stack_lines = []
        for index, frame in enumerate(payload.get("skill_stack") or []):
            if not isinstance(frame, dict):
                continue
            suspended_skill_id = str(frame.get("skill_id") or "")
            if not suspended_skill_id or suspended_skill_id == skill_id:
                continue
            suspended_step_id = str(frame.get("step_id") or "").strip()
            stack_lines.append(
                {
                    "id": f"skill_{event.id}_suspended_{index}",
                    "kind": "skill",
                    "text": f"挂起技能 {skill_names.get(suspended_skill_id, suspended_skill_id)}",
                    "detail": f"当前步骤 {suspended_step_id}" if suspended_step_id else None,
                    "state": "completed",
                }
            )
        if not stack_lines and from_skill_id and from_skill_id != skill_id:
            from_step_id = str(payload.get("from_step_id") or "").strip()
            stack_lines.append(
                {
                    "id": f"skill_{event.id}_suspended_from",
                    "kind": "skill",
                    "text": f"挂起技能 {skill_names.get(from_skill_id, from_skill_id)}",
                    "detail": f"当前步骤 {from_step_id}" if from_step_id else None,
                    "state": "completed",
                }
            )
        return [*stack_lines, line]
    if event.event_type == "skill_completed":
        skill_id = str(payload.get("skill_id") or "")
        return {
            "id": f"skill_{event.id}",
            "kind": "skill",
            "text": f"完成技能 {skill_names.get(skill_id, skill_id)}" if skill_id else "完成技能",
            "detail": str(payload.get("reason") or "") or None,
            "state": "completed",
        }
    if event.event_type == "tool_call_started":
        name = str(payload.get("name") or "")
        tool_call_id = str(payload.get("tool_call_id") or name or event.id)
        if not name:
            return None
        return {
            "id": f"tool_{tool_call_id}",
            "kind": "tool",
            "text": f"调用工具 {name}",
            "detail": None,
            "state": "running",
        }
    if event.event_type == "knowledge_query_started":
        query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
        text = str(query.get("query") if isinstance(query, dict) else payload.get("text") or "").strip()
        return {
            "id": f"knowledge_{event.id}_started",
            "kind": "knowledge",
            "text": "查询业务资料",
            "detail": text or None,
            "state": "running",
        }
    if event.event_type == "knowledge_query_finished":
        chunks = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
        buckets = payload.get("selected_buckets") if isinstance(payload.get("selected_buckets"), list) else []
        concepts = payload.get("selected_concepts") if isinstance(payload.get("selected_concepts"), list) else []
        evidence = payload.get("evidence_pack") if isinstance(payload.get("evidence_pack"), list) else []
        parts = [
            f"命中 Wiki {len(concepts)} 个" if concepts else "",
            f"展开 {len(buckets)} 个知识桶" if buckets else "",
            f"读取 {len(chunks)} 个片段" if chunks else "",
            f"生成 {len(evidence)} 条引用候选" if evidence else "",
        ]
        return {
            "id": f"knowledge_{event.id}_finished",
            "kind": "knowledge",
            "text": "读取业务资料",
            "detail": " · ".join(part for part in parts if part),
            "state": "completed",
        }
    if event.event_type == "tool_call_finished":
        name = str(payload.get("tool_name") or "")
        tool_call_id = str(payload.get("tool_call_id") or name or event.id)
        success = bool(payload.get("success"))
        return {
            "id": f"tool_{tool_call_id}",
            "kind": "tool",
            "text": f"{'调用工具' if success else '工具调用失败'} {name}",
            "detail": _tool_trace_detail(payload),
            "state": "completed" if success else "failed",
        }
    if event.event_type == "agent_loop_continued":
        iteration = str(payload.get("iteration") or event.id)
        target_tool = str(payload.get("target_tool_name") or "").strip()
        return {
            "id": f"decision_stepping_tool_continuation_{iteration}",
            "kind": "decision",
            "text": "重新分析执行动作",
            "detail": f"决定继续调用工具 {target_tool}" if target_tool else "决定继续调用工具",
            "state": "completed",
        }
    if event.event_type == "agent_loop_completed":
        iteration = str(payload.get("iteration") or event.id)
        return [
            {
                "id": f"decision_stepping_tool_continuation_{iteration}",
                "kind": "decision",
                "text": "重新分析执行动作",
                "detail": "判断无需继续调用工具",
                "state": "completed",
            },
            {
                "id": f"decision_responding_{iteration}",
                "kind": "decision",
                "text": "组织回复",
                "detail": None,
                "state": "completed",
            },
        ]
    if event.event_type == "reflection_decision_created":
        needs_retry = bool(payload.get("needs_retry"))
        return {
            "id": f"decision_{event.id}",
            "kind": "decision",
            "text": "反思后继续尝试" if needs_retry else "反思通过",
            "detail": _reflection_trace_detail(payload),
            "state": "completed",
        }
    if event.event_type == "reflection_skipped":
        return {
            "id": f"decision_{event.id}",
            "kind": "decision",
            "text": "反思已关闭",
            "detail": str(payload.get("reason") or "") or None,
            "state": "completed",
        }
    if event.event_type == "reflection_retry_started":
        mode = str(payload.get("mode") or "").strip()
        target_tool = str(payload.get("target_tool_name") or "").strip()
        target_skill = str(payload.get("target_skill_id") or "").strip()
        target = target_tool or skill_names.get(target_skill, target_skill)
        return {
            "id": f"decision_{event.id}",
            "kind": "decision",
            "text": f"重试{ '工具' if mode == 'tool' else '技能' } {target}".strip(),
            "detail": str(payload.get("reason") or "") or None,
            "state": "completed",
        }
    if event.event_type == "error_occurred":
        return {
            "id": f"error_{event.id}",
            "kind": "thinking",
            "text": "思考遇到问题",
            "detail": str(payload.get("message") or "") or None,
            "state": "failed",
        }
    return None


def _tool_trace_detail(payload: dict) -> str | None:
    data = payload.get("data")
    data_dict = data if isinstance(data, dict) else {}
    parts = [
        str(data_dict.get("source") or "").strip(),
        "未命中" if data_dict.get("found") is False else "已命中" if data_dict.get("found") is True else "",
        str(data_dict.get("miss_reason") or "").strip(),
        str(data_dict.get("recommendation") or "").strip(),
    ]
    text = " · ".join(part for part in parts if part)
    return text or None


def _reflection_trace_detail(payload: dict) -> str | None:
    parts = [
        str(payload.get("reason") or "").strip(),
        f"工具 {payload['target_tool_name']}" if payload.get("target_tool_name") else "",
        f"技能 {payload['target_skill_id']}" if payload.get("target_skill_id") else "",
        f"步骤 {payload['target_step_id']}" if payload.get("target_step_id") else "",
    ]
    text = " · ".join(part for part in parts if part)
    return text or None


def _upsert_trace_line(lines: list[dict], line: dict) -> None:
    for index, item in enumerate(lines):
        if item.get("id") == line.get("id"):
            lines[index] = line
            return
    lines.append(line)


def _complete_trace_lines(lines: list[dict]) -> None:
    for line in lines:
        if line.get("state") == "running":
            line["state"] = "completed"
    thinking = next((line for line in lines if line.get("id") == "thinking"), None)
    if thinking:
        thinking["text"] = "已完成思考"
        thinking["state"] = "completed"


def _finish_trace_if_needed(trace: dict, fallback_time) -> None:
    if not trace.get("completed_at") and fallback_time:
        trace["completed_at"] = fallback_time.isoformat()
    _complete_trace_lines(trace["lines"])
