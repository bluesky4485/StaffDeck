import { DatabaseOutlined, EyeOutlined, SearchOutlined } from '@ant-design/icons';
import { Button, Card, Descriptions, Drawer, Empty, Form, Input, Space, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useState } from 'react';
import { api, TENANT_ID } from '../api/client';
import type { MemoryRead } from '../types';

const ENTERPRISE_AGENT_STORAGE_KEY = 'ultrarag_enterprise_agent_scope';

type MemoryFilter = {
  username?: string;
  user_id?: string;
  q?: string;
};

type MemoryUserGroup = {
  key: string;
  username?: string;
  user_id: string;
  memories: MemoryRead[];
  kinds: string[];
  latest_at: string;
  preview: string;
};

export default function MemoriesPage() {
  const [rows, setRows] = useState<MemoryRead[]>([]);
  const [detail, setDetail] = useState<MemoryUserGroup | null>(null);
  const [loading, setLoading] = useState(false);
  const [agentId, setAgentId] = useState(() => window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) || '');
  const [form] = Form.useForm<MemoryFilter>();

  const load = async () => {
    setLoading(true);
    try {
      const values = form.getFieldsValue();
      const params = new URLSearchParams({ tenant_id: TENANT_ID });
      if (agentId) params.set('agent_id', agentId);
      if (values.username?.trim()) params.set('username', values.username.trim());
      if (values.user_id?.trim()) params.set('user_id', values.user_id.trim());
      if (values.q?.trim()) params.set('q', values.q.trim());
      params.set('limit', '500');
      const result = await api.get<MemoryRead[]>(`/api/enterprise/memories?${params.toString()}`);
      setRows(result);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '查询失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const onScopeChange = (event: Event) => {
      const nextAgentId = (event as CustomEvent<{ agentId?: string }>).detail?.agentId || window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) || '';
      setAgentId(nextAgentId);
    };
    window.addEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
    return () => window.removeEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
  }, []);

  useEffect(() => {
    load();
  }, [agentId]);

  const groups = useMemo(() => groupMemories(rows), [rows]);
  const emptyDescription = agentId
    ? '当前员工暂无成长轨迹；新的对话记忆会按员工隔离沉淀。'
    : '暂无成长轨迹';

  const columns: ColumnsType<MemoryUserGroup> = [
    { title: '用户名', dataIndex: 'username', width: 160, ellipsis: true, render: (value) => value || '-' },
    { title: '用户 ID', dataIndex: 'user_id', width: 180, ellipsis: true },
    {
      title: '类型',
      dataIndex: 'kinds',
      width: 210,
      render: (kinds: string[]) => (
        <Space size={[4, 4]} wrap>
          {kinds.map((kind) => <Tag key={kind} color={memoryColor(kind)}>{kind}</Tag>)}
        </Space>
      ),
    },
    { title: '记忆数', width: 110, render: (_, row) => row.memories.length },
    {
      title: '摘要',
      dataIndex: 'preview',
      ellipsis: true,
      render: (value) => <span className="muted-cell">{value || '-'}</span>,
    },
    { title: '最近更新', dataIndex: 'latest_at', width: 180, render: (value) => new Date(value).toLocaleString() },
    {
      title: '操作',
      width: 110,
      fixed: 'right',
      render: (_, row) => (
        <Button icon={<EyeOutlined />} onClick={() => setDetail(row)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>成长轨迹</Typography.Title>
      </div>
      <Card className="data-card" title={<><DatabaseOutlined /> 成长轨迹查询</>}>
        <Form form={form} layout="inline" className="toolbar-form" onFinish={load}>
          <Form.Item name="username" label="用户名">
            <Input allowClear placeholder="如 user_demo" />
          </Form.Item>
          <Form.Item name="user_id" label="用户 ID">
            <Input allowClear placeholder="如 user_demo" />
          </Form.Item>
          <Form.Item name="q" label="搜索">
            <Input allowClear placeholder="用户名、用户 ID、记忆内容" />
          </Form.Item>
          <Space>
            <Button type="primary" icon={<SearchOutlined />} htmlType="submit" loading={loading}>查询</Button>
            <Button onClick={() => { form.resetFields(); load(); }}>重置</Button>
          </Space>
        </Form>
        <Table
          rowKey="key"
          columns={columns}
          dataSource={groups}
          loading={loading}
          pagination={{ pageSize: 10 }}
          locale={{ emptyText: <Empty description={emptyDescription} /> }}
          scroll={{ x: 1080 }}
        />
      </Card>
      <Drawer
        title="成长轨迹详情"
        open={Boolean(detail)}
        width={780}
        onClose={() => setDetail(null)}
        destroyOnClose
      >
        {detail ? (
          <div className="memory-detail">
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="用户名">{detail.username || '-'}</Descriptions.Item>
              <Descriptions.Item label="用户 ID">{detail.user_id}</Descriptions.Item>
              <Descriptions.Item label="记忆数">{detail.memories.length}</Descriptions.Item>
              <Descriptions.Item label="类型">
                <Space size={[4, 4]} wrap>
                  {detail.kinds.map((kind) => <Tag key={kind} color={memoryColor(kind)}>{kind}</Tag>)}
                </Space>
              </Descriptions.Item>
            </Descriptions>
            <div className="memory-records">
              {detail.memories.map((item) => (
                <Card
                  key={item.id}
                  size="small"
                  className="memory-record-card"
                  title={<Tag color={memoryColor(item.kind)}>{item.kind}</Tag>}
                  extra={<Typography.Text type="secondary">{new Date(item.updated_at).toLocaleString()}</Typography.Text>}
                >
                  <div className="memory-record-meta">
                    <span>importance: {item.importance}</span>
                    <span>session: {item.session_id || '-'}</span>
                  </div>
                  <Typography.Paragraph className="memory-content">
                    {item.content}
                  </Typography.Paragraph>
                  {Object.keys(item.metadata || {}).length > 0 ? (
                    <details className="memory-metadata">
                      <summary>metadata</summary>
                      <pre>{JSON.stringify(item.metadata, null, 2)}</pre>
                    </details>
                  ) : null}
                </Card>
              ))}
            </div>
          </div>
        ) : null}
      </Drawer>
    </>
  );
}

function groupMemories(rows: MemoryRead[]): MemoryUserGroup[] {
  const map = new Map<string, MemoryRead[]>();
  rows.forEach((row) => {
    const key = row.username || row.user_id;
    const existing = map.get(key) || [];
    existing.push(row);
    map.set(key, existing);
  });
  return Array.from(map.entries()).map(([key, memories]) => {
    const sorted = [...memories].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    const kinds = Array.from(new Set(sorted.map((item) => item.kind))).sort();
    return {
      key,
      username: sorted[0]?.username,
      user_id: sorted[0]?.user_id || key,
      memories: sorted,
      kinds,
      latest_at: sorted[0]?.updated_at,
      preview: sorted.map((item) => item.content.replace(/\s+/g, ' ').trim()).filter(Boolean).join(' / '),
    };
  }).sort((a, b) => new Date(b.latest_at).getTime() - new Date(a.latest_at).getTime());
}

function memoryColor(kind: string): string {
  if (kind === 'profile') return 'green';
  if (kind === 'summary') return 'blue';
  return 'default';
}
