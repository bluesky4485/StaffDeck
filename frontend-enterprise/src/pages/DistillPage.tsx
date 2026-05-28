import { SaveOutlined, StopOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Space, Typography, message } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { api, streamPost, TENANT_ID } from '../api/client';
import type { SkillCard } from '../types';

type SkillCardEditor = Omit<SkillCard, 'skill_id'> & { skill_id: string };

function toSkillEditor(content: SkillCard): SkillCardEditor {
  const { skill_id, ...rest } = content;
  return { skill_id, ...rest };
}

function fromSkillEditor(content: SkillCardEditor | (Partial<SkillCardEditor> & Partial<SkillCard>)): SkillCard {
  const { skill_id, ...rest } = content;
  return { skill_id: skill_id || 'new_skill', ...rest } as SkillCard;
}

export default function DistillPage() {
  const [form] = Form.useForm();
  const [draft, setDraft] = useState<SkillCard | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [jsonText, setJsonText] = useState('');
  const [loading, setLoading] = useState(false);
  const [streamStatus, setStreamStatus] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function distill() {
    setLoading(true);
    setDraft(null);
    setWarnings([]);
    setJsonText('');
    setStreamStatus('正在改写技能');
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const values = await form.validateFields();
      await streamPost(
        '/api/enterprise/skills/distill/stream',
        {
          tenant_id: TENANT_ID,
          title: values.title,
          raw_content: values.raw_content,
        },
        (item) => {
          if (item.event === 'status') {
            setStreamStatus('正在改写技能');
            return;
          }
          if (item.event === 'chunk') {
            const content = typeof item.data.content === 'string' ? item.data.content : '';
            if (content) {
              setJsonText((current) => current + content);
            }
            return;
          }
          if (item.event === 'complete') {
            const draftSkill = item.data.draft_skill as SkillCard;
            const nextWarnings = Array.isArray(item.data.warnings) ? item.data.warnings.map(String) : [];
            setDraft(draftSkill);
            setWarnings(nextWarnings);
            setJsonText(JSON.stringify(toSkillEditor(draftSkill), null, 2));
            setStreamStatus('生成完成');
          }
        },
        controller.signal,
      );
    } catch (error) {
      if (controller.signal.aborted) {
        message.info('已停止生成');
      } else {
        message.error(error instanceof Error ? error.message : '生成失败');
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      setLoading(false);
    }
  }

  function stopDistill() {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setStreamStatus('已停止');
  }

  async function saveDraft() {
    try {
      const content = fromSkillEditor(JSON.parse(jsonText));
      try {
        await api.post('/api/enterprise/skills', { tenant_id: TENANT_ID, content, status: 'draft' });
      } catch (error) {
        if (!(error instanceof Error) || !error.message.includes('409')) {
          throw error;
        }
        await api.put(`/api/enterprise/skills/${content.skill_id}`, { tenant_id: TENANT_ID, content, status: 'draft' });
      }
      message.success('草稿已保存');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存失败');
    }
  }

  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>技能改写</Typography.Title>
      </div>
      <div className="grid-2">
        <Card>
          <Form form={form} layout="vertical">
            <Form.Item label="文档标题" name="title" rules={[{ required: true }]}>
              <Input placeholder="输入技能名称" />
            </Form.Item>
            <Form.Item label="原始技能文本" name="raw_content" rules={[{ required: true }]}>
              <Input.TextArea rows={18} placeholder="粘贴业务流程、客服话术或操作规范" />
            </Form.Item>
            <Space>
              <Button type="primary" icon={<ThunderboltOutlined />} loading={loading} onClick={distill}>生成</Button>
              {loading && <Button icon={<StopOutlined />} onClick={stopDistill}>停止</Button>}
            </Space>
          </Form>
        </Card>
        <Card
          title="Skill Card"
          extra={
            <Space>
              {streamStatus && <Typography.Text type="secondary">{streamStatus}</Typography.Text>}
              <Button disabled={!draft || loading} icon={<SaveOutlined />} onClick={saveDraft}>
                保存草稿
              </Button>
            </Space>
          }
        >
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {warnings.map((warning) => (
              <Alert key={warning} type="warning" message={warning} showIcon />
            ))}
            <Input.TextArea className="json-editor" value={jsonText} onChange={(event) => setJsonText(event.target.value)} />
          </Space>
        </Card>
      </div>
    </>
  );
}
