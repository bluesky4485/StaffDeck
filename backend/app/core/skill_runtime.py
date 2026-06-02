from __future__ import annotations

from app.db.models import ChatSession, utc_now
from app.session.session_schema import RouterDecision


class SkillRuntime:
    def apply_decision(self, session: ChatSession, decision: RouterDecision) -> ChatSession:
        self._append_pending_tasks(session, decision)
        current_frame = {
            "skill_id": session.active_skill_id,
            "step_id": session.active_step_id,
            "slots": session.slots_json or {},
            "summary": session.summary,
            "last_agent_question": session.last_agent_question,
        }
        current_frame_with_hints = _frame_with_slot_hints(current_frame, decision.slot_hints)

        if decision.decision == "start_skill":
            session.skill_stack_json = _without_skill(session.skill_stack_json, decision.target_skill_id)
            session.active_skill_id = decision.target_skill_id
            session.active_step_id = decision.target_step_id
            session.slots_json = dict(decision.slot_hints or {})
            session.resume_after_answer_json = None

        elif decision.decision in {"continue_current_skill", "jump_within_current_skill"}:
            if decision.target_step_id:
                session.active_step_id = decision.target_step_id

        elif decision.decision in {
            "answer_related_question_then_resume",
            "answer_chitchat_then_resume",
        }:
            if session.active_skill_id and session.active_step_id:
                session.resume_after_answer_json = current_frame_with_hints
            current_skill_id = current_frame["skill_id"]
            if (
                decision.target_skill_id
                and current_skill_id
                and decision.target_skill_id != current_skill_id
            ):
                target_frame, stack = _pop_last_skill_frame(
                    session.skill_stack_json, decision.target_skill_id
                )
                stack = _without_skill(stack, str(current_skill_id))
                stack.append(current_frame_with_hints)
                session.skill_stack_json = stack
                if target_frame:
                    session.active_skill_id = target_frame.get("skill_id")
                    session.active_step_id = target_frame.get("step_id") or decision.target_step_id
                    session.slots_json = target_frame.get("slots") or {}
                    session.summary = target_frame.get("summary")
                    session.last_agent_question = target_frame.get("last_agent_question")
                else:
                    session.active_skill_id = decision.target_skill_id
                    session.active_step_id = decision.target_step_id
                    session.slots_json = dict(decision.slot_hints or {})
            else:
                if decision.target_skill_id:
                    session.active_skill_id = decision.target_skill_id
                if decision.target_step_id:
                    session.active_step_id = decision.target_step_id

        elif decision.decision == "suspend_current_and_start_new_skill":
            target_frame, stack = _pop_last_skill_frame(
                session.skill_stack_json, decision.target_skill_id
            )
            current_skill_id = current_frame["skill_id"]
            if current_skill_id and current_skill_id != decision.target_skill_id:
                stack = _without_skill(stack, str(current_skill_id))
                stack.append(current_frame)
            session.skill_stack_json = stack
            if target_frame:
                session.active_skill_id = target_frame.get("skill_id")
                session.active_step_id = target_frame.get("step_id") or decision.target_step_id
                session.slots_json = target_frame.get("slots") or {}
                session.summary = target_frame.get("summary")
                session.last_agent_question = target_frame.get("last_agent_question")
            else:
                session.active_skill_id = decision.target_skill_id
                session.active_step_id = decision.target_step_id
                session.slots_json = dict(decision.slot_hints or {})
            session.resume_after_answer_json = None

        elif decision.decision == "exit_current_skill":
            session.skill_stack_json = _without_skill(session.skill_stack_json, session.active_skill_id)
            session.active_skill_id = None
            session.active_step_id = None
            session.slots_json = {}
            session.resume_after_answer_json = None

        elif decision.decision == "handoff_human":
            session.status = "handoff"

        if decision.slot_hints and decision.decision != "exit_current_skill":
            session.slots_json = {**(session.slots_json or {}), **dict(decision.slot_hints)}

        session.updated_at = utc_now()
        return session

    def pop_next_pending_task(self, session: ChatSession) -> RouterDecision | None:
        tasks = list(session.pending_tasks_json or [])
        while tasks:
            task = tasks.pop(0)
            target_skill_id = task.get("target_skill_id") if isinstance(task, dict) else None
            if not target_skill_id:
                continue
            session.pending_tasks_json = tasks
            return RouterDecision(
                decision=task.get("decision") or "start_skill",
                target_skill_id=target_skill_id,
                target_step_id=task.get("target_step_id"),
                confidence=float(task.get("confidence") or 0.0),
                user_intent=task.get("user_intent"),
                reason=task.get("reason"),
                source_message=task.get("source_message"),
                should_resume_after_answer=False,
                clarification_question="",
                slot_hints=task.get("slot_hints") if isinstance(task.get("slot_hints"), dict) else {},
            )
        session.pending_tasks_json = []
        return None

    def _append_pending_tasks(self, session: ChatSession, decision: RouterDecision) -> None:
        if not decision.pending_tasks:
            return
        tasks = list(session.pending_tasks_json or [])
        existing = {
            (
                str(task.get("target_skill_id") or ""),
                str(task.get("target_step_id") or ""),
                str(task.get("source_message") or ""),
                str(task.get("user_intent") or ""),
            )
            for task in tasks
            if isinstance(task, dict)
        }
        for task in decision.pending_tasks:
            task_json = task.model_dump(mode="json")
            key = (
                str(task_json.get("target_skill_id") or ""),
                str(task_json.get("target_step_id") or ""),
                str(task_json.get("source_message") or ""),
                str(task_json.get("user_intent") or ""),
            )
            if not key[0] or key in existing:
                continue
            tasks.append(task_json)
            existing.add(key)
        session.pending_tasks_json = tasks

    def complete_current_skill(self, session: ChatSession) -> ChatSession:
        session.skill_stack_json = _without_skill(session.skill_stack_json, session.active_skill_id)
        session.active_skill_id = None
        session.active_step_id = None
        session.slots_json = {}
        session.resume_after_answer_json = None
        session.updated_at = utc_now()
        return session

    def finish_interrupt_response(self, session: ChatSession) -> ChatSession:
        resume = session.resume_after_answer_json
        if resume:
            session.active_skill_id = resume.get("skill_id")
            session.active_step_id = resume.get("step_id")
            session.slots_json = resume.get("slots") or {}
            session.summary = resume.get("summary")
            session.last_agent_question = resume.get("last_agent_question")
            session.skill_stack_json = _without_skill(session.skill_stack_json, session.active_skill_id)
            session.resume_after_answer_json = None
            session.updated_at = utc_now()
        return session


def _pop_last_skill_frame(
    stack_json: list[dict] | None,
    skill_id: str | None,
) -> tuple[dict | None, list[dict]]:
    stack = list(stack_json or [])
    if not skill_id:
        return None, stack
    for index in range(len(stack) - 1, -1, -1):
        if stack[index].get("skill_id") == skill_id:
            frame = stack.pop(index)
            return frame, _without_skill(stack, skill_id)
    return None, stack


def _without_skill(stack_json: list[dict] | None, skill_id: str | None) -> list[dict]:
    if not skill_id:
        return list(stack_json or [])
    return [frame for frame in list(stack_json or []) if frame.get("skill_id") != skill_id]


def _frame_with_slot_hints(frame: dict, slot_hints: dict | None) -> dict:
    if not slot_hints:
        return frame
    next_frame = dict(frame)
    next_frame["slots"] = {**(next_frame.get("slots") or {}), **dict(slot_hints)}
    return next_frame
