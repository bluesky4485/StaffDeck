from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.api.chat import message_read, session_read
from app.db import get_session
from app.db.models import ChatSession, Message, MessageFeedback, User, utc_now
from app.feedback import FEEDBACK_BUCKET_LABELS, enqueue_feedback_analysis, feedback_analysis_read, feedback_summary
from app.security.tenant import ensure_tenant

router = APIRouter(prefix="/api/enterprise/feedback", tags=["enterprise:feedback"])


@router.get("/summary")
def get_feedback_summary(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    db: Session = Depends(get_session),
) -> dict:
    ensure_tenant(db, tenant_id)
    rows = list(
        db.exec(
            select(MessageFeedback)
            .where(MessageFeedback.tenant_id == tenant_id)
            .order_by(MessageFeedback.updated_at.desc())
            .limit(limit)
        ).all()
    )
    if agent_id:
        rows = [row for row in rows if _feedback_matches_agent(db, tenant_id, row, agent_id)]
    return feedback_summary(rows)


@router.get("/sessions")
def list_feedback_sessions(
    tenant_id: str = Query(...),
    rating: str = Query(default="down"),
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> list[dict]:
    ensure_tenant(db, tenant_id)
    feedback_rows = list(
        db.exec(
            select(MessageFeedback)
            .where(MessageFeedback.tenant_id == tenant_id, MessageFeedback.rating == rating)
            .order_by(MessageFeedback.updated_at.desc())
            .limit(limit)
        ).all()
    )
    grouped: dict[str, list[MessageFeedback]] = {}
    for row in feedback_rows:
        grouped.setdefault(row.session_id, []).append(row)

    results: list[dict] = []
    for session_id, rows in grouped.items():
        chat_session = db.get(ChatSession, session_id)
        if not chat_session or chat_session.tenant_id != tenant_id:
            continue
        if agent_id and chat_session.agent_id != agent_id:
            continue
        latest = max(rows, key=lambda item: item.updated_at)
        latest_analysis = feedback_analysis_read(latest)
        latest_message = db.get(Message, latest.message_id)
        user = db.get(User, chat_session.user_id) if chat_session.user_id else None
        down_rows = [item for item in rows if item.rating == "down"]
        bucket_counts: dict[str, int] = {}
        for item in down_rows:
            bucket = item.analysis_bucket or "unknown"
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        primary_bucket = max(bucket_counts.items(), key=lambda item: item[1])[0] if bucket_counts else None
        results.append(
            {
                "session_id": chat_session.id,
                "tenant_id": chat_session.tenant_id,
                "agent_id": chat_session.agent_id,
                "user_id": chat_session.user_id,
                "username": user.username if user else None,
                "display_name": user.display_name if user else None,
                "title": chat_session.title,
                "summary": chat_session.summary,
                "status": chat_session.status,
                "feedback_count": len(rows),
                "latest_feedback_at": latest.updated_at.isoformat(),
                "latest_message_id": latest.message_id,
                "latest_message": latest_message.content if latest_message else "",
                "analysis_status": latest_analysis["status"],
                "analysis_bucket": latest_analysis["bucket"],
                "analysis_bucket_label": latest_analysis["bucket_label"],
                "analysis_summary": latest_analysis["summary"],
                "primary_bucket": primary_bucket,
                "primary_bucket_label": FEEDBACK_BUCKET_LABELS.get(primary_bucket or "unknown", primary_bucket or "unknown"),
                "bucket_counts": bucket_counts,
                "updated_at": chat_session.updated_at.isoformat(),
            }
        )
    return sorted(results, key=lambda item: item["latest_feedback_at"], reverse=True)


def _feedback_matches_agent(db: Session, tenant_id: str, feedback: MessageFeedback, agent_id: str) -> bool:
    chat_session = db.get(ChatSession, feedback.session_id)
    return bool(chat_session and chat_session.tenant_id == tenant_id and chat_session.agent_id == agent_id)


@router.get("/sessions/{session_id}")
def get_feedback_session_detail(
    session_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
) -> dict:
    ensure_tenant(db, tenant_id)
    chat_session = db.get(ChatSession, session_id)
    if not chat_session or chat_session.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = list(
        db.exec(
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.session_id == session_id)
            .order_by(Message.created_at)
        ).all()
    )
    feedback_rows = list(
        db.exec(
            select(MessageFeedback)
            .where(MessageFeedback.tenant_id == tenant_id, MessageFeedback.session_id == session_id)
            .order_by(MessageFeedback.updated_at.desc())
        ).all()
    )
    feedback_by_message = {row.message_id: row for row in feedback_rows}
    user = db.get(User, chat_session.user_id) if chat_session.user_id else None
    return {
        "session": {
            **session_read(chat_session).model_dump(),
            "username": user.username if user else None,
            "display_name": user.display_name if user else None,
        },
        "messages": [_message_with_feedback(message, feedback_by_message.get(message.id)) for message in messages],
        "feedback": [
            {
                "id": row.id,
                "message_id": row.message_id,
                "user_id": row.user_id,
                "rating": row.rating,
                "analysis": feedback_analysis_read(row),
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in feedback_rows
        ],
    }


@router.post("/{feedback_id}/reanalyze")
def reanalyze_feedback(
    feedback_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
) -> dict:
    ensure_tenant(db, tenant_id)
    row = db.get(MessageFeedback, feedback_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Feedback not found")
    now = utc_now()
    row.analysis_status = "pending"
    row.analysis_bucket = None
    row.analysis_reason = None
    row.analysis_summary = None
    row.analysis_confidence = None
    row.analysis_json = {"retry_requested_at": now.isoformat()}
    row.analyzed_at = None
    row.updated_at = now
    db.add(row)
    db.commit()
    db.refresh(row)
    job = enqueue_feedback_analysis(row.tenant_id, row.id, row.session_id)
    return {
        "feedback_id": row.id,
        "analysis_status": row.analysis_status,
        "job_id": job.id,
        "updated_at": row.updated_at.isoformat(),
    }


def _message_with_feedback(message: Message, feedback: MessageFeedback | None) -> dict:
    payload = message_read(message, feedback.rating if feedback else None).model_dump()
    if feedback:
        payload["feedback_id"] = feedback.id
        payload["feedback_updated_at"] = feedback.updated_at.isoformat()
        payload["feedback_analysis"] = feedback_analysis_read(feedback)
    return payload
