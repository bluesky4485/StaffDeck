import {
  BranchesOutlined,
  CheckOutlined,
  CodeOutlined,
  SaveOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { Alert, Button, Card, Empty, Input, Space, Typography, message } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, streamPost, TENANT_ID } from '../api/client';
import type { SkillCard, SkillRead } from '../types';

type ChatItem = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
};

type TargetSelection = {
  path: string;
  label: string;
};

type ViewMode = 'source' | 'flow';

const DEFAULT_TARGET_PATHS = ['basic'];

export default function DistillPage() {
  const [searchParams] = useSearchParams();
  const skillId = searchParams.get('skill_id');
  const [draft, setDraft] = useState<SkillCard | null>(null);
  const [loadedSkill, setLoadedSkill] = useState<SkillRead | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [messages, setMessages] = useState<ChatItem[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content: '请粘贴原始技能说明，或点击右侧某一块后告诉我需要怎样改写。',
    },
  ]);
  const [input, setInput] = useState('');
  const [selectedPaths, setSelectedPaths] = useState<string[]>(DEFAULT_TARGET_PATHS);
  const [viewMode, setViewMode] = useState<ViewMode>('source');
  const [loading, setLoading] = useState(false);
  const [streamStatus, setStreamStatus] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!skillId) {
      setDraft(null);
      setLoadedSkill(null);
      setWarnings([]);
      setSelectedPaths(DEFAULT_TARGET_PATHS);
      return;
    }
    api
      .get<SkillRead>(`/api/enterprise/skills/${encodeURIComponent(skillId)}?tenant_id=${TENANT_ID}`)
      .then((result) => {
        setDraft(result.content);
        setLoadedSkill(result);
        setSelectedPaths(DEFAULT_TARGET_PATHS);
        setMessages([
          {
            id: 'loaded',
            role: 'assistant',
            content: `已加载「${result.name}」。你可以在右侧选择一个或多个区域，然后在这里描述需要怎样改写。`,
          },
        ]);
      })
      .catch((error) => message.error(error instanceof Error ? error.message : '加载技能失败'));
  }, [skillId]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const allPaths = useMemo(() => (draft ? allTargetPaths(draft) : DEFAULT_TARGET_PATHS), [draft]);
  const allSelected = draft ? allPaths.every((path) => selectedPaths.includes(path)) : false;

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput('');
    pushMessage('user', text);
    if (!draft) {
      await createDraftFromText(text);
      return;
    }
    await rewriteSelectedTarget(text);
  }

  async function createDraftFromText(text: string) {
    const payload = parseInitialSkillPrompt(text);
    setLoading(true);
    setStreamStatus('正在生成技能草稿');
    const assistantId = pushMessage('assistant', '正在生成技能草稿...');
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamPost(
        '/api/enterprise/skills/distill/stream',
        { tenant_id: TENANT_ID, ...payload },
        (item) => {
          if (item.event === 'complete') {
            const draftSkill = item.data.draft_skill as SkillCard;
            const nextWarnings = Array.isArray(item.data.warnings) ? item.data.warnings.map(String) : [];
            setDraft(draftSkill);
            setWarnings(nextWarnings);
            setSelectedPaths(DEFAULT_TARGET_PATHS);
            updateMessage(
              assistantId,
              `已生成「${draftSkill.name}」草稿。你可以在右侧选择一个或多个区域继续改写。`,
            );
            setStreamStatus('生成完成');
          }
        },
        controller.signal,
      );
    } catch (error) {
      updateMessage(assistantId, '生成失败，当前草稿未变更。');
      if (controller.signal.aborted) {
        message.info('已停止生成');
      } else {
        message.error(error instanceof Error ? error.message : '生成失败');
      }
    } finally {
      finishStream(controller);
    }
  }

  async function rewriteSelectedTarget(text: string) {
    if (!draft) return;
    setLoading(true);
    setStreamStatus('正在改写选中内容');
    const assistantId = pushMessage('assistant', '');
    const controller = new AbortController();
    let receivedMessageChunk = false;
    const targets = selectedPaths.length > 0 ? selectedPaths : DEFAULT_TARGET_PATHS;
    abortRef.current = controller;
    try {
      await streamPost(
        `/api/enterprise/skills/${encodeURIComponent(draft.skill_id)}/rewrite/stream`,
        {
          tenant_id: TENANT_ID,
          current_skill: draft,
          instruction: text,
          target_path: targets[0],
          target_paths: targets,
          target_label: targetLabel(targets, draft),
          conversation: messages.map((item) => ({ role: item.role, content: item.content })),
        },
        (item) => {
          if (item.event === 'message_chunk') {
            const content = typeof item.data.content === 'string' ? item.data.content : '';
            if (content) {
              receivedMessageChunk = true;
              appendMessage(assistantId, content);
            }
            return;
          }
          if (item.event === 'complete') {
            const nextDraft = item.data.draft_skill as SkillCard;
            const nextWarnings = Array.isArray(item.data.warnings) ? item.data.warnings.map(String) : [];
            setDraft(nextDraft);
            setSelectedPaths((current) => reconcileSelectedPaths(current, nextDraft));
            setWarnings(nextWarnings);
            setStreamStatus('改写完成');
            if (!receivedMessageChunk) {
              updateMessage(assistantId, String(item.data.assistant_message || '已完成局部改写。'));
            }
          }
        },
        controller.signal,
      );
    } catch (error) {
      updateMessage(assistantId, '改写失败，当前草稿未变更。');
      if (controller.signal.aborted) {
        message.info('已停止改写');
      } else {
        message.error(error instanceof Error ? error.message : '改写失败');
      }
    } finally {
      finishStream(controller);
    }
  }

  async function saveDraft() {
    if (!draft) return;
    try {
      if (loadedSkill) {
        await api.put(`/api/enterprise/skills/${loadedSkill.skill_id}`, {
          tenant_id: TENANT_ID,
          content: draft,
          status: loadedSkill.status,
        });
      } else {
        try {
          await api.post('/api/enterprise/skills', { tenant_id: TENANT_ID, content: draft, status: 'draft' });
        } catch (error) {
          if (!(error instanceof Error) || !error.message.includes('409')) throw error;
          await api.put(`/api/enterprise/skills/${draft.skill_id}`, {
            tenant_id: TENANT_ID,
            content: draft,
            status: 'draft',
          });
        }
      }
      message.success('草稿已保存');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存失败');
    }
  }

  function stopStream() {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setStreamStatus('已停止');
  }

  function toggleTarget(target: TargetSelection) {
    setSelectedPaths((current) => {
      if (current.includes(target.path)) {
        return current.length > 1 ? current.filter((path) => path !== target.path) : current;
      }
      return [...current, target.path];
    });
  }

  function toggleAllTargets() {
    setSelectedPaths(allSelected ? DEFAULT_TARGET_PATHS : allPaths);
  }

  function pushMessage(role: ChatItem['role'], content: string) {
    const id = `${role}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    setMessages((current) => [...current, { id, role, content }]);
    return id;
  }

  function updateMessage(id: string, content: string) {
    setMessages((current) => current.map((item) => (item.id === id ? { ...item, content } : item)));
  }

  function appendMessage(id: string, content: string) {
    setMessages((current) =>
      current.map((item) => (item.id === id ? { ...item, content: `${item.content}${content}` } : item)),
    );
  }

  function finishStream(controller: AbortController) {
    if (abortRef.current === controller) abortRef.current = null;
    setLoading(false);
  }

  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>技能改写</Typography.Title>
      </div>
      <div className="skill-workbench">
        <Card className="skill-chat-card">
          <div className="skill-chat-panel">
            <div className="skill-chat-messages">
              {messages.map((item) => (
                <div key={item.id} className={`skill-chat-row ${item.role}`}>
                  <div className="skill-chat-bubble">{item.content || '正在处理...'}</div>
                </div>
              ))}
            </div>
            <div className="skill-chat-composer">
              <Input.TextArea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onPressEnter={(event) => {
                  if (!event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void send();
                  }
                }}
                rows={4}
                placeholder={
                  draft
                    ? '说明你要如何改写右侧选中的部分'
                    : '输入“标题：... 原始SOP文本：...”或直接粘贴流程说明'
                }
              />
              <div className="skill-chat-actions">
                <Typography.Text type="secondary">{streamStatus}</Typography.Text>
                <Space>
                  {loading && (
                    <Button icon={<StopOutlined />} onClick={stopStream}>
                      停止
                    </Button>
                  )}
                  <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={() => void send()}>
                    发送
                  </Button>
                </Space>
              </div>
            </div>
          </div>
        </Card>
        <Card
          className="skill-source-card"
          title={viewMode === 'source' ? '源码' : '流程图'}
          extra={
            <Button disabled={!draft || loading} icon={<SaveOutlined />} onClick={saveDraft}>
              保存草稿
            </Button>
          }
        >
          <div className="skill-source-toolbar">
            <Space>
              <Button
                icon={viewMode === 'source' ? <BranchesOutlined /> : <CodeOutlined />}
                onClick={() => setViewMode(viewMode === 'source' ? 'flow' : 'source')}
              >
                {viewMode === 'source' ? '显示流程' : '显示源码'}
              </Button>
              <Button disabled={!draft} onClick={toggleAllTargets}>
                {allSelected ? '取消全选' : '全选'}
              </Button>
            </Space>
          </div>
          {warnings.map((warning) => (
            <Alert key={warning} type="warning" message={warning} showIcon className="skill-warning" />
          ))}
          {!draft ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无技能草稿" />
          ) : viewMode === 'source' ? (
            <SkillSource
              skill={draft}
              selectedPaths={selectedPaths}
              onToggle={toggleTarget}
            />
          ) : (
            <SkillFlow skill={draft} selectedPaths={selectedPaths} onToggle={toggleTarget} />
          )}
        </Card>
      </div>
    </>
  );
}

function SkillSource({
  skill,
  selectedPaths,
  onToggle,
}: {
  skill: SkillCard;
  selectedPaths: string[];
  onToggle: (target: TargetSelection) => void;
}) {
  return (
    <div className="skill-source-md">
      <div className="skill-source-group-title">基础信息</div>
      <button
        type="button"
        className={`skill-source-section ${selectedPaths.includes('basic') ? 'active' : ''}`}
        onClick={() => onToggle({ path: 'basic', label: '基础信息' })}
      >
        {selectedPaths.includes('basic') && <span className="selection-mark"><CheckOutlined /></span>}
        <pre>{basicToMarkdown(skill)}</pre>
      </button>
      <div className="skill-source-group-title">详细步骤</div>
      <div className="skill-source-steps">
        {skill.steps.map((step, index) => {
          const stepId = String(step.step_id || `step_${index + 1}`);
          const path = stepTargetPath(index);
          return (
            <button
              type="button"
              key={path}
              className={`skill-source-section ${selectedPaths.includes(path) ? 'active' : ''}`}
              onClick={() => onToggle({ path, label: `步骤 ${index + 1}：${step.name || stepId}` })}
            >
              {selectedPaths.includes(path) && <span className="selection-mark"><CheckOutlined /></span>}
              <pre>{stepToMarkdown(step, index)}</pre>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function SkillFlow({
  skill,
  selectedPaths,
  onToggle,
}: {
  skill: SkillCard;
  selectedPaths: string[];
  onToggle: (target: TargetSelection) => void;
}) {
  return (
    <div className="skill-flow">
      <button
        type="button"
        className={`skill-flow-node root ${selectedPaths.includes('basic') ? 'active' : ''}`}
        onClick={() => onToggle({ path: 'basic', label: '基础信息' })}
      >
        {selectedPaths.includes('basic') && <span className="selection-mark"><CheckOutlined /></span>}
        <span>基础信息</span>
        <strong>{skill.name}</strong>
        <small>{skill.skill_id}</small>
        <p>{skill.description || '暂无描述'}</p>
        <div className="skill-flow-meta">
          <em>业务域 {skill.business_domain || '-'}</em>
          <em>必填 {joinPlain(skill.required_info)}</em>
          <em>意图 {joinPlain(skill.trigger_intents)}</em>
        </div>
      </button>
      {skill.steps.map((step, index) => {
        const stepId = String(step.step_id || `step_${index + 1}`);
        const path = stepTargetPath(index);
        const toolActions = asStringList(step.allowed_actions).filter((action) =>
          String(action).startsWith('call_tool:'),
        );
        return (
          <div className="skill-flow-step" key={path}>
            <div className="skill-flow-line" />
            <button
              type="button"
              className={`skill-flow-node ${selectedPaths.includes(path) ? 'active' : ''}`}
              onClick={() => onToggle({ path, label: `步骤 ${index + 1}：${step.name || stepId}` })}
            >
              {selectedPaths.includes(path) && <span className="selection-mark"><CheckOutlined /></span>}
              <span>Step {index + 1}</span>
              <strong>{String(step.name || stepId)}</strong>
              <small>{stepId}</small>
              <p>{String(step.instruction || '暂无说明')}</p>
              <div className="skill-flow-meta">
                <em>字段 {joinPlain(asStringList(step.expected_user_info))}</em>
                <em>动作 {joinPlain(asStringList(step.allowed_actions))}</em>
              </div>
            </button>
            {toolActions.length > 0 && (
              <div className="skill-flow-tools">
                {toolActions.map((action) => (
                  <div className="skill-flow-tool" key={String(action)}>
                    {String(action).replace('call_tool:', '')}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function parseInitialSkillPrompt(text: string): { title: string; raw_content: string } {
  const titleMatch = text.match(/标题[:：]\s*([^\n，,]+)/);
  const rawMatch = text.match(/原始(?:SOP|技能)?文本[:：]?\s*([\s\S]+)/);
  const lines = text.split('\n').map((line) => line.trim()).filter(Boolean);
  const title = titleMatch?.[1]?.trim() || lines[0]?.slice(0, 32) || '新技能';
  const rawContent = rawMatch?.[1]?.trim() || lines.slice(titleMatch ? 0 : 1).join('\n') || text;
  return { title, raw_content: rawContent };
}

function basicToMarkdown(skill: SkillCard): string {
  return [
    `# ${skill.name}`,
    '',
    `- skill_id: \`${skill.skill_id}\``,
    `- version: \`${skill.version}\``,
    `- business_domain: ${skill.business_domain || '-'}`,
    `- description: ${skill.description || '-'}`,
    `- trigger_intents: ${joinList(skill.trigger_intents)}`,
    `- user_utterance_examples: ${joinList(skill.user_utterance_examples)}`,
    `- goal: ${joinList(skill.goal)}`,
    `- required_info: ${joinList(skill.required_info)}`,
    `- response_rules: ${joinList(skill.response_rules)}`,
  ].join('\n');
}

function stepToMarkdown(step: Record<string, unknown>, index: number): string {
  return [
    `### Step ${index + 1}: ${String(step.name || '-')}`,
    `- step_id: \`${String(step.step_id || '-')}\``,
    `- instruction: ${String(step.instruction || '-')}`,
    `- expected_user_info: ${joinList(asStringList(step.expected_user_info))}`,
    `- allowed_actions: ${joinList(asStringList(step.allowed_actions))}`,
  ].join('\n');
}

function joinList(values: string[] | undefined): string {
  return values && values.length > 0 ? values.map((item) => `\`${item}\``).join(', ') : '-';
}

function joinPlain(values: string[] | undefined): string {
  return values && values.length > 0 ? values.join('、') : '-';
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function allTargetPaths(skill: SkillCard): string[] {
  return [
    'basic',
    ...skill.steps.map((_step, index) => stepTargetPath(index)),
  ];
}

function reconcileSelectedPaths(paths: string[], skill: SkillCard): string[] {
  const available = allTargetPaths(skill);
  const next = paths.filter((path) => available.includes(path));
  return next.length > 0 ? next : DEFAULT_TARGET_PATHS;
}

function targetLabel(paths: string[], skill: SkillCard): string {
  const labels = paths.map((path) => {
    if (path === 'basic') return '基础信息';
    const stepIndex = stepIndexFromPath(path);
    if (stepIndex !== null) {
      const index = stepIndex;
      const step = index >= 0 ? skill.steps[index] : null;
      return step ? `步骤 ${index + 1}：${step.name || step.step_id || path}` : path;
    }
    return path;
  });
  return labels.join('、');
}

function stepTargetPath(index: number): string {
  return `steps[${index}]`;
}

function stepIndexFromPath(path: string): number | null {
  const match = path.match(/^steps\[(\d+)\]$/);
  return match ? Number(match[1]) : null;
}
