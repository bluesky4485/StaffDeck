import {
  BranchesOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  CloudSyncOutlined,
  DeleteOutlined,
  DislikeOutlined,
  DownOutlined,
  EditOutlined,
  FileSearchOutlined,
  LikeOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PlusOutlined,
  RightOutlined,
  SendOutlined,
  StopOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { Button, Dropdown, Empty, Input, Modal, Select, Typography, message } from 'antd';
import type { MouseEvent, ReactNode } from 'react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { SHOW_DEBUG, TENANT_ID, api, clearAuthSession, getAuthSession, isAuthError, streamChatTurn } from '../api/client';
import CodeBlock from '../components/CodeBlock';
import EmployeeAvatarMark from '../components/EmployeeAvatarMark';
import { employeeDisplayName, employeeProfile, isEmployeeOwnedBy, isGalleryEmployee, visibleChatEmployees } from '../employee';
import { ThemeToggleButton } from '../theme';
import type {
  AgentProfileRead,
  ChatMessage,
  ChatSession,
  ChatTurnResponse,
  KnowledgeCitation,
  ModelConfigRead,
  ScheduledTaskDraftRead,
  ScheduledTaskRead,
  TurnTraceRead,
  UIConfigRead,
} from '../types';

type SessionSlot = {
  serverMessages: ChatMessage[];
  realtimeMessages: ChatMessage[];
};

type StreamSlot = {
  loading: boolean;
  phase: string;
  timer: number | null;
  accumulated: string;
  turnId: string | null;
  abortController: AbortController | null;
};

type TraceSkill = {
  skillId: string;
  name?: string;
  stepId?: string;
  state?: string;
};

type TraceTool = {
  toolId: string;
  toolCallId?: string;
  toolName: string;
  rawToolName?: string;
  success?: boolean;
  isError?: boolean;
  content?: unknown;
};

type TraceLine = {
  id: string;
  kind: 'thinking' | 'decision' | 'skill' | 'tool' | 'code' | 'knowledge';
  text: string;
  detail?: string;
  code?: string;
  language?: string;
  output?: string;
  outputLanguage?: string;
  outputTitle?: string;
  state: 'running' | 'completed' | 'failed';
  collapsible?: boolean;
};

type TurnTrace = {
  lines: TraceLine[];
  startedAt: number;
  completedAt?: number;
};

type ComposerIntent = 'normal' | 'scheduled_task';
const MODEL_CONFIG_STORAGE_PREFIX = 'skill_agent_selected_model_config';

function modelStorageKey(tenantId: string): string {
  return `${MODEL_CONFIG_STORAGE_PREFIX}:${tenantId}`;
}

function modelDisplayName(model: ModelConfigRead): string {
  return (model.name || model.model || '模型').trim();
}

function modelDetailText(model: ModelConfigRead): string {
  const detail = model.model && model.model !== model.name ? model.model : model.provider;
  return model.is_default ? `${detail} · 默认` : detail;
}

function createEmptySlot(): SessionSlot {
  return { serverMessages: [], realtimeMessages: [] };
}

function createStreamSlot(): StreamSlot {
  return { loading: false, phase: '', timer: null, accumulated: '', turnId: null, abortController: null };
}

function createTurnTrace(): TurnTrace {
  return { lines: [], startedAt: Date.now() };
}

function normalizeMessageText(value?: string): string {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
}

function renderInlineMarkdown(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(`[^`]*`|\*\*[^*]+?\*\*|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g;
  let cursor = 0;
  let index = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }
    const token = match[0];
    const key = `${keyPrefix}-inline-${index}`;
    if (token.startsWith('`') && token.endsWith('`')) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith('**') && token.endsWith('**')) {
      nodes.push(<strong key={key}>{renderInlineMarkdown(token.slice(2, -2), key)}</strong>);
    } else {
      const link = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
      if (link) {
        nodes.push(
          <a key={key} href={link[2]} target="_blank" rel="noreferrer">
            {link[1]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    }
    cursor = match.index + token.length;
    index += 1;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

function renderInlineLines(lines: string[], keyPrefix: string): ReactNode[] {
  return lines.flatMap((line, lineIndex) => {
    const nodes = renderInlineMarkdown(line, `${keyPrefix}-line-${lineIndex}`);
    if (lineIndex === 0) return nodes;
    return [<br key={`${keyPrefix}-br-${lineIndex}`} />, ...nodes];
  });
}

function isBlockBoundary(line: string): boolean {
  const trimmed = line.trim();
  return (
    trimmed.startsWith('```') ||
    /^#{1,6}\s+/.test(trimmed) ||
    /^[-*]\s+/.test(trimmed) ||
    /^\d+[.)]\s+/.test(trimmed)
  );
}

function renderMarkdownBlocks(content: string): ReactNode[] {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;
  let blockIndex = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();
    const key = `md-${blockIndex}`;
    if (!trimmed) {
      index += 1;
      continue;
    }

    if (trimmed.startsWith('```')) {
      const language = trimmed.slice(3).trim();
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(
        <CodeBlock key={key} className="md-code-block" code={codeLines.join('\n')} language={language || undefined} />,
      );
      blockIndex += 1;
      continue;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length, 4) as 1 | 2 | 3 | 4;
      const Tag = `h${level}` as keyof JSX.IntrinsicElements;
      blocks.push(<Tag key={key}>{renderInlineMarkdown(heading[2], key)}</Tag>);
      index += 1;
      blockIndex += 1;
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^[-*]\s+/, ''));
        index += 1;
      }
      blocks.push(
        <ul key={key}>
          {items.map((item, itemIndex) => (
            <li key={`${key}-${itemIndex}`}>{renderInlineMarkdown(item, `${key}-${itemIndex}`)}</li>
          ))}
        </ul>,
      );
      blockIndex += 1;
      continue;
    }

    if (/^\d+[.)]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length && /^\d+[.)]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^\d+[.)]\s+/, ''));
        index += 1;
      }
      blocks.push(
        <ol key={key}>
          {items.map((item, itemIndex) => (
            <li key={`${key}-${itemIndex}`}>{renderInlineMarkdown(item, `${key}-${itemIndex}`)}</li>
          ))}
        </ol>,
      );
      blockIndex += 1;
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && lines[index].trim() && !isBlockBoundary(lines[index])) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(<p key={key}>{renderInlineLines(paragraphLines, key)}</p>);
    blockIndex += 1;
  }

  return blocks;
}

function MarkdownMessage({ content }: { content: string }) {
  return <div className="assistant-answer markdown-message">{renderMarkdownBlocks(content)}</div>;
}

function TerminalTraceIcon() {
  return (
    <svg className="trace-terminal-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="3.5" y="5" width="17" height="14" rx="2.8" />
      <path d="M7.5 9.5L10.2 12l-2.7 2.5" />
      <path d="M12.4 14.5h4.2" />
    </svg>
  );
}

function SessionChatIcon() {
  return (
    <svg className="session-chat-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 4.2c-4.7 0-8.1 3.05-8.1 7.25 0 2.32 1.02 4.32 2.75 5.65l-.55 2.65 3.05-1.45c.9.26 1.9.4 2.95.4 4.7 0 8.1-3.05 8.1-7.25S16.7 4.2 12 4.2Z" />
      <path d="M8.7 11.45h.04M12 11.45h.04M15.3 11.45h.04" />
    </svg>
  );
}

function parseMessageTime(value?: string): number {
  if (!value) return 0;
  const normalized = /(?:z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const time = Date.parse(normalized);
  return Number.isFinite(time) ? time : 0;
}

function hasEquivalentServerMessage(messageItem: ChatMessage, serverMessages: ChatMessage[]): boolean {
  const content = normalizeMessageText(messageItem.content);
  if (!content) return false;
  return serverMessages.some((serverMessage) => {
    if (serverMessage.role !== messageItem.role) return false;
    if (normalizeMessageText(serverMessage.content) !== content) return false;
    if (messageItem.turnId || serverMessage.turnId) {
      return Boolean(messageItem.turnId && serverMessage.turnId && messageItem.turnId === serverMessage.turnId);
    }
    return true;
  });
}

function hasServerMessageForTurn(messageItem: ChatMessage, serverMessages: ChatMessage[]): boolean {
  if (!messageItem.turnId) return false;
  return serverMessages.some(
    (serverMessage) => serverMessage.turnId === messageItem.turnId && serverMessage.role === messageItem.role,
  );
}

function attachTurnIdsToServerMessages(
  serverMessages: ChatMessage[],
  realtimeMessages: ChatMessage[],
  previousMessages: ChatMessage[] = [],
): ChatMessage[] {
  const pendingTurns = realtimeMessages.filter((item) => item.turnId && item.role === 'user').reverse();
  const realtimeTurnIds = new Set(
    realtimeMessages
      .map((item) => item.turnId)
      .filter((turnId): turnId is string => Boolean(turnId)),
  );
  const pendingTurnIdsByServerId = new Map<string, string>();
  const matchedServerMessageIds = new Set<string>();
  const previousTurnIds = new Map(
    previousMessages
      .filter((item) => item.turnId && (item.turnId === item.id || realtimeTurnIds.has(item.turnId)))
      .map((item) => [item.id, item.turnId as string]),
  );

  pendingTurns.forEach((pendingTurn) => {
    const pendingContent = normalizeMessageText(pendingTurn.content);
    if (!pendingTurn.turnId || !pendingContent) return;
    for (let index = serverMessages.length - 1; index >= 0; index -= 1) {
      const serverMessage = serverMessages[index];
      if (serverMessage.role !== 'user' || matchedServerMessageIds.has(serverMessage.id)) continue;
      if (normalizeMessageText(serverMessage.content) !== pendingContent) continue;
      pendingTurnIdsByServerId.set(serverMessage.id, pendingTurn.turnId);
      matchedServerMessageIds.add(serverMessage.id);
      return;
    }
  });

  let activeTurnId: string | undefined;

  return serverMessages.map((messageItem) => {
    const previousTurnId = previousTurnIds.get(messageItem.id);
    if (messageItem.role === 'user') {
      activeTurnId = pendingTurnIdsByServerId.get(messageItem.id) || previousTurnId || messageItem.turnId || messageItem.id;
      return { ...messageItem, turnId: activeTurnId };
    }
    if (messageItem.role === 'assistant' && activeTurnId) {
      return { ...messageItem, turnId: previousTurnId || messageItem.turnId || activeTurnId };
    }
    return previousTurnId ? { ...messageItem, turnId: previousTurnId } : messageItem;
  });
}

function shouldKeepRealtimeMessage(
  messageItem: ChatMessage,
  serverMessages: ChatMessage[],
  latestServerTime: number,
  activeTurnId?: string | null,
): boolean {
  if (messageItem.isStreaming) {
    return !messageItem.turnId || !activeTurnId || messageItem.turnId === activeTurnId;
  }
  if (hasEquivalentServerMessage(messageItem, serverMessages)) return false;
  if (hasServerMessageForTurn(messageItem, serverMessages)) return false;
  if (messageItem.turnId && activeTurnId && messageItem.turnId === activeTurnId) return true;
  if (!latestServerTime) return true;
  return parseMessageTime(messageItem.created_at) > latestServerTime;
}

function computeMergedMessages(slot: SessionSlot, activeTurnId?: string | null): ChatMessage[] {
  const serverIds = new Set(slot.serverMessages.map((item) => item.id));
  const latestServerTime = Math.max(0, ...slot.serverMessages.map((item) => parseMessageTime(item.created_at)));
  const extras = slot.realtimeMessages.filter((item) => {
    if (serverIds.has(item.id)) return false;
    return shouldKeepRealtimeMessage(item, slot.serverMessages, latestServerTime, activeTurnId);
  });
  const combined = [
    ...slot.serverMessages.map((messageItem, index) => ({ messageItem, index })),
    ...extras.map((messageItem, index) => ({ messageItem, index: slot.serverMessages.length + index })),
  ];

  return combined
    .sort((left, right) => (
      parseMessageTime(left.messageItem.created_at) - parseMessageTime(right.messageItem.created_at) ||
      left.index - right.index
    ))
    .map((item) => item.messageItem);
}

function publicStreamPhase(data: Record<string, unknown>): string {
  const phase = typeof data.phase === 'string' ? data.phase : '';
  const text = typeof data.text === 'string' ? data.text : '';
  if (phase === 'error') return text || '请求失败';
  if (phase === 'scheduled_task_draft') return text || '生成自动任务草案';
  if (isKnowledgeTracePhase(phase)) return text || knowledgeTraceText(data);
  return '正在思考';
}

const KNOWLEDGE_TRACE_PHASES = new Set([
  'knowledge',
  'okf_route',
  'okf_only',
  'document_route',
  'document_route_fallback',
  'bucket_route',
  'bucket_route_fallback',
  'section_expand',
  'read_chunks',
  'evidence_pack',
  'no_visible_knowledge',
  'no_documents',
  'no_buckets',
]);

function isKnowledgeTracePhase(phase: string): boolean {
  return KNOWLEDGE_TRACE_PHASES.has(phase);
}

function knowledgeTraceText(data: Record<string, unknown>): string {
  const raw = typeof data.message === 'string'
    ? data.message
    : typeof data.text === 'string'
      ? data.text
      : '';
  if (!raw) return '查询业务资料';
  return raw.replace(/知识/g, '业务资料');
}

function knowledgeTraceDetail(data: Record<string, unknown>): string | undefined {
  const query = isPlainRecord(data.query) && typeof data.query.query === 'string' ? data.query.query : '';
  const parts = [
    query ? `查询：${query}` : '',
    typeof data.selected_count === 'number' ? `命中 Wiki ${data.selected_count} 个` : '',
    typeof data.candidate_count === 'number' ? `候选 ${data.candidate_count} 个` : '',
    typeof data.chunk_count === 'number' ? `读取 ${data.chunk_count} 个片段` : '',
    typeof data.evidence_count === 'number' ? `整理 ${data.evidence_count} 条证据` : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : undefined;
}

function knowledgeResultTraceDetail(data: Record<string, unknown>): string | undefined {
  const concepts = Array.isArray(data.selected_concepts) ? data.selected_concepts.length : 0;
  const chunks = Array.isArray(data.chunks) ? data.chunks.length : 0;
  const evidence = Array.isArray(data.evidence_pack) ? data.evidence_pack.length : 0;
  const parts = [
    concepts ? `命中 Wiki ${concepts} 个` : '',
    chunks ? `读取 ${chunks} 个片段` : '',
    evidence ? `生成 ${evidence} 条引用候选` : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : undefined;
}

function normalizeTraceSkill(value: unknown): TraceSkill | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const skillId = typeof item.skillId === 'string' ? item.skillId : '';
  if (!skillId) return null;
  return {
    skillId,
    name: typeof item.name === 'string' ? item.name : skillId,
    stepId: typeof item.stepId === 'string' ? item.stepId : undefined,
    state: typeof item.state === 'string' ? item.state : undefined,
  };
}

function streamSkillLabel(data: Record<string, unknown>, skill: TraceSkill): string {
  if (skill.state === 'suspended') return '挂起技能';
  const decision = typeof data.runtimeDecision === 'string' ? data.runtimeDecision : '';
  const fromSkillId = typeof data.fromSkillId === 'string' ? data.fromSkillId : '';
  const toSkillId = typeof data.toSkillId === 'string' ? data.toSkillId : '';
  if (decision === 'start_skill') return '选择技能';
  if (decision === 'suspend_current_and_start_new_skill') return '切换技能';
  if (
    (decision === 'answer_related_question_then_resume' || decision === 'answer_chitchat_then_resume')
    && fromSkillId
    && toSkillId
    && fromSkillId !== toSkillId
  ) return '切换技能';
  if (decision === 'exit_current_skill') return '恢复技能';
  return '推进技能';
}

function normalizeTraceTool(value: unknown): TraceTool | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const toolId = typeof item.toolId === 'string' ? item.toolId : '';
  if (!toolId) return null;
  return {
    toolId,
    toolCallId: typeof item.toolCallId === 'string' ? item.toolCallId : undefined,
    toolName: typeof item.toolName === 'string' ? item.toolName : toolId,
    rawToolName: typeof item.rawToolName === 'string' ? item.rawToolName : toolId,
    success: typeof item.success === 'boolean' ? item.success : undefined,
    isError: typeof item.isError === 'boolean' ? item.isError : undefined,
    content: item.content,
  };
}

function shortTraceValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function toolTraceDetail(tool: TraceTool): string | undefined {
  const content = tool.content && typeof tool.content === 'object' ? tool.content as Record<string, unknown> : null;
  const data = content?.data && typeof content.data === 'object' ? content.data as Record<string, unknown> : null;
  const parts = [
    tool.rawToolName && tool.rawToolName !== tool.toolName ? tool.rawToolName : '',
    shortTraceValue(data?.source),
    data?.found === false ? '未命中' : data?.found === true ? '已命中' : '',
    shortTraceValue(data?.miss_reason),
    shortTraceValue(data?.recommendation),
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : undefined;
}

function reflectionTraceDetail(data: Record<string, unknown>): string | undefined {
  const parts = [
    typeof data.reason === 'string' ? data.reason : '',
    typeof data.target_tool_name === 'string' ? `工具 ${data.target_tool_name}` : '',
    typeof data.target_skill_id === 'string' ? `技能 ${data.target_skill_id}` : '',
    typeof data.target_step_id === 'string' ? `步骤 ${data.target_step_id}` : '',
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : undefined;
}

function formatTracePayload(value: unknown): string {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function tracePayloadLanguage(value: string): string {
  if (!value.trim()) return 'text';
  try {
    JSON.parse(value);
    return 'json';
  } catch {
    return 'text';
  }
}

function generalSkillTraceDetail(data: Record<string, unknown>, phase: string): string | undefined {
  const review = isPlainRecord(data.review) ? data.review : undefined;
  if (phase.startsWith('reflection_')) {
    return [
      typeof review?.reason === 'string' ? review.reason : '',
      typeof review?.repair_hint === 'string' ? review.repair_hint : '',
    ]
      .filter(Boolean)
      .join(' · ') || undefined;
  }
  const detail = typeof data.rationale === 'string'
    ? data.rationale
    : typeof data.text === 'string'
      ? data.text
      : undefined;
  return detail?.trim() || undefined;
}

function generalSkillTraceOutput(data: Record<string, unknown>, phase: string, accumulatedText?: string): {
  output?: string;
  language?: string;
  title?: string;
} {
  if (phase === 'stdout_chunk') {
    const output = formatTracePayload(accumulatedText || data.stdout_preview || data.text);
    return output ? { output, language: tracePayloadLanguage(output), title: '查看运行输出' } : {};
  }
  if (phase === 'stderr_chunk') {
    const output = formatTracePayload(accumulatedText || data.stderr_preview || data.text);
    return output ? { output, language: tracePayloadLanguage(output), title: '查看错误输出' } : {};
  }
  if (phase === 'code_finished' || phase === 'code_timeout') {
    const result: Record<string, unknown> = {};
    if ('return_code' in data) result.return_code = data.return_code;
    if ('structured_result' in data) result.structured_result = data.structured_result;
    if (typeof data.stdout_preview === 'string' && data.stdout_preview.trim()) result.stdout = data.stdout_preview;
    if (typeof data.stderr_preview === 'string' && data.stderr_preview.trim()) result.stderr = data.stderr_preview;
    const output = Object.keys(result).length > 0
      ? formatTracePayload(result)
      : formatTracePayload(data.stdout_preview || data.stderr_preview || data.text);
    return output ? { output, language: tracePayloadLanguage(output), title: phase === 'code_timeout' ? '查看超时结果' : '查看执行结果' } : {};
  }
  if (phase.startsWith('reflection_')) {
    const result: Record<string, unknown> = {};
    if ('structured_result' in data) result.structured_result = data.structured_result;
    if ('review' in data) result.review = data.review;
    if (typeof data.stdout_preview === 'string' && data.stdout_preview.trim()) result.stdout = data.stdout_preview;
    if (typeof data.stderr_preview === 'string' && data.stderr_preview.trim()) result.stderr = data.stderr_preview;
    const output = Object.keys(result).length > 0 ? formatTracePayload(result) : '';
    return output ? { output, language: tracePayloadLanguage(output), title: '查看校验详情' } : {};
  }
  return {};
}

function traceLineAllowed(line: TraceLine, config: UIConfigRead): boolean {
  if (line.kind === 'thinking' || line.kind === 'decision') return config.show_thinking_trace;
  if (line.kind === 'code') return config.show_thinking_trace;
  if (line.kind === 'skill') return config.show_skill_trace;
  if (line.kind === 'tool') return config.show_tool_trace;
  return true;
}

function traceSummary(trace: TurnTrace, lines: TraceLine[]): { text: string; state: TraceLine['state'] } {
  if (lines.some((line) => line.state === 'running')) {
    return { text: '正在执行', state: 'running' };
  }
  if (lines.some((line) => line.state === 'failed')) {
    return { text: '执行遇到问题', state: 'failed' };
  }
  return { text: '执行记录', state: 'completed' };
}

function traceDetails(lines: TraceLine[]): TraceLine[] {
  const hiddenPlaceholders = new Set(['正在思考', '已完成思考', '正在执行', '执行记录']);
  const details = lines.filter((line) => {
    if (line.kind === 'thinking') return false;
    if (hiddenPlaceholders.has(line.text) && !line.detail && !line.code && !line.output) return false;
    return true;
  });
  return details.length > 0 ? details : lines.filter((line) => line.text !== '已完成思考' && line.text !== '执行记录');
}

function canRateMessage(item: ChatMessage): boolean {
  return (
    item.role === 'assistant'
    && !item.isStreaming
    && !item.isError
    && !item.id.startsWith('__')
    && !item.id.startsWith('text_')
    && !item.id.startsWith('error_')
  );
}

function stripTrailingCitationSummary(content: string): string {
  if (!content) return content;
  return content
    .replace(/(?:\n|\s){0,3}(?:参考资料|引用来源|资料来源)\s*[:：]\s*(?:\[\d+\]\s*)+$/u, '')
    .trimEnd();
}

function citationLabelsInContent(content: string): Set<number> {
  const labels = new Set<number>();
  content.replace(/\[(\d+)\]/g, (_match, value: string) => {
    const label = Number(value);
    if (Number.isInteger(label) && label >= 1) {
      labels.add(label);
    }
    return _match;
  });
  return labels;
}

function citationLabelNumber(citation: KnowledgeCitation, fallback: number): number {
  const labelText = citation.label || citation.id;
  const match = String(labelText || '').match(/\[(\d+)\]/);
  if (match) {
    const label = Number(match[1]);
    if (Number.isInteger(label) && label >= 1) {
      return label;
    }
  }
  return fallback;
}

function knowledgeCitations(item: ChatMessage, content: string): KnowledgeCitation[] {
  const citations = item.metadata?.knowledge_citations;
  if (!Array.isArray(citations)) return [];
  const usedLabels = citationLabelsInContent(content);
  if (usedLabels.size === 0) return [];
  const seen = new Set<string>();
  const result: KnowledgeCitation[] = [];
  citations.forEach((citation, index) => {
    if (!citation || !citation.id) return;
    const labelNumber = citationLabelNumber(citation, index + 1);
    if (!usedLabels.has(labelNumber)) return;
    const identity = (
      citation.title || citation.section_path || citation.summary || citation.excerpt || citation.source_path || citation.concept_id || citation.id
    )
      .replace(/\/\s*evidence\s*\d+/i, '')
      .split(/在第\s*\d+\s*章第\s*\d+\s*节/)[0]
      .split('。')[0];
    const key = normalizeMessageText(identity).toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    result.push({ ...citation, label: `[${labelNumber}]` });
  });
  return result.slice(0, 4).sort((a, b) => citationLabelNumber(a, 0) - citationLabelNumber(b, 0));
}

function scheduledDraftForMessage(item: ChatMessage): ScheduledTaskDraftRead | null {
  const draft = item.metadata?.scheduled_task_draft;
  if (!isPlainRecord(draft) || draft.should_create === false) return null;
  if (typeof draft.title !== 'string' || typeof draft.prompt !== 'string' || typeof draft.agent_id !== 'string') {
    return null;
  }
  return draft as unknown as ScheduledTaskDraftRead;
}

function createdScheduledTaskForMessage(item: ChatMessage): ScheduledTaskRead | undefined {
  const task = item.metadata?.scheduled_task_created;
  if (!isPlainRecord(task)) return undefined;
  if (typeof task.id !== 'string' || typeof task.title !== 'string' || typeof task.prompt !== 'string') {
    return undefined;
  }
  return task as unknown as ScheduledTaskRead;
}

function isScheduledTaskPrompt(item: ChatMessage): boolean {
  return item.role === 'user' && item.metadata?.interaction_mode === 'scheduled_task';
}

function ScheduledDraftCard({
  draft,
  createdTask,
  onConfirm,
  onDismiss,
}: {
  draft: ScheduledTaskDraftRead;
  createdTask?: ScheduledTaskRead;
  onConfirm: (draft: ScheduledTaskDraftRead) => void;
  onDismiss: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editableDraft, setEditableDraft] = useState<ScheduledTaskDraftRead>(draft);
  const created = Boolean(createdTask);
  const displayDraft = createdTask
    ? ({
      ...draft,
      title: createdTask.title,
      prompt: createdTask.prompt,
      description: createdTask.description || draft.description,
      schedule_type: createdTask.schedule_type,
      schedule: createdTask.schedule,
      timezone: createdTask.timezone,
      rrule: createdTask.rrule || draft.rrule,
    } as ScheduledTaskDraftRead)
    : editableDraft;

  useEffect(() => {
    setEditableDraft(draft);
    setEditing(false);
  }, [
    draft.agent_id,
    draft.title,
    draft.prompt,
    draft.description,
    draft.schedule_type,
    draft.timezone,
    draft.rrule,
    JSON.stringify(draft.schedule || {}),
    createdTask?.id,
  ]);

  const updateDraft = (patch: Partial<ScheduledTaskDraftRead>) => {
    setEditableDraft((current) => ({ ...current, ...patch }));
  };
  const scheduleValue = scheduleEditValue(editableDraft);
  const validateDraft = (nextDraft: ScheduledTaskDraftRead) => {
    if (!nextDraft.title.trim()) {
      message.warning('请输入自动任务名称');
      return false;
    }
    if (!nextDraft.prompt.trim()) {
      message.warning('请输入执行内容');
      return false;
    }
    if (!scheduleEditValue(nextDraft).trim()) {
      message.warning('请输入执行计划');
      return false;
    }
    return true;
  };
  const updateScheduleType = (value: ScheduledTaskDraftRead['schedule_type']) => {
    setEditableDraft((current) => {
      const currentSchedule = current.schedule || {};
      const time = String(currentSchedule.time || '09:00');
      let schedule: Record<string, unknown>;
      if (value === 'once') {
        schedule = { run_at: String(currentSchedule.run_at || '') };
      } else if (value === 'weekly') {
        schedule = {
          time,
          weekdays: Array.isArray(currentSchedule.weekdays) ? currentSchedule.weekdays : [0],
        };
      } else if (value === 'monthly') {
        schedule = {
          time,
          day_of_month: currentSchedule.day_of_month || 1,
        };
      } else {
        schedule = { time };
      }
      return { ...current, schedule_type: value, schedule };
    });
  };
  const updateScheduleValue = (value: string) => {
    setEditableDraft((current) => ({ ...current, schedule: scheduleFromEditValue(current, value) }));
  };
  const completeEdit = () => {
    if (!validateDraft(editableDraft)) return;
    setEditing(false);
  };
  const confirmDraft = () => {
    if (created) return;
    if (!validateDraft(editableDraft)) return;
    onConfirm(editableDraft);
  };

  return (
    <div className={`scheduled-draft-card ${editing ? 'editing' : ''}${created ? ' created' : ''}`}>
      <div className="scheduled-draft-header">
        <div className="scheduled-draft-identity">
          <div className="scheduled-draft-icon">{created ? <CheckCircleOutlined /> : <ClockCircleOutlined />}</div>
          <div className="scheduled-draft-title-block">
            <div className="scheduled-draft-kicker">{created ? '定时任务已创建' : '定时任务草案'}</div>
            {editing ? (
              <Input
                size="small"
                className="scheduled-draft-title-input"
                value={editableDraft.title}
                onChange={(event) => updateDraft({ title: event.target.value })}
              />
            ) : (
              <strong>{displayDraft.title}</strong>
            )}
          </div>
        </div>
        <div className="scheduled-draft-top-actions">
          {created ? (
            <span className="scheduled-draft-created-badge">
              <CheckCircleOutlined />
              已创建
            </span>
          ) : editing ? (
            <>
              <Button size="small" type="primary" onClick={completeEdit}>完成</Button>
              <Button size="small" type="text" onClick={() => { setEditableDraft(draft); setEditing(false); }}>取消</Button>
            </>
          ) : (
            <>
              <Button size="small" type="text" icon={<EditOutlined />} onClick={() => setEditing(true)}>编辑</Button>
              <Button size="small" type="text" onClick={onDismiss}>忽略</Button>
            </>
          )}
        </div>
      </div>
      {editing ? (
        <div className="scheduled-draft-editor">
          <label>
            <span>计划类型</span>
            <Select
              size="small"
              value={editableDraft.schedule_type}
              onChange={updateScheduleType}
              options={[
                { value: 'once', label: '一次性' },
                { value: 'daily', label: '每天' },
                { value: 'weekly', label: '每周' },
                { value: 'monthly', label: '每月' },
              ]}
            />
          </label>
          <label>
            <span>执行计划</span>
            <Input
              size="small"
              value={scheduleValue}
              placeholder={editableDraft.schedule_type === 'once' ? 'YYYY-MM-DDTHH:mm:ss+08:00' : 'HH:mm'}
              onChange={(event) => updateScheduleValue(event.target.value)}
            />
          </label>
          <label>
            <span>时区</span>
            <Input
              size="small"
              value={editableDraft.timezone || 'Asia/Shanghai'}
              onChange={(event) => updateDraft({ timezone: event.target.value })}
            />
          </label>
          <label className="scheduled-draft-editor-full">
            <span>执行内容</span>
            <Input.TextArea
              autoSize={{ minRows: 3, maxRows: 6 }}
              value={editableDraft.prompt}
              onChange={(event) => updateDraft({ prompt: event.target.value })}
            />
          </label>
          <label className="scheduled-draft-editor-full">
            <span>说明</span>
            <Input.TextArea
              autoSize={{ minRows: 2, maxRows: 4 }}
              value={editableDraft.description || ''}
              placeholder="可补充任务目的、范围或结果要求"
              onChange={(event) => updateDraft({ description: event.target.value })}
            />
          </label>
        </div>
      ) : (
        <div className="scheduled-draft-body">
          <div className="scheduled-draft-meta-grid">
            <div className="scheduled-draft-meta-item">
              <span>计划</span>
              <strong>{formatDraftSchedule(displayDraft)}</strong>
            </div>
            <div className="scheduled-draft-meta-item">
              <span>类型</span>
              <strong>{scheduleTypeLabel(displayDraft.schedule_type)}</strong>
            </div>
            <div className="scheduled-draft-meta-item">
              <span>时区</span>
              <strong>{displayDraft.timezone || 'Asia/Shanghai'}</strong>
            </div>
          </div>
          <div className="scheduled-draft-prompt">
            <span>执行内容</span>
            <p>{displayDraft.prompt}</p>
          </div>
          {displayDraft.description && (
            <div className="scheduled-draft-description">
              <span>说明</span>
              <p>{displayDraft.description}</p>
            </div>
          )}
        </div>
      )}
      {!created && (
        <div className="scheduled-draft-footer">
          {editing && <Button size="small" type="text" onClick={onDismiss}>忽略草案</Button>}
          <Button size="small" type="primary" onClick={confirmDraft}>确认创建</Button>
        </div>
      )}
    </div>
  );
}

function citationKindLabel(citation: KnowledgeCitation): string {
  if (citation.kind === 'concept') return 'Wiki 概念';
  if (citation.kind === 'okf') return 'OKF 引用';
  return '证据片段';
}

function citationDisplayTitle(citation: KnowledgeCitation): string {
  const raw = citation.title || citation.section_path || citation.source_path || citation.concept_id || '知识引用';
  const title = raw
    .replace(/\/\s*evidence\s*\d+/i, '')
    .split('用于统一')[0]
    .split('。服务人员')[0]
    .trim();
  return title || raw;
}

function citationSourceLabel(citation: KnowledgeCitation): string {
  const raw = citation.source_path || '';
  if (!raw) return '';
  return raw.replace(/\/\s*evidence\s*\d+/i, '').split(' / ')[0].trim() || raw;
}

function citationSectionLabel(citation: KnowledgeCitation): string {
  const raw = citation.section_path || citation.title || '';
  if (!raw) return '';
  return citationDisplayTitle({ ...citation, title: raw });
}

export default function ChatWindowPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [auth] = useState(() => getAuthSession());
  const tenantId = auth?.user.tenant_id || TENANT_ID;
  const userId = auth?.user.id || '';
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState(() => window.localStorage.getItem('skill_agent_selected_agent') || '');
  const [modelConfigs, setModelConfigs] = useState<ModelConfigRead[]>([]);
  const [selectedModelConfigId, setSelectedModelConfigId] = useState(
    () => window.localStorage.getItem(modelStorageKey(tenantId)) || '',
  );
  const [newSessionOpen, setNewSessionOpen] = useState(false);
  const [newSessionAgentId, setNewSessionAgentId] = useState('');
  const [input, setInput] = useState('');
  const [composerIntent, setComposerIntent] = useState<ComposerIntent>('normal');
  const [lastTurn, setLastTurn] = useState<ChatTurnResponse | null>(null);
  const [renameSession, setRenameSession] = useState<ChatSession | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [storeTick, setStoreTick] = useState(0);
  const [streamTick, setStreamTick] = useState(0);
  const [traceTick, setTraceTick] = useState(0);
  const [expandedTraceIds, setExpandedTraceIds] = useState<string[]>([]);
  const [scheduledDrafts, setScheduledDrafts] = useState<Record<string, ScheduledTaskDraftRead>>({});
  const [createdScheduledTasks, setCreatedScheduledTasks] = useState<Record<string, ScheduledTaskRead>>({});
  const [dismissedDraftMessageIds, setDismissedDraftMessageIds] = useState<string[]>([]);
  const [activeCitation, setActiveCitation] = useState<KnowledgeCitation | null>(null);
  const [isComposing, setIsComposing] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => (
    window.localStorage.getItem('skill_agent_sidebar_collapsed') === 'true'
  ));
  const [uiConfig, setUiConfig] = useState<UIConfigRead>({
    tenant_id: tenantId,
    show_thinking_trace: true,
    show_skill_trace: true,
    show_tool_trace: true,
    reflection_max_rounds: 1,
    agent_loop_max_actions: 6,
    updated_at: '',
  });
  const chatMessagesRef = useRef<HTMLDivElement>(null);
  const storeRef = useRef(new Map<string, SessionSlot>());
  const streamRef = useRef(new Map<string, StreamSlot>());
  const turnTraceRef = useRef(new Map<string, TurnTrace>());

  const notifyStore = useCallback(() => setStoreTick((value) => value + 1), []);
  const notifyStream = useCallback(() => setStreamTick((value) => value + 1), []);
  const notifyTrace = useCallback(() => setTraceTick((value) => value + 1), []);
  const scrollChatToBottom = useCallback(() => {
    const element = chatMessagesRef.current;
    if (!element) return;
    window.requestAnimationFrame(() => {
      element.scrollTop = element.scrollHeight;
      window.requestAnimationFrame(() => {
        element.scrollTop = element.scrollHeight;
      });
    });
  }, []);

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem('skill_agent_sidebar_collapsed', String(next));
      return next;
    });
  }, []);

  const currentSession = sessions.find((item) => item.id === sessionId) || null;
  const availableAgents = visibleChatEmployees(agents, auth?.user);
  const personalAgents = availableAgents.filter((agent) => !isGalleryEmployee(agent) || isEmployeeOwnedBy(agent, auth?.user));
  const personalAgentIds = new Set(personalAgents.map((agent) => agent.id));
  const galleryAgents = availableAgents.filter((agent) => isGalleryEmployee(agent) && !personalAgentIds.has(agent.id));
  const defaultAgent = availableAgents.find((agent) => agent.id === selectedAgentId) || availableAgents[0] || null;
  const sessionAgent = currentSession?.agent_id
    ? agents.find((agent) => agent.id === currentSession.agent_id) || null
    : null;
  const displayedAgent = sessionAgent || defaultAgent;
  const displayedProfile = displayedAgent ? employeeProfile(displayedAgent) : null;
  const enabledModelConfigs = useMemo(() => modelConfigs.filter((item) => item.enabled), [modelConfigs]);
  const selectedModelConfig = (
    enabledModelConfigs.find((item) => item.id === selectedModelConfigId)
    || enabledModelConfigs.find((item) => item.is_default)
    || enabledModelConfigs[0]
    || null
  );

  const changeAgent = useCallback((value: string) => {
    setSelectedAgentId(value);
    window.localStorage.setItem('skill_agent_selected_agent', value);
  }, []);

  const changeModelConfig = useCallback((value: string) => {
    setSelectedModelConfigId(value);
    if (value) {
      window.localStorage.setItem(modelStorageKey(tenantId), value);
    } else {
      window.localStorage.removeItem(modelStorageKey(tenantId));
    }
  }, [tenantId]);

  useEffect(() => {
    api
      .get<AgentProfileRead[]>(`/api/chat/agents?tenant_id=${tenantId}`)
          .then((rows) => {
        setAgents(rows);
        setSelectedAgentId((current) => {
          const employeeRows = visibleChatEmployees(rows, auth?.user);
          if (current && employeeRows.some((item) => item.id === current)) return current;
          const next = employeeRows[0]?.id || '';
          if (next) window.localStorage.setItem('skill_agent_selected_agent', next);
          return next;
        });
        setNewSessionAgentId((current) => (
          current && visibleChatEmployees(rows, auth?.user).some((item) => item.id === current)
            ? current
            : (visibleChatEmployees(rows, auth?.user).find((item) => item.id === selectedAgentId)?.id || visibleChatEmployees(rows, auth?.user)[0]?.id || '')
        ));
      })
      .catch(() => setAgents([]));
  }, [auth?.user, selectedAgentId, tenantId]);

  useEffect(() => {
    if (!auth) return;
    api
      .get<ModelConfigRead[]>(`/api/enterprise/model-configs?tenant_id=${tenantId}`)
      .then((rows) => {
        setModelConfigs(rows);
        setSelectedModelConfigId((current) => {
          const enabledRows = rows.filter((item) => item.enabled);
          const stored = window.localStorage.getItem(modelStorageKey(tenantId)) || '';
          if (current && enabledRows.some((item) => item.id === current)) return current;
          if (stored && enabledRows.some((item) => item.id === stored)) return stored;
          const next = enabledRows.find((item) => item.is_default)?.id || enabledRows[0]?.id || '';
          if (next) {
            window.localStorage.setItem(modelStorageKey(tenantId), next);
          }
          return next;
        });
      })
      .catch((error) => {
        if (isAuthError(error)) {
          clearAuthSession();
          navigate('/login', { replace: true });
          return;
        }
        setModelConfigs([]);
      });
  }, [auth, navigate, tenantId]);
  const toggleTrace = useCallback((turnId: string) => {
    setExpandedTraceIds((current) => (
      current.includes(turnId)
        ? current.filter((item) => item !== turnId)
        : [...current, turnId]
    ));
  }, []);

  const getSlot = useCallback((id: string): SessionSlot => {
    const store = storeRef.current;
    if (!store.has(id)) {
      store.set(id, createEmptySlot());
    }
    return store.get(id)!;
  }, []);

  const getStreamSlot = useCallback((id: string): StreamSlot => {
    const store = streamRef.current;
    if (!store.has(id)) {
      store.set(id, createStreamSlot());
    }
    return store.get(id)!;
  }, []);

  const getTurnTrace = useCallback((id: string): TurnTrace => {
    const store = turnTraceRef.current;
    if (!store.has(id)) {
      store.set(id, createTurnTrace());
    }
    return store.get(id)!;
  }, []);

  const upsertTraceLine = useCallback((turnId: string, line: TraceLine) => {
    const trace = getTurnTrace(turnId);
    const index = trace.lines.findIndex((item) => item.id === line.id);
    if (index >= 0) {
      trace.lines = [...trace.lines];
      trace.lines[index] = line;
    } else {
      trace.lines = [...trace.lines, line].slice(-80);
    }
    notifyTrace();
  }, [getTurnTrace, notifyTrace]);

  const finishTrace = useCallback((turnId: string, failed = false) => {
    const trace = getTurnTrace(turnId);
    trace.completedAt = Date.now();
    trace.lines = trace.lines.map((line) => ({
      ...line,
      state: failed && line.state === 'running' ? 'failed' : line.state === 'running' ? 'completed' : line.state,
    }));
    notifyTrace();
  }, [getTurnTrace, notifyTrace]);

  const pruneRealtime = useCallback((id: string) => {
    const slot = getSlot(id);
    const stream = getStreamSlot(id);
    const latestServerTime = Math.max(0, ...slot.serverMessages.map((item) => parseMessageTime(item.created_at)));
    slot.realtimeMessages = slot.realtimeMessages.filter((item) => {
      if (slot.serverMessages.some((serverMessage) => serverMessage.id === item.id)) return false;
      return shouldKeepRealtimeMessage(item, slot.serverMessages, latestServerTime, stream.turnId);
    });
  }, [getSlot, getStreamSlot]);

  const displayedMessages = useMemo(() => {
    if (!sessionId) return [];
    void storeTick;
    void streamTick;
    return computeMergedMessages(getSlot(sessionId), getStreamSlot(sessionId).turnId);
  }, [getSlot, getStreamSlot, sessionId, storeTick, streamTick]);

  const currentStream = useMemo(() => {
    void streamTick;
    return sessionId ? getStreamSlot(sessionId) : createStreamSlot();
  }, [getStreamSlot, sessionId, streamTick]);
  const modelMenuItems = useMemo(() => {
    if (!enabledModelConfigs.length) {
      return [
        {
          key: 'empty',
          disabled: true,
          label: <span className="composer-model-menu-empty">暂无可用模型</span>,
        },
      ];
    }
    return enabledModelConfigs.map((model) => ({
      key: model.id,
      label: (
        <span className="composer-model-menu-item">
          <span className="composer-model-menu-copy">
            <span className="composer-model-menu-name">{modelDisplayName(model)}</span>
            <span className="composer-model-menu-detail">{modelDetailText(model)}</span>
          </span>
          {selectedModelConfig?.id === model.id && <CheckOutlined />}
        </span>
      ),
    }));
  }, [enabledModelConfigs, selectedModelConfig?.id]);
  const currentScheduledDraft = sessionId ? scheduledDrafts[sessionId] : undefined;
  const hasVisibleMessageScheduledDraft = displayedMessages.some((item) => (
    item.role === 'assistant'
    && !dismissedDraftMessageIds.includes(item.id)
    && Boolean(scheduledDraftForMessage(item))
  ));

  const loadSessions = useCallback(() => {
    api
      .get<ChatSession[]>(`/api/chat/sessions?tenant_id=${tenantId}`)
      .then(setSessions)
      .catch((error) => {
        if (isAuthError(error)) {
          clearAuthSession();
          navigate('/login', { replace: true });
          return;
        }
        message.error(error.message);
      });
  }, [navigate, tenantId]);

  const loadMessages = useCallback((id: string) => {
    return api
      .get<ChatMessage[]>(`/api/chat/sessions/${id}/messages?tenant_id=${tenantId}`)
      .then((rows) => {
        const slot = getSlot(id);
        slot.serverMessages = attachTurnIdsToServerMessages(rows, slot.realtimeMessages, slot.serverMessages);
        pruneRealtime(id);
        notifyStore();
      })
      .catch((error) => {
        if (isAuthError(error)) {
          clearAuthSession();
          navigate('/login', { replace: true });
          return;
        }
        message.error(error instanceof Error ? error.message : '消息加载失败');
      });
  }, [getSlot, navigate, notifyStore, pruneRealtime, tenantId]);

  const loadTraces = useCallback((id: string) => {
    return api
      .get<TurnTraceRead[]>(`/api/chat/sessions/${id}/trace?tenant_id=${tenantId}`)
      .then((rows) => {
        rows.forEach((row) => {
          turnTraceRef.current.set(row.turn_id, {
            lines: row.lines.map((line) => ({
              id: line.id,
              kind: line.kind,
              text: line.text,
              detail: line.detail || undefined,
              code: line.code || undefined,
              language: line.language || undefined,
              output: line.output || undefined,
              outputLanguage: line.outputLanguage || undefined,
              outputTitle: line.outputTitle || undefined,
              state: line.state,
              collapsible: Boolean(line.collapsible || line.code || line.output),
            })),
            startedAt: Date.parse(row.started_at) || Date.now(),
            completedAt: row.completed_at ? Date.parse(row.completed_at) : undefined,
          });
        });
        notifyTrace();
      })
      .catch((error) => {
        if (isAuthError(error)) {
          clearAuthSession();
          navigate('/login', { replace: true });
          return;
        }
        message.error(error instanceof Error ? error.message : '轨迹加载失败');
      });
  }, [navigate, notifyTrace, tenantId]);

  const appendRealtime = useCallback((id: string, messageItem: ChatMessage) => {
    const slot = getSlot(id);
    slot.realtimeMessages = [...slot.realtimeMessages, messageItem].slice(-200);
    notifyStore();
  }, [getSlot, notifyStore]);

  const updateMessageFeedback = useCallback((
    id: string,
    messageId: string,
    rating: ChatMessage['feedback_rating'],
  ) => {
    const slot = getSlot(id);
    const update = (item: ChatMessage) => (
      item.id === messageId ? { ...item, feedback_rating: rating } : item
    );
    slot.serverMessages = slot.serverMessages.map(update);
    slot.realtimeMessages = slot.realtimeMessages.map(update);
    notifyStore();
  }, [getSlot, notifyStore]);

  const updateStreaming = useCallback((id: string, text: string, turnId?: string) => {
    const slot = getSlot(id);
    const stream = getStreamSlot(id);
    const streamId = `__streaming_${id}`;
    const streamingMessage: ChatMessage = {
      id: streamId,
      turnId: turnId || stream.turnId || undefined,
      role: 'assistant',
      content: text,
      created_at: new Date().toISOString(),
      isStreaming: true,
    };
    const index = slot.realtimeMessages.findIndex((item) => item.id === streamId);
    if (index >= 0) {
      slot.realtimeMessages = [...slot.realtimeMessages];
      slot.realtimeMessages[index] = streamingMessage;
    } else {
      slot.realtimeMessages = [...slot.realtimeMessages, streamingMessage];
    }
    notifyStore();
  }, [getSlot, getStreamSlot, notifyStore]);

  const flushStreaming = useCallback((id: string) => {
    const stream = getStreamSlot(id);
    if (stream.timer) {
      window.clearTimeout(stream.timer);
      stream.timer = null;
    }
    if (stream.accumulated) {
      updateStreaming(id, stream.accumulated);
    }
  }, [getStreamSlot, updateStreaming]);

  const finalizeStreaming = useCallback((id: string) => {
    flushStreaming(id);
    const slot = getSlot(id);
    const streamId = `__streaming_${id}`;
    const index = slot.realtimeMessages.findIndex((item) => item.id === streamId);
    if (index >= 0) {
      const streamMessage = slot.realtimeMessages[index];
      slot.realtimeMessages = [...slot.realtimeMessages];
      slot.realtimeMessages[index] = {
        ...streamMessage,
        id: `text_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        isStreaming: false,
      };
    }
    const stream = getStreamSlot(id);
    stream.accumulated = '';
    stream.turnId = null;
    notifyStore();
  }, [flushStreaming, getSlot, getStreamSlot, notifyStore]);

  useEffect(() => {
    if (!auth) {
      navigate('/login', { replace: true });
      return;
    }
    loadSessions();
  }, [auth, loadSessions, navigate]);

  useEffect(() => {
    if (!auth) return;
    api
      .get<UIConfigRead>(`/api/chat/ui-config?tenant_id=${tenantId}`)
      .then(setUiConfig)
      .catch(() => undefined);
  }, [auth, tenantId]);

  useEffect(() => {
    if (!sessionId) return;
    loadMessages(sessionId);
    loadTraces(sessionId);
  }, [loadMessages, loadTraces, sessionId]);

  useLayoutEffect(() => {
    scrollChatToBottom();
  }, [displayedMessages.length, scrollChatToBottom, sessionId, storeTick, traceTick]);

  useEffect(() => {
    if (!currentStream.loading) return;
    scrollChatToBottom();
  }, [currentStream.loading, currentStream.phase, scrollChatToBottom, storeTick, streamTick, traceTick]);

  useEffect(() => {
    return () => {
      streamRef.current.forEach((slot) => {
        if (slot.timer) {
          window.clearTimeout(slot.timer);
        }
        slot.abortController?.abort();
      });
    };
  }, []);

  function openCreateSession() {
    const fallbackAgentId = selectedAgentId && availableAgents.some((agent) => agent.id === selectedAgentId)
      ? selectedAgentId
      : availableAgents[0]?.id || '';
    setNewSessionAgentId(fallbackAgentId);
    setNewSessionOpen(true);
  }

  async function createSession() {
    const agentId = newSessionAgentId || selectedAgentId || availableAgents[0]?.id || '';
    if (!agentId) {
      message.warning('请先选择接单员工');
      return;
    }
    const session = await api.post<ChatSession>('/api/chat/sessions', {
      tenant_id: tenantId,
      agent_id: agentId,
    });
    changeAgent(agentId);
    setNewSessionOpen(false);
    getSlot(session.id);
    loadSessions();
    navigate(`/${session.id}`);
  }

  function openRename(event: MouseEvent<HTMLElement>, session: ChatSession) {
    event.stopPropagation();
    setRenameSession(session);
    setRenameTitle(session.title || session.id);
  }

  async function saveRename() {
    if (!renameSession) return;
    const title = renameTitle.trim();
    if (!title) {
      message.warning('请输入任务名称');
      return;
    }
    const updated = await api.put<ChatSession>(`/api/chat/sessions/${renameSession.id}`, {
      tenant_id: tenantId,
      title,
    });
    setSessions((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    setRenameSession(null);
    setRenameTitle('');
    message.success('已重命名');
  }

  function confirmDelete(event: MouseEvent<HTMLElement>, target: ChatSession) {
    event.stopPropagation();
    Modal.confirm({
      title: '删除任务记录',
      content: `确定删除「${target.title || target.id}」吗？此操作会同时删除该任务的消息记录。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        const stream = getStreamSlot(target.id);
        stream.abortController?.abort();
        streamRef.current.delete(target.id);
        storeRef.current.delete(target.id);
        await api.delete(`/api/chat/sessions/${target.id}?tenant_id=${tenantId}`);
        setSessions((items) => items.filter((item) => item.id !== target.id));
        if (target.id === sessionId) {
          navigate('/');
        }
        message.success('已删除');
      },
    });
  }

  function abortStream() {
    if (!sessionId) return;
    const stream = getStreamSlot(sessionId);
    stream.abortController?.abort();
    stream.abortController = null;
    finalizeStreaming(sessionId);
    appendRealtime(sessionId, {
      id: `local_interrupt_${Date.now()}`,
      role: 'system',
      content: '已停止本次生成。',
      created_at: new Date().toISOString(),
    });
    stream.loading = false;
    stream.phase = '';
    notifyStream();
  }

  async function rateMessage(item: ChatMessage, rating: 'up' | 'down') {
    if (!sessionId || !canRateMessage(item)) return;
    const previous = item.feedback_rating || null;
    const next = previous === rating ? null : rating;
    updateMessageFeedback(sessionId, item.id, next);
    try {
      if (next) {
        await api.post(`/api/chat/messages/${item.id}/feedback`, {
          tenant_id: tenantId,
          rating: next,
        });
      } else {
        await api.delete(`/api/chat/messages/${item.id}/feedback?tenant_id=${tenantId}`);
      }
    } catch (error) {
      updateMessageFeedback(sessionId, item.id, previous);
      if (isAuthError(error)) {
        clearAuthSession();
        navigate('/login', { replace: true });
        return;
      }
      message.error(error instanceof Error ? error.message : '反馈提交失败');
    }
  }

  async function confirmScheduledTask(draft: ScheduledTaskDraftRead, draftKey?: string) {
    if (!sessionId) return;
    try {
      const saved = await api.post<ScheduledTaskRead>('/api/chat/scheduled-tasks', {
        tenant_id: tenantId,
        agent_id: draft.agent_id,
        title: draft.title,
        prompt: draft.prompt,
        description: draft.description,
        schedule_type: draft.schedule_type,
        schedule: draft.schedule,
        timezone: draft.timezone || 'Asia/Shanghai',
        rrule: draft.rrule,
        status: 'active',
        concurrency_policy: 'forbid',
        misfire_policy: 'coalesce',
        source_session_id: draft.source_session_id || sessionId,
        metadata: {
          created_from: 'chat_confirmation',
          confidence: draft.confidence,
          reason: draft.reason,
        },
      });
      const createdKey = draftKey || `session:${sessionId}`;
      setCreatedScheduledTasks((prev) => ({ ...prev, [createdKey]: saved }));
      setScheduledDrafts((prev) => {
        const next = { ...prev };
        delete next[sessionId];
        return next;
      });
      message.success(`自动任务「${saved.title}」已启用`);
    } catch (error) {
      if (isAuthError(error)) {
        clearAuthSession();
        navigate('/login', { replace: true });
        return;
      }
      message.error(error instanceof Error ? error.message : '创建自动任务失败');
    }
  }

  function dismissScheduledTaskDraft(messageId?: string) {
    if (!sessionId) return;
    setScheduledDrafts((prev) => {
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });
    if (messageId) {
      setDismissedDraftMessageIds((prev) => (prev.includes(messageId) ? prev : [...prev, messageId]));
    }
  }

  async function send() {
    if (!input.trim() || !sessionId) return;
    const currentSessionId = sessionId;
    const activeSession = sessions.find((item) => item.id === currentSessionId);
    if (!activeSession) {
      message.warning('任务信息还在加载，请稍后再发送');
      return;
    }
    const sessionAgentId = activeSession?.agent_id || selectedAgentId || '';
    if (!sessionAgentId) {
      message.warning('该任务没有绑定接单员工，请新建任务后再发送');
      return;
    }
    const stream = getStreamSlot(currentSessionId);
    if (stream.loading) return;
    const userText = input.trim();
    const requestIntent = composerIntent;
    const turnId = `turn_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    setInput('');
    setComposerIntent('normal');
    stream.accumulated = '';
    stream.turnId = turnId;
    appendRealtime(currentSessionId, {
      id: `local_${turnId}`,
      turnId,
      role: 'user',
      content: userText,
      metadata: requestIntent === 'scheduled_task'
        ? { interaction_mode: 'scheduled_task' }
        : {},
      created_at: new Date().toISOString(),
    });
    upsertTraceLine(turnId, { id: 'thinking', kind: 'thinking', text: '正在思考', state: 'running' });
    updateStreaming(currentSessionId, '', turnId);
    stream.loading = true;
    stream.phase = '正在思考';
    notifyStream();

    const controller = new AbortController();
    stream.abortController = controller;

    try {
      await streamChatTurn({
        tenant_id: tenantId,
        session_id: currentSessionId,
        user_id: userId,
        agent_id: sessionAgentId,
        message: userText,
        channel: 'web',
        interaction_mode: requestIntent,
        model_config_id: selectedModelConfig?.id,
      }, (item) => {
        const eventSessionId = String(item.data.sessionId || currentSessionId);
        if (item.event === 'session_created') {
          return;
        }
        if (item.event === 'scheduled_task_draft') {
          const draft = item.data as unknown as ScheduledTaskDraftRead;
          if (draft.should_create) {
            setScheduledDrafts((prev) => ({ ...prev, [eventSessionId]: draft }));
          }
          return;
        }
        if (item.event === 'skill_state') {
          const skills = Array.isArray(item.data.currentSkills) ? item.data.currentSkills : [];
          skills
            .map((entry) => normalizeTraceSkill(entry))
            .filter((entry): entry is TraceSkill => Boolean(entry))
            .forEach((skill) => {
              const label = streamSkillLabel(item.data, skill);
              upsertTraceLine(turnId, {
                id: `skill_${skill.skillId}_${skill.state || 'active'}`,
                kind: 'skill',
                text: `${label} ${skill.name || skill.skillId}`,
                detail: skill.stepId ? `当前步骤 ${skill.stepId}` : undefined,
                state: skill.state === 'suspended' ? 'completed' : 'running',
              });
          });
          return;
        }
        if (item.event === 'general_skill_state') {
          const skillName = typeof item.data.skillName === 'string' ? item.data.skillName : '';
          const skillSlug = typeof item.data.skillSlug === 'string' ? item.data.skillSlug : '';
          upsertTraceLine(turnId, {
            id: `general_skill_${skillSlug || skillName || 'selected'}`,
            kind: 'skill',
            text: `选择技能 ${skillName || skillSlug || ''}`.trim(),
            detail: skillSlug || undefined,
            state: 'running',
          });
          return;
        }
        if (item.event === 'general_skill_trace') {
          const phase = typeof item.data.phase === 'string' ? item.data.phase : 'trace';
          const text = typeof item.data.message === 'string' ? item.data.message : '执行技能';
          const code = typeof item.data.code === 'string' ? item.data.code : '';
          const runtime = typeof item.data.runtime === 'string' ? item.data.runtime : '';
          const attempt = typeof item.data.attempt === 'number' || typeof item.data.attempt === 'string'
            ? String(item.data.attempt)
            : '';
          const trace = getTurnTrace(turnId);
          const sequence = trace.lines.length;
          const isOutputChunk = phase === 'stdout_chunk' || phase === 'stderr_chunk';
          const id = isOutputChunk
            ? `general_skill_trace_${phase}_${attempt || 'current'}`
            : `general_skill_trace_${phase}_${attempt || sequence}`;
          const rawDetail = generalSkillTraceDetail(item.data, phase);
          const existing = trace.lines.find((line) => line.id === id);
          const previousOutput = existing?.output || existing?.detail || '';
          const detail = isOutputChunk && previousOutput && rawDetail
            ? `${previousOutput}${rawDetail}`
            : rawDetail;
          const outputInfo = generalSkillTraceOutput(item.data, phase, detail);
          const codePhases = new Set([
            'plan_created',
            'attempt_started',
            'running_code',
            'stdout_chunk',
            'stderr_chunk',
            'code_finished',
            'code_timeout',
          ]);
          const runningPhases = new Set([
            'planning',
            'repair_planning',
            'attempt_started',
            'running_code',
            'reflection_reviewing',
            'replying',
          ]);
          upsertTraceLine(turnId, {
            id,
            kind: codePhases.has(phase) ? 'code' : 'decision',
            text,
            detail: outputInfo.output ? undefined : detail,
            code: code || undefined,
            language: code ? (runtime === 'bash' ? 'bash' : 'python') : undefined,
            output: outputInfo.output,
            outputLanguage: outputInfo.language,
            outputTitle: outputInfo.title,
            state: runningPhases.has(phase) ? 'running' : phase.includes('failed') || phase === 'code_timeout' ? 'failed' : 'completed',
            collapsible: Boolean(code || outputInfo.output),
          });
          return;
        }
        if (item.event === 'knowledge_result') {
          upsertTraceLine(turnId, {
            id: 'knowledge_lookup',
            kind: 'knowledge',
            text: '读取业务资料',
            detail: knowledgeResultTraceDetail(item.data),
            state: 'completed',
          });
          return;
        }
        if (item.event === 'tool_result') {
          const tool = normalizeTraceTool(item.data);
          if (tool) {
            upsertTraceLine(turnId, {
              id: `tool_${tool.toolCallId || tool.rawToolName || tool.toolId}`,
              kind: 'tool',
              text: `${tool.isError ? '工具调用失败' : '调用工具'} ${tool.toolName}`,
              detail: toolTraceDetail(tool),
              state: tool.isError ? 'failed' : 'completed',
            });
          }
          return;
        }
        if (item.event === 'agent_loop_continued' || item.event === 'agent_loop_completed') {
          const iteration = typeof item.data.iteration === 'number' || typeof item.data.iteration === 'string'
            ? String(item.data.iteration)
            : '1';
          const targetTool = typeof item.data.target_tool_name === 'string' ? item.data.target_tool_name : '';
          upsertTraceLine(turnId, {
            id: `decision_stepping_tool_continuation_${iteration}`,
            kind: 'decision',
            text: '重新分析执行动作',
            detail: item.event === 'agent_loop_continued'
              ? (targetTool ? `决定继续调用工具 ${targetTool}` : '决定继续调用工具')
              : '判断无需继续调用工具',
            state: 'completed',
          });
          if (item.event === 'agent_loop_completed') {
            upsertTraceLine(turnId, {
              id: 'decision_responding',
              kind: 'decision',
              text: '组织回复',
              state: 'running',
            });
          }
          return;
        }
        if (item.event === 'reflection_decision') {
          const needsRetry = item.data.needs_retry === true;
          const skipped = item.data.skipped === true;
          upsertTraceLine(turnId, {
            id: 'reflection',
            kind: 'decision',
            text: skipped ? '反思已关闭' : needsRetry ? '反思后继续尝试' : '反思通过',
            detail: reflectionTraceDetail(item.data),
            state: 'completed',
          });
          return;
        }
        if (item.event === 'status') {
          const eventStream = getStreamSlot(eventSessionId);
          const phase = typeof item.data.phase === 'string' ? item.data.phase : 'thinking';
          eventStream.phase = publicStreamPhase(item.data);
          if (phase === 'tool' && typeof item.data.tool_name === 'string') {
            const toolCallId = typeof item.data.tool_call_id === 'string' ? item.data.tool_call_id : item.data.tool_name;
            upsertTraceLine(turnId, {
              id: `tool_${toolCallId}`,
              kind: 'tool',
              text: `正在调用工具 ${item.data.tool_name}`,
              state: 'running',
            });
          } else if (phase === 'routing') {
            upsertTraceLine(turnId, { id: 'decision_router', kind: 'decision', text: '判断意图', state: 'running' });
          } else if (isKnowledgeTracePhase(phase)) {
            upsertTraceLine(turnId, {
              id: 'knowledge_lookup',
              kind: 'knowledge',
              text: knowledgeTraceText(item.data),
              detail: knowledgeTraceDetail(item.data),
              state: phase === 'evidence_pack' || phase.startsWith('no_') || phase === 'okf_only' ? 'completed' : 'running',
            });
          } else if (phase === 'stepping') {
            const repairReason = typeof item.data.repair_reason === 'string' ? item.data.repair_reason : 'main';
            const iteration = typeof item.data.iteration === 'number' || typeof item.data.iteration === 'string'
              ? `_${item.data.iteration}`
              : '';
            upsertTraceLine(turnId, {
              id: `decision_stepping_${repairReason}${iteration}`,
              kind: 'decision',
              text: repairReason === 'main' ? '分析执行动作' : '重新分析执行动作',
              state: 'running',
            });
          } else if (phase === 'reflecting') {
            upsertTraceLine(turnId, { id: 'reflection', kind: 'decision', text: '正在反思', state: 'running' });
          } else if (phase === 'responding') {
            upsertTraceLine(turnId, { id: 'decision_responding', kind: 'decision', text: '组织回复', state: 'running' });
          } else if (phase === 'scheduled_task_draft') {
            upsertTraceLine(turnId, {
              id: 'scheduled_task_draft',
              kind: 'decision',
              text: '生成自动任务草案',
              detail: '来自本条消息的定时项目，等待用户确认后启用',
              state: 'completed',
            });
          } else if (phase !== 'received') {
            upsertTraceLine(turnId, {
              id: `decision_status_${phase}`,
              kind: 'decision',
              text: eventStream.phase,
              state: 'running',
            });
          } else {
            upsertTraceLine(turnId, { id: 'thinking', kind: 'thinking', text: '正在思考', state: 'running' });
          }
          notifyStream();
          return;
        }
        if (item.event === 'stream_replace') {
          const next = typeof item.data.content === 'string' ? item.data.content : '';
          const eventStream = getStreamSlot(eventSessionId);
          if (eventStream.timer) {
            window.clearTimeout(eventStream.timer);
            eventStream.timer = null;
          }
          eventStream.accumulated = next;
          updateStreaming(eventSessionId, next);
          notifyStream();
          return;
        }
        if (item.event === 'stream_delta' || item.event === 'token') {
          const piece = typeof item.data.content === 'string' ? item.data.content : '';
          if (!piece) return;
          const eventStream = getStreamSlot(eventSessionId);
          eventStream.accumulated += piece;
          if (!eventStream.timer) {
            eventStream.timer = window.setTimeout(() => {
              eventStream.timer = null;
              updateStreaming(eventSessionId, eventStream.accumulated);
            }, 100);
          }
          return;
        }
        if (item.event === 'stream_end') {
          finishTrace(turnId);
          upsertTraceLine(turnId, { id: 'thinking', kind: 'thinking', text: '执行记录', state: 'completed' });
          finalizeStreaming(eventSessionId);
          return;
        }
        if (item.event === 'complete' || item.event === 'done') {
          const result = item.data as ChatTurnResponse;
          const userIntent = typeof result.router_decision?.user_intent === 'string' ? result.router_decision.user_intent : '';
          const decisionReason = typeof result.router_decision?.reason === 'string' ? result.router_decision.reason : '';
          if (userIntent || decisionReason) {
            upsertTraceLine(turnId, {
              id: 'decision_router',
              kind: 'decision',
              text: userIntent ? `判断意图 ${userIntent}` : '完成技能判断',
              detail: decisionReason || undefined,
              state: 'completed',
            });
          }
          finishTrace(turnId);
          upsertTraceLine(turnId, { id: 'thinking', kind: 'thinking', text: '执行记录', state: 'completed' });
          finalizeStreaming(eventSessionId);
          setLastTurn(result);
          const eventStream = getStreamSlot(eventSessionId);
          eventStream.loading = false;
          eventStream.phase = '';
          eventStream.abortController = null;
          notifyStream();
          loadSessions();
          window.setTimeout(() => {
            loadMessages(eventSessionId);
            loadTraces(eventSessionId);
          }, 250);
        }
      }, controller.signal);
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      if (isAuthError(error)) {
        clearAuthSession();
        navigate('/login', { replace: true });
        finishTrace(turnId, true);
        stream.loading = false;
        stream.phase = '';
        notifyStream();
        return;
      }
      appendRealtime(currentSessionId, {
        id: `error_${Date.now()}`,
        role: 'assistant',
        content: '发送失败，请检查后端服务是否已启动。',
        created_at: new Date().toISOString(),
        isError: true,
      });
      message.error(error instanceof Error ? error.message : '发送失败');
      finishTrace(turnId, true);
      stream.loading = false;
      stream.phase = '';
      notifyStream();
    } finally {
      if (stream.abortController === controller) {
        stream.abortController = null;
        stream.loading = false;
        stream.phase = '';
        notifyStream();
      }
    }
  }

  return (
    <div className={`chat-layout ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="session-pane">
        <div className="sidebar-head">
          <Button
            className="icon-button"
            icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            aria-label={sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
            onClick={toggleSidebar}
          />
          <div className="brand-block">
            <span className="brand-mark">UR</span>
            <div>
              <div className="brand-title">UltraRAG4</div>
              <div className="brand-subtitle">{auth?.user.display_name || auth?.user.username}</div>
            </div>
          </div>
          <div className="sidebar-actions">
            <Button className="icon-button primary" icon={<PlusOutlined />} onClick={openCreateSession} />
            <Button
              className="icon-button sidebar-logout"
              icon={<LogoutOutlined />}
              onClick={() => {
                clearAuthSession();
                navigate('/login', { replace: true });
              }}
            />
          </div>
        </div>
        <div className="session-section-label">任务记录</div>
        {sessions.map((session) => {
          const itemStream = getStreamSlot(session.id);
          const sessionTitle = session.title || session.id;
          const sessionSummary = itemStream.loading
            ? itemStream.phase || '正在思考'
            : session.summary || session.last_agent_question || '新任务';
          return (
            <div
              key={session.id}
              role="button"
              tabIndex={0}
              className={`session-card ${session.id === sessionId ? 'active' : ''}`}
              onClick={() => navigate(`/${session.id}`)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  navigate(`/${session.id}`);
                }
              }}
            >
              <div className="session-card-content">
                <div className="session-meta">
                  <div className="session-title" title={sessionTitle}>
                    <span className="session-title-icon"><SessionChatIcon /></span>
                    <span className="session-title-text">{sessionTitle}</span>
                  </div>
                  <div className="session-summary" title={sessionSummary}>
                    {sessionSummary}
                  </div>
                </div>
                <div className="session-actions">
                  <Button
                    className="session-action"
                    size="small"
                    type="text"
                    icon={<EditOutlined />}
                    aria-label="重命名任务"
                    onClick={(event) => openRename(event, session)}
                  />
                  <Button
                    className="session-action danger"
                    size="small"
                    type="text"
                    icon={<DeleteOutlined />}
                    aria-label="删除任务"
                    onClick={(event) => confirmDelete(event, session)}
                  />
                </div>
              </div>
            </div>
          );
        })}
        <div className="chat-agent-dock">
          <EmployeeAvatarMark profile={displayedProfile} fallback="UR" className="chat-agent-mark" />
          <div className="chat-agent-main">
            <span className="chat-agent-label">
              {sessionId ? '当前接单员工' : '新任务默认员工'}
            </span>
            <span className="chat-agent-name">
              {displayedAgent && displayedProfile
                ? `${employeeDisplayName(displayedAgent)} · ${displayedProfile.roleName}`
                : '暂无可用员工'}
            </span>
          </div>
        </div>
      </aside>
      <main className="chat-main">
        <div className="chat-header">
          <div>
            <Typography.Text strong>任务派发台</Typography.Text>
            <div className="header-subtitle">{sessionId}</div>
          </div>
          <div className="chat-header-actions">
            <ThemeToggleButton />
          </div>
        </div>
        <div className="chat-messages" ref={chatMessagesRef}>
          {displayedMessages.length === 0 && <div className="chat-empty-state">暂无消息，描述任务后开始</div>}
          <div className="message-stack">
            {displayedMessages.map((item) => {
              const turnId = item.turnId || item.id;
              const trace = item.role === 'assistant' ? turnTraceRef.current.get(turnId) : undefined;
              const visibleTrace = trace?.lines.filter((line) => traceLineAllowed(line, uiConfig)) || [];
              const summary = trace && visibleTrace.length > 0 ? traceSummary(trace, visibleTrace) : null;
              const details = traceDetails(visibleTrace);
              const expanded = expandedTraceIds.includes(turnId);
              const visibleContent = item.role === 'assistant'
                ? stripTrailingCitationSummary(item.content)
                : item.content;
              const citations = item.role === 'assistant' ? knowledgeCitations(item, visibleContent) : [];
              const scheduledTaskPrompt = isScheduledTaskPrompt(item);
              const scheduledDraft = item.role === 'assistant' && !dismissedDraftMessageIds.includes(item.id)
                ? scheduledDraftForMessage(item)
                : null;
              const persistedCreatedTask = item.role === 'assistant'
                ? createdScheduledTaskForMessage(item)
                : undefined;
              void traceTick;
              return (
                <div key={item.id} className="message-item">
                  <div className={`message-row ${item.role} ${item.isError ? 'error' : ''}`}>
                    <div className={`bubble ${summary ? 'has-trace' : ''}`}>
                      {summary && (
                        <div className="assistant-trace">
                          <button
                            type="button"
                            className={`turn-trace-summary ${summary.state}`}
                            onClick={() => toggleTrace(turnId)}
                          >
                            <span className="trace-icon-slot"><CloudSyncOutlined /></span>
                            <span className="trace-primary-text" data-text={summary.text}>{summary.text}</span>
                            {details.length > 0 && (
                              <span className="trace-chevron-slot">
                                {expanded ? <DownOutlined /> : <RightOutlined />}
                              </span>
                            )}
                          </button>
                          {expanded && details.length > 0 && (
                            <div className="turn-trace-details">
                              {details.map((line) => (
                                <div key={line.id} className={`turn-trace-line ${line.kind} ${line.state}`}>
                                  <span className="trace-icon-slot">
                                    {line.kind === 'skill' ? (
                                      <BranchesOutlined />
                                    ) : line.kind === 'tool' ? (
                                      <ToolOutlined />
                                    ) : line.kind === 'knowledge' ? (
                                      <FileSearchOutlined />
                                    ) : line.kind === 'code' ? (
                                      <TerminalTraceIcon />
                                    ) : (
                                      <CloudSyncOutlined />
                                    )}
                                  </span>
                                    <span className="turn-trace-content">
                                      <span className="trace-primary-text" data-text={line.text}>{line.text}</span>
                                      {line.detail && <span className="turn-trace-detail">{line.detail}</span>}
                                      {line.code && (
                                        <details className="turn-trace-code-wrap" open>
                                          <summary>查看代码</summary>
                                          <CodeBlock className="turn-trace-code" code={line.code} language={line.language || 'python'} />
                                        </details>
                                      )}
                                      {line.output && (
                                        <details className="turn-trace-code-wrap turn-trace-output-wrap" open>
                                          <summary>{line.outputTitle || '查看输出'}</summary>
                                          <CodeBlock className="turn-trace-code" code={line.output} language={line.outputLanguage || 'text'} />
                                        </details>
                                      )}
                                    </span>
                                  </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                      {visibleContent ? (
                        item.role === 'assistant' ? (
                          <MarkdownMessage content={visibleContent} />
                        ) : (
                          <div className="plain-answer">
                            {scheduledTaskPrompt && (
                              <span className="message-mode-chip">
                                <ClockCircleOutlined />
                                定时项目
                              </span>
                            )}
                            <span>{visibleContent}</span>
                          </div>
                        )
                      ) : item.role === 'assistant' && item.isStreaming && !summary ? (
                        <span className="typing-caret" />
                      ) : null}
                      {item.role === 'assistant' && citations.length > 0 && (
                        <div className="message-citations" aria-label="知识引用">
                          <div className="citation-heading">
                            <FileSearchOutlined />
                            <span>引用的业务资料</span>
                          </div>
                          <div className="citation-list">
                            {citations.map((citation) => (
                              <button
                                key={citation.id}
                                type="button"
                                className="citation-chip"
                                onClick={() => setActiveCitation(citation)}
                              >
                                <span className="citation-index">{citation.label || citation.id}</span>
                                <span className="citation-title">{citationDisplayTitle(citation)}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      {scheduledDraft && (
                        <ScheduledDraftCard
                          draft={scheduledDraft}
                          createdTask={createdScheduledTasks[item.id] || persistedCreatedTask}
                          onConfirm={(nextDraft) => void confirmScheduledTask(nextDraft, item.id)}
                          onDismiss={() => dismissScheduledTaskDraft(item.id)}
                        />
                      )}
                      {canRateMessage(item) && (
                        <div className="message-feedback">
                          <Button
                            type="text"
                            size="small"
                            className={item.feedback_rating === 'up' ? 'active' : ''}
                            icon={<LikeOutlined />}
                            aria-label="点赞"
                            onClick={() => rateMessage(item, 'up')}
                          />
                          <Button
                            type="text"
                            size="small"
                            className={item.feedback_rating === 'down' ? 'active danger' : ''}
                            icon={<DislikeOutlined />}
                            aria-label="点踩"
                            onClick={() => rateMessage(item, 'down')}
                          />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          {currentScheduledDraft && !hasVisibleMessageScheduledDraft && (
            <ScheduledDraftCard
              draft={currentScheduledDraft}
              createdTask={sessionId ? createdScheduledTasks[`session:${sessionId}`] : undefined}
              onConfirm={(nextDraft) => void confirmScheduledTask(nextDraft)}
              onDismiss={() => dismissScheduledTaskDraft()}
            />
          )}
          {SHOW_DEBUG && lastTurn && <pre className="debug-panel">{JSON.stringify(lastTurn.session_state, null, 2)}</pre>}
        </div>
        <div className="chat-input">
          <form
            className="composer-v2"
            onSubmit={(event) => {
              event.preventDefault();
              send();
            }}
          >
            <Input.TextArea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onPressEnter={(event) => {
                const nativeEvent = event.nativeEvent as KeyboardEvent & { isComposing?: boolean };
                if (!event.shiftKey && !isComposing && !nativeEvent.isComposing && nativeEvent.keyCode !== 229) {
                  event.preventDefault();
                  send();
                }
              }}
              onCompositionStart={() => setIsComposing(true)}
              onCompositionEnd={() => window.setTimeout(() => setIsComposing(false), 0)}
              autoSize={{ minRows: 2, maxRows: 8 }}
              placeholder="描述要交给员工办理的任务..."
            />
            <div className="composer-toolbar">
              <div className="composer-context-row">
                <Dropdown
                  trigger={['click']}
                  placement="topLeft"
                  menu={{
                    items: [
                      {
                        key: 'scheduled_task',
                        icon: <ClockCircleOutlined />,
                        label: '创建定时任务',
                      },
                    ],
                    onClick: ({ key }) => {
                      if (key === 'scheduled_task') setComposerIntent('scheduled_task');
                    },
                  }}
                >
                  <Button
                    type="text"
                    htmlType="button"
                    className="composer-plus-button"
                    icon={<PlusOutlined />}
                    disabled={currentStream.loading}
                    aria-label="添加对话项目"
                  />
                </Dropdown>
                {composerIntent === 'scheduled_task' && (
                  <button
                    type="button"
                    className="composer-intent-chip"
                    title="取消定时项目"
                    onClick={() => setComposerIntent('normal')}
                  >
                    <span className="composer-intent-icon" aria-hidden="true">
                      <ClockCircleOutlined />
                    </span>
                    <span>定时项目</span>
                  </button>
                )}
                <div className="composer-hint">Enter 发送 / Shift+Enter 换行</div>
              </div>
              <div className="composer-actions-row">
                <Dropdown
                  trigger={['click']}
                  placement="topRight"
                  overlayClassName="composer-model-dropdown"
                  menu={{
                    items: modelMenuItems,
                    onClick: ({ key }) => {
                      if (key !== 'empty') changeModelConfig(String(key));
                    },
                  }}
                >
                  <Button
                    type="text"
                    htmlType="button"
                    className="composer-model-button"
                    disabled={currentStream.loading || !enabledModelConfigs.length}
                  >
                    <span className="composer-model-label">
                      {selectedModelConfig ? modelDisplayName(selectedModelConfig) : '默认模型'}
                    </span>
                    <DownOutlined />
                  </Button>
                </Dropdown>
                <Button
                  type="primary"
                  htmlType={currentStream.loading ? 'button' : 'submit'}
                  icon={currentStream.loading ? <StopOutlined /> : <SendOutlined />}
                  onClick={currentStream.loading ? abortStream : undefined}
                  className={`composer-send-button${currentStream.loading ? ' stop-button' : ''}`}
                  aria-label={currentStream.loading ? '停止生成' : '发送'}
                />
              </div>
            </div>
          </form>
        </div>
      </main>
      <Modal
        className="new-session-agent-modal"
        title="选择接单员工"
        open={newSessionOpen}
        okText="创建任务"
        cancelText="取消"
        okButtonProps={{ disabled: !newSessionAgentId }}
        onOk={createSession}
        onCancel={() => setNewSessionOpen(false)}
      >
        <div className="new-session-agent-copy">
          一个任务只绑定一位接单员工。创建后，该任务不会随左下角默认选择变化。
        </div>
        <div className="new-session-agent-list">
          {availableAgents.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可用员工" />
          ) : (
            <>
              {personalAgents.length > 0 && <div className="new-session-agent-group-title">个人员工</div>}
              {personalAgents.map((agent) => {
                const profile = employeeProfile(agent);
                return (
                  <button
                    key={agent.id}
                    type="button"
                    className={`new-session-agent-card ${newSessionAgentId === agent.id ? 'selected' : ''}`}
                    onClick={() => setNewSessionAgentId(agent.id)}
                  >
                    <EmployeeAvatarMark profile={profile} />
                    <span className="new-session-agent-info">
                      <span className="new-session-agent-name">{employeeDisplayName(agent)}</span>
                      <span className="new-session-agent-desc">{profile.roleName} · {agent.description || '使用该员工的技能、SOP、业务资料和岗位人设'}</span>
                    </span>
                    {isGalleryEmployee(agent) && <span className="new-session-agent-badge">已开放</span>}
                  </button>
                );
              })}
              {galleryAgents.length > 0 && <div className="new-session-agent-group-title">员工广场</div>}
              {galleryAgents.map((agent) => {
                const profile = employeeProfile(agent);
                return (
                  <button
                    key={agent.id}
                    type="button"
                    className={`new-session-agent-card ${newSessionAgentId === agent.id ? 'selected' : ''}`}
                    onClick={() => setNewSessionAgentId(agent.id)}
                  >
                    <EmployeeAvatarMark profile={profile} />
                    <span className="new-session-agent-info">
                      <span className="new-session-agent-name">{employeeDisplayName(agent)}</span>
                      <span className="new-session-agent-desc">{profile.roleName} · {agent.description || '员工广场开放的数字员工'}</span>
                    </span>
                    <span className="new-session-agent-badge">广场</span>
                  </button>
                );
              })}
            </>
          )}
        </div>
      </Modal>
      <Modal
        className="knowledge-citation-modal"
        title="引用详情"
        width="min(1160px, calc(100vw - 40px))"
        open={Boolean(activeCitation)}
        footer={null}
        onCancel={() => setActiveCitation(null)}
      >
        {activeCitation && (
          <div className="citation-detail">
            <div className="citation-detail-eyebrow">{citationKindLabel(activeCitation)}</div>
            <h3>{citationDisplayTitle(activeCitation)}</h3>
            {(activeCitation.summary || activeCitation.excerpt) && (
              <div className="citation-detail-section">
                <span>引用内容</span>
                <p>{activeCitation.summary || activeCitation.excerpt}</p>
              </div>
            )}
            {activeCitation.summary && activeCitation.excerpt && (
              <div className="citation-detail-section">
                <span>证据片段</span>
                <blockquote>{activeCitation.excerpt}</blockquote>
              </div>
            )}
            {(activeCitation.source_path || activeCitation.section_path || activeCitation.concept_id) && (
              <div className="citation-detail-grid">
                {activeCitation.source_path && (
                  <div>
                    <span>来源</span>
                    <strong>{citationSourceLabel(activeCitation)}</strong>
                  </div>
                )}
                {activeCitation.section_path && (
                  <div>
                    <span>章节</span>
                    <strong>{citationSectionLabel(activeCitation)}</strong>
                  </div>
                )}
                {activeCitation.concept_id && (
                  <div>
                    <span>Wiki 页面</span>
                    <strong>{activeCitation.concept_id}</strong>
                  </div>
                )}
              </div>
            )}
            {activeCitation.confidence_reason && (
              <div className="citation-detail-note">{activeCitation.confidence_reason}</div>
            )}
          </div>
        )}
      </Modal>
      <Modal
        title="重命名任务"
        open={Boolean(renameSession)}
        okText="保存"
        cancelText="取消"
        onOk={saveRename}
        onCancel={() => {
          setRenameSession(null);
          setRenameTitle('');
        }}
      >
        <Input
          autoFocus
          maxLength={80}
          value={renameTitle}
          onChange={(event) => setRenameTitle(event.target.value)}
          onPressEnter={saveRename}
          placeholder="输入任务名称"
        />
      </Modal>
    </div>
  );
}

function formatDraftSchedule(draft: ScheduledTaskDraftRead): string {
  const schedule = draft.schedule || {};
  if (draft.schedule_type === 'weekly') {
    const weekdays = Array.isArray(schedule.weekdays)
      ? schedule.weekdays.map((item) => ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][Number(item)]).filter(Boolean).join('、')
      : '周一';
    return `每周 ${weekdays} ${schedule.time || '09:00'}`;
  }
  if (draft.schedule_type === 'monthly') {
    return `每月 ${schedule.day_of_month || 1} 号 ${schedule.time || '09:00'}`;
  }
  if (draft.schedule_type === 'once') {
    const value = String(schedule.run_at || '');
    const date = value ? new Date(value) : null;
    return date && !Number.isNaN(date.getTime())
      ? `一次性 ${date.toLocaleString('zh-CN', { hour12: false })}`
      : '一次性';
  }
  return `每天 ${schedule.time || '09:00'}`;
}

function scheduleTypeLabel(type: ScheduledTaskDraftRead['schedule_type']): string {
  if (type === 'once') return '一次性';
  if (type === 'weekly') return '每周';
  if (type === 'monthly') return '每月';
  return '每天';
}

function scheduleEditValue(draft: ScheduledTaskDraftRead): string {
  const schedule = draft.schedule || {};
  if (draft.schedule_type === 'once') return String(schedule.run_at || '');
  return String(schedule.time || '09:00');
}

function scheduleFromEditValue(draft: ScheduledTaskDraftRead, value: string): Record<string, unknown> {
  if (draft.schedule_type === 'once') {
    return { ...(draft.schedule || {}), run_at: value };
  }
  return { ...(draft.schedule || {}), time: value };
}
