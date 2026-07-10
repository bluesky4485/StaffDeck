from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app import paths
from app.db.models import ChatSession, MemoryRecord, ModelConfig, Tool, User, utc_now
from app.llm import LLMClient
from app.session.session_schema import ChatTurnRequest, StepAgentResult
from app.tools.tool_schema import ToolResult


PROMPT_PATH = paths.resource_dir() / "app" / "llm" / "prompts" / "memory_extractor_prompt.md"
RERANK_PROMPT_PATH = paths.resource_dir() / "app" / "llm" / "prompts" / "memory_reranker_prompt.md"
MEMORY_SOURCE = "model_memory_extractor"
PROFILE_NAME_KEY = "preferred_name"
ALLOWED_MEMORY_KINDS = {"profile", "preference", "fact"}


class MemoryService:
    def __init__(self, db: Session):
        self.db = db

    def recall(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        limit: int = 5,
        model_config: ModelConfig | None = None,
        agent_id: str | None = None,
    ) -> list[MemoryRecord]:
        rows = [
            row
            for row in self._list_user_memories(tenant_id, user_id, limit=80, agent_id=agent_id)
            if row.kind in ALLOWED_MEMORY_KINDS
        ]
        profile_rows = [row for row in rows if row.kind == "profile"][:2]
        other_rows = [row for row in rows if row.kind != "profile"]
        if model_config and other_rows:
            ranked = self._llm_rerank_memories(query, other_rows, model_config, limit=max(0, limit - len(profile_rows)))
            recalled = [*profile_rows, *ranked]
            return recalled[:limit]
        query_terms = _terms(query)
        scored = sorted(
            other_rows,
            key=lambda row: (_score(row.content, query_terms), row.importance, row.updated_at),
            reverse=True,
        )
        ranked_other = [row for row in scored if _score(row.content, query_terms) > 0]
        recalled = [*profile_rows, *ranked_other]
        return recalled[:limit] or rows[: min(limit, len(rows))]

    def _llm_rerank_memories(
        self,
        query: str,
        rows: list[MemoryRecord],
        model_config: ModelConfig,
        limit: int,
    ) -> list[MemoryRecord]:
        if limit <= 0:
            return []
        candidates = rows[:30]
        by_id = {row.id: row for row in candidates}
        try:
            raw = LLMClient(model_config).generate_json(
                RERANK_PROMPT_PATH.read_text(encoding="utf-8"),
                {
                    "user_message": query,
                    "candidate_memories": [memory_read(row) for row in candidates],
                    "limit": limit,
                },
            )
        except Exception:
            return self._lexical_recall(query, rows, limit)
        ids = raw.get("memory_ids") if isinstance(raw, dict) else []
        ranked: list[MemoryRecord] = []
        for memory_id in ids if isinstance(ids, list) else []:
            row = by_id.get(str(memory_id))
            if row and row not in ranked:
                ranked.append(row)
            if len(ranked) >= limit:
                break
        if ranked:
            return ranked
        return self._lexical_recall(query, rows, limit)

    def _lexical_recall(self, query: str, rows: list[MemoryRecord], limit: int) -> list[MemoryRecord]:
        query_terms = _terms(query)
        scored = sorted(
            rows,
            key=lambda row: (_score(row.content, query_terms), row.importance, row.updated_at),
            reverse=True,
        )
        return [row for row in scored if _score(row.content, query_terms) > 0][:limit] or scored[:limit]

    def capture_turn(
        self,
        request: ChatTurnRequest,
        session: ChatSession,
        reply: str,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
        model_config: ModelConfig,
        recent_messages: list[dict[str, str]],
    ) -> list[MemoryRecord]:
        if not request.user_id:
            return []

        agent_id = session.agent_id
        user = self.db.get(User, request.user_id)
        username = user.username if user else request.user_id
        existing_rows = self._list_user_memories(
            request.tenant_id,
            request.user_id,
            limit=30,
            normalize=False,
            agent_id=agent_id,
        )
        raw_delta = LLMClient(model_config).generate_json(
            PROMPT_PATH.read_text(encoding="utf-8"),
            {
                "user_message": request.message,
                "assistant_reply": reply,
                "recent_messages": _recent_messages_with_reply(recent_messages, reply),
                "existing_memories": [memory_read(row) for row in existing_rows],
                "step_result": step_result.model_dump(mode="json"),
                "tool_result": tool_result.model_dump(mode="json") if tool_result else None,
            },
        )
        records: list[MemoryRecord] = []
        for update in _normalize_memory_updates(raw_delta):
            if update["operation"] == "delete":
                self._delete_keyed_memory(
                    request.tenant_id,
                    request.user_id,
                    update["kind"],
                    update["key"],
                    agent_id=agent_id,
                )
                continue
            records.append(
                self._upsert_keyed_memory(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    username=username,
                    session_id=session.id,
                    kind=update["kind"],
                    key=update["key"],
                    content=update["content"],
                    importance=update["importance"],
                    metadata={
                        "source": MEMORY_SOURCE,
                        "key": update["key"],
                        "reason": update.get("reason"),
                        "agent_id": agent_id,
                    },
                    agent_id=agent_id,
                )
            )

        return records

    def _list_user_memories(
        self,
        tenant_id: str,
        user_id: str,
        limit: int = 80,
        normalize: bool = True,
        agent_id: str | None = None,
    ) -> list[MemoryRecord]:
        fetch_limit = limit * 5 if agent_id else limit
        rows = list(
            self.db.exec(
                select(MemoryRecord)
                .where(
                    MemoryRecord.tenant_id == tenant_id,
                    MemoryRecord.user_id == user_id,
                    MemoryRecord.kind != "conversation",
                )
                .order_by(MemoryRecord.updated_at.desc())
                .limit(fetch_limit)
            ).all()
        )
        if agent_id:
            rows = [row for row in rows if self._memory_matches_agent(row, agent_id)]
        rows = rows[:limit]
        return memory_rows_for_read(rows) if normalize else rows

    def _upsert_keyed_memory(
        self,
        tenant_id: str,
        user_id: str,
        username: str | None,
        session_id: str,
        kind: str,
        key: str,
        content: str,
        importance: float,
        metadata: dict[str, Any],
        agent_id: str | None = None,
    ) -> MemoryRecord:
        existing, duplicates = self._find_keyed_memory_candidates(tenant_id, user_id, kind, key, agent_id=agent_id)
        now = utc_now()
        if existing:
            existing.content = content[:1200]
            existing.username = username
            existing.session_id = session_id
            existing.importance = importance
            existing.updated_at = now
            existing.metadata_json = {**(existing.metadata_json or {}), **metadata}
            record = existing
        else:
            record = MemoryRecord(
                tenant_id=tenant_id,
                user_id=user_id,
                username=username,
                session_id=session_id,
                kind=kind,
                content=content[:1200],
                importance=importance,
                metadata_json=metadata,
            )
            self.db.add(record)

        for duplicate in duplicates:
            if duplicate.id != record.id:
                self.db.delete(duplicate)
        self.db.add(record)
        return record

    def _delete_keyed_memory(
        self,
        tenant_id: str,
        user_id: str,
        kind: str,
        key: str,
        agent_id: str | None = None,
    ) -> None:
        existing, duplicates = self._find_keyed_memory_candidates(tenant_id, user_id, kind, key, agent_id=agent_id)
        for row in [existing, *duplicates]:
            if row:
                self.db.delete(row)

    def _find_keyed_memory_candidates(
        self,
        tenant_id: str,
        user_id: str,
        kind: str,
        key: str,
        agent_id: str | None = None,
    ) -> tuple[MemoryRecord | None, list[MemoryRecord]]:
        rows = list(
            self.db.exec(
                select(MemoryRecord)
                .where(
                    MemoryRecord.tenant_id == tenant_id,
                    MemoryRecord.user_id == user_id,
                    MemoryRecord.kind == kind,
                )
                .order_by(MemoryRecord.updated_at.desc())
            ).all()
        )
        if agent_id:
            rows = [row for row in rows if self._memory_matches_agent(row, agent_id)]
        candidates = [row for row in rows if _memory_matches_key(row, key)]
        if not candidates:
            return None, []
        return candidates[0], candidates[1:]

    def _upsert_summary(
        self,
        tenant_id: str,
        user_id: str,
        username: str | None,
        session_id: str,
        summary: str,
        metadata: dict[str, Any],
        agent_id: str | None = None,
    ) -> MemoryRecord:
        summary_rows = list(
            self.db.exec(
                select(MemoryRecord)
                .where(
                    MemoryRecord.tenant_id == tenant_id,
                    MemoryRecord.user_id == user_id,
                    MemoryRecord.kind == "summary",
                )
                .order_by(MemoryRecord.updated_at.desc())
            ).all()
        )
        if agent_id:
            existing = next((row for row in summary_rows if self._memory_matches_agent(row, agent_id)), None)
        else:
            existing = summary_rows[0] if summary_rows else None
        now = utc_now()
        if existing:
            existing.content = summary[:1800]
            existing.username = username
            existing.session_id = session_id
            existing.importance = 0.8
            existing.updated_at = now
            existing.metadata_json = {
                **(existing.metadata_json or {}),
                **metadata,
                "agent_id": agent_id,
                "turn_count": int((existing.metadata_json or {}).get("turn_count", 0)) + 1,
            }
            self.db.add(existing)
            return existing
        record = MemoryRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            username=username,
            session_id=session_id,
            kind="summary",
            content=summary[:1800],
            importance=0.8,
            metadata_json={**metadata, "agent_id": agent_id, "turn_count": 1},
        )
        self.db.add(record)
        return record

    def _memory_matches_agent(self, record: MemoryRecord, agent_id: str | None) -> bool:
        if memory_matches_agent(record, agent_id):
            return True
        if not agent_id or memory_agent_id(record) or not record.session_id:
            return False
        session = self.db.get(ChatSession, record.session_id)
        return bool(session and session.agent_id == agent_id)


def memory_read(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "user_id": record.user_id,
        "username": record.username,
        "session_id": record.session_id,
        "kind": record.kind,
        "content": record.content,
        "importance": record.importance,
        "metadata": record.metadata_json or {},
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def memory_rows_for_read(rows: list[MemoryRecord]) -> list[MemoryRecord]:
    visible: list[MemoryRecord] = []
    seen_keys: set[tuple[str, str, str | None, str]] = set()
    for row in rows:
        if _is_legacy_transcript_summary(row):
            continue
        dedupe_key = (row.user_id, row.kind, memory_agent_id(row), _read_dedupe_key(row))
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        visible.append(row)
    return visible


def memory_agent_id(record: MemoryRecord) -> str | None:
    metadata = record.metadata_json or {}
    value = metadata.get("agent_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def memory_matches_agent(record: MemoryRecord, agent_id: str | None) -> bool:
    if not agent_id:
        return True
    return memory_agent_id(record) == agent_id


def tool_read_for_activity(tool: Tool | None, result: ToolResult | None = None) -> dict[str, Any]:
    return {
        "name": result.tool_name if result else tool.name if tool else "",
        "display_name": tool.display_name if tool else None,
        "description": tool.description if tool else None,
        "success": result.success if result else None,
    }


def _normalize_memory_updates(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items = raw.get("memories")
    if not isinstance(items, list):
        return []

    updates: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind not in ALLOWED_MEMORY_KINDS:
            continue
        content = str(item.get("content") or "").strip()
        operation = str(item.get("operation") or "upsert").strip().lower()
        if operation not in {"upsert", "delete"}:
            operation = "upsert"
        if operation == "upsert" and not content:
            continue
        key = _normalize_memory_key(item.get("key"), kind, content)
        updates.append(
            {
                "operation": operation,
                "kind": kind,
                "key": key,
                "content": content,
                "importance": _normalize_importance(item.get("importance")),
                "reason": str(item.get("reason") or "").strip()[:300],
            }
        )
    return updates


def _normalize_summary(raw: dict[str, Any]) -> str:
    value = raw.get("updated_summary") or raw.get("summary")
    if not isinstance(value, str):
        return ""
    return value.strip()[:1800]


def _normalize_memory_key(value: Any, kind: str, content: str) -> str:
    if isinstance(value, str):
        normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
        if normalized:
            return normalized[:80]
    if kind == "profile" and content.startswith("用户姓名/称呼："):
        return PROFILE_NAME_KEY
    digest = hashlib.md5(f"{kind}:{content}".encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"{kind}_{digest}"


def _normalize_importance(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.7
    return min(max(number, 0.0), 1.0)


def _memory_matches_key(record: MemoryRecord, key: str) -> bool:
    metadata = record.metadata_json or {}
    if metadata.get("key") == key:
        return True
    return key == PROFILE_NAME_KEY and record.kind == "profile" and record.content.startswith("用户姓名/称呼：")


def _read_dedupe_key(record: MemoryRecord) -> str:
    metadata = record.metadata_json or {}
    key = metadata.get("key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    if record.kind == "profile" and record.content.startswith("用户姓名/称呼："):
        return PROFILE_NAME_KEY
    if record.kind == "summary":
        return "summary"
    return record.id


def _is_legacy_transcript_summary(record: MemoryRecord) -> bool:
    if record.kind != "summary":
        return False
    metadata = record.metadata_json or {}
    if metadata.get("source") == MEMORY_SOURCE:
        return False
    return "用户本轮诉求：" in record.content or "最近处理结果：" in record.content


def _recent_messages_with_reply(recent_messages: list[dict[str, str]], reply: str) -> list[dict[str, str]]:
    messages = [message for message in recent_messages if message.get("content")]
    if reply.strip():
        messages.append({"role": "assistant", "content": reply.strip()})
    return messages[-12:]


def _terms(text: str) -> set[str]:
    words = set(re.findall(r"[A-Za-z0-9_]{2,}", text.lower()))
    words.update(char for char in text if "\u4e00" <= char <= "\u9fff")
    return words


def _score(content: str, query_terms: set[str]) -> int:
    if not query_terms:
        return 1
    lower = content.lower()
    return sum(1 for term in query_terms if term in lower)
