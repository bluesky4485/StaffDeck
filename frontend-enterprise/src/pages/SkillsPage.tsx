import {
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  HistoryOutlined,
  MoreOutlined,
  PlusOutlined,
  RollbackOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { Button, Card, Col, Descriptions, Dropdown, Modal, Row, Segmented, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, TENANT_ID } from '../api/client';
import type { SkillRead, SkillVersionRead } from '../types';

const STATUS_LABELS: Record<SkillRead['status'], { text: string; color: string }> = {
  draft: { text: '草稿', color: 'blue' },
  published: { text: '已发布', color: 'green' },
  archived: { text: '已归档', color: 'default' },
};

type RankingMode = 'calls' | 'positive' | 'negative';
type RankingScope = 'current' | 'total';
type RankedSkill = SkillRead & { rank: number };
type RankingModalState = { mode: RankingMode; scope: RankingScope };
type NumericSkillMetric =
  | 'call_count'
  | 'positive_feedback_count'
  | 'negative_feedback_count'
  | 'positive_rate'
  | 'negative_rate'
  | 'total_call_count'
  | 'total_positive_feedback_count'
  | 'total_negative_feedback_count'
  | 'total_positive_rate'
  | 'total_negative_rate'
  | 'recent_call_count'
  | 'recent_positive_feedback_count'
  | 'recent_negative_feedback_count'
  | 'recent_positive_rate'
  | 'recent_negative_rate';

export default function SkillsPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<SkillRead[]>([]);
  const [versionRows, setVersionRows] = useState<SkillVersionRead[]>([]);
  const [versionSkill, setVersionSkill] = useState<SkillRead | null>(null);
  const [detailVersion, setDetailVersion] = useState<SkillVersionRead | null>(null);
  const [rankingModal, setRankingModal] = useState<RankingModalState | null>(null);
  const [positiveScope, setPositiveScope] = useState<RankingScope>('current');
  const [negativeScope, setNegativeScope] = useState<RankingScope>('current');
  const [versionModalTitle, setVersionModalTitle] = useState('');
  const [versionModalOpen, setVersionModalOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const result = await api.get<SkillRead[]>(`/api/enterprise/skills?tenant_id=${TENANT_ID}`);
      setRows(result);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const columns: ColumnsType<SkillRead> = useMemo(
    () => [
      { title: '技能名称', dataIndex: 'name', width: 180, ellipsis: true },
      { title: '技能 ID', dataIndex: 'skill_id', width: 190, ellipsis: true },
      { title: '业务域', dataIndex: 'business_domain', width: 140, ellipsis: true },
      { title: '版本', dataIndex: 'version', width: 90 },
      {
        title: '状态',
        dataIndex: 'status',
        width: 110,
        render: (status: SkillRead['status']) => {
          const option = STATUS_LABELS[status] || { text: status, color: 'default' };
          return <Tag color={option.color}>{option.text}</Tag>;
        },
      },
      { title: '调用次数', dataIndex: 'call_count', width: 100 },
      {
        title: '好评率',
        dataIndex: 'positive_rate',
        width: 100,
        render: (value: number) => percent(value),
      },
      {
        title: '差评率',
        dataIndex: 'negative_rate',
        width: 100,
        render: (value: number) => percent(value),
      },
      {
        title: '操作',
        width: 80,
        fixed: 'right',
        render: (_, row) => (
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                { key: 'edit', icon: <EditOutlined />, label: '编辑' },
                { key: 'versions', icon: <HistoryOutlined />, label: '版本管理' },
                { key: 'publish', icon: <CheckCircleOutlined />, label: '发布' },
                { key: 'archive', icon: <StopOutlined />, label: '下线' },
                { key: 'delete', icon: <DeleteOutlined />, label: '删除', danger: true },
              ],
              onClick: ({ key }) => handleAction(key, row),
            }}
          >
            <Button type="text" icon={<MoreOutlined />} aria-label="技能操作" />
          </Dropdown>
        ),
      },
    ],
    [],
  );

  const rankingRows = useMemo(
    () => ({
      calls: rankByMetric(rows, 'total_call_count'),
      positiveCurrent: rankByMetric(rows, 'positive_rate', 'positive_feedback_count', 'call_count'),
      positiveTotal: rankByMetric(rows, 'total_positive_rate', 'total_positive_feedback_count', 'total_call_count'),
      negativeCurrent: rankByMetric(rows, 'negative_rate', 'negative_feedback_count', 'call_count'),
      negativeTotal: rankByMetric(rows, 'total_negative_rate', 'total_negative_feedback_count', 'total_call_count'),
    }),
    [rows],
  );

  const positiveRankingRows = positiveScope === 'current' ? rankingRows.positiveCurrent : rankingRows.positiveTotal;
  const negativeRankingRows = negativeScope === 'current' ? rankingRows.negativeCurrent : rankingRows.negativeTotal;
  const rankingModalRows = rankingModal ? rankingRowsFor(rankingRows, rankingModal.mode, rankingModal.scope) : [];
  const rankingModalTitle = rankingModal ? rankingTitle(rankingModal.mode, rankingModal.scope) : '完整排行';
  const rankingModalColumns = useMemo<ColumnsType<RankedSkill>>(
    () => [
      { title: '排名', dataIndex: 'rank', width: 80 },
      { title: '技能名称', dataIndex: 'name', ellipsis: true },
      { title: '技能 ID', dataIndex: 'skill_id', ellipsis: true },
      {
        title: rankingModal?.scope === 'current' ? '版本' : '版本范围',
        width: 130,
        render: (_, row) => rankingVersionText(row, rankingModal?.scope || 'total'),
      },
      { title: '业务域', dataIndex: 'business_domain', width: 140, ellipsis: true },
      {
        title: rankingMetricTitle(rankingModal?.mode || 'calls', rankingModal?.scope || 'total'),
        width: 130,
        render: (_, row) => rankingMetricValue(row, rankingModal?.mode || 'calls', rankingModal?.scope || 'total'),
      },
      {
        title: '调用次数',
        width: 110,
        render: (_, row) => `${rankingCalls(row, rankingModal?.scope || 'total')} 次`,
      },
      {
        title: '好评率',
        width: 110,
        render: (_, row) => percent(rankingPositiveRate(row, rankingModal?.scope || 'total')),
      },
      {
        title: '差评率',
        width: 110,
        render: (_, row) => percent(rankingNegativeRate(row, rankingModal?.scope || 'total')),
      },
      {
        title: '反馈数',
        width: 110,
        render: (_, row) => rankingFeedbackText(row, rankingModal?.scope || 'total'),
      },
    ],
    [rankingModal],
  );

  function openCreate() {
    navigate('/enterprise/skills/distill?mode=create');
  }

  function openEdit(row: SkillRead) {
    navigate(`/enterprise/skills/distill?skill_id=${encodeURIComponent(row.skill_id)}`);
  }

  async function publish(row: SkillRead) {
    await api.post(`/api/enterprise/skills/${row.skill_id}/publish?tenant_id=${TENANT_ID}`);
    message.success('已发布');
    load();
  }

  async function archive(row: SkillRead) {
    await api.post(`/api/enterprise/skills/${row.skill_id}/archive?tenant_id=${TENANT_ID}`);
    message.success('已下线');
    load();
  }

  async function openVersions(row: SkillRead) {
    setVersionSkill(row);
    setVersionModalTitle(`版本管理：${row.name}`);
    setVersionModalOpen(true);
    try {
      const result = await api.get<SkillVersionRead[]>(
        `/api/enterprise/skills/${encodeURIComponent(row.skill_id)}/versions?tenant_id=${TENANT_ID}`,
      );
      setVersionRows(result);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载版本失败');
    }
  }

  async function showVersionDetail(row: SkillVersionRead) {
    try {
      const result = await api.get<SkillVersionRead>(
        `/api/enterprise/skills/${encodeURIComponent(row.skill_id)}/versions/${encodeURIComponent(row.version)}?tenant_id=${TENANT_ID}`,
      );
      setDetailVersion(result);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载版本详情失败');
    }
  }

  function rollbackVersion(row: SkillVersionRead) {
    Modal.confirm({
      title: `回滚到版本 ${row.version}？`,
      content: `当前技能将切换为「${row.name}」的 ${row.version} 版本内容，历史版本记录和历史反馈数据不会被删除。`,
      okText: '回滚',
      cancelText: '取消',
      onOk: async () => {
        const result = await api.post<SkillRead>(
          `/api/enterprise/skills/${encodeURIComponent(row.skill_id)}/versions/${encodeURIComponent(row.version)}/rollback?tenant_id=${TENANT_ID}`,
        );
        message.success(`已回滚到 ${row.version}`);
        await load();
        await openVersions(result);
      },
    });
  }

  function remove(row: SkillRead) {
    Modal.confirm({
      title: `删除技能「${row.name}」？`,
      content: '删除后不会移除历史会话记录，但技能列表中将不再显示该技能。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        await api.delete(`/api/enterprise/skills/${row.skill_id}?tenant_id=${TENANT_ID}`);
        message.success('已删除');
        load();
      },
    });
  }

  function handleAction(key: string, row: SkillRead) {
    if (key === 'edit') openEdit(row);
    if (key === 'versions') void openVersions(row);
    if (key === 'publish') void publish(row);
    if (key === 'archive') void archive(row);
    if (key === 'delete') remove(row);
  }

  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>技能管理</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建
        </Button>
      </div>
      <Card className="data-card" title="技能列表">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={rows}
          loading={loading}
          pagination={{ pageSize: 10 }}
          scroll={{ x: 1080 }}
          size="middle"
        />
      </Card>
      <Row gutter={[16, 16]} className="skill-rank-row">
        <Col xs={24} lg={8}>
          <RankingCard
            title="调用排行榜"
            rows={rankingRows.calls.slice(0, 5)}
            value={(row) => `${row.total_call_count || 0} 次`}
            onMore={() => setRankingModal({ mode: 'calls', scope: 'total' })}
          />
        </Col>
        <Col xs={24} lg={8}>
          <RankingCard
            title="好评排行榜"
            rows={positiveRankingRows.slice(0, 5)}
            value={(row) => percent(positiveScope === 'current' ? row.positive_rate : row.total_positive_rate)}
            version={(row) => rankingVersionText(row, positiveScope)}
            scope={positiveScope}
            onScopeChange={setPositiveScope}
            onMore={() => setRankingModal({ mode: 'positive', scope: positiveScope })}
          />
        </Col>
        <Col xs={24} lg={8}>
          <RankingCard
            title="差评排行榜"
            rows={negativeRankingRows.slice(0, 5)}
            value={(row) => percent(negativeScope === 'current' ? row.negative_rate : row.total_negative_rate)}
            version={(row) => rankingVersionText(row, negativeScope)}
            scope={negativeScope}
            onScopeChange={setNegativeScope}
            onMore={() => setRankingModal({ mode: 'negative', scope: negativeScope })}
          />
        </Col>
      </Row>
      <Modal
        open={Boolean(rankingModal)}
        title={rankingModalTitle}
        width={1080}
        footer={null}
        onCancel={() => setRankingModal(null)}
      >
        <Table
          rowKey="skill_id"
          dataSource={rankingModalRows}
          columns={rankingModalColumns}
          pagination={{ pageSize: 10, pageSizeOptions: [10, 15], showSizeChanger: true }}
          size="small"
          scroll={{ x: 960 }}
        />
      </Modal>
      <Modal
        open={versionModalOpen}
        title={versionModalTitle}
        width={1080}
        footer={null}
        onCancel={() => {
          setVersionModalOpen(false);
          setVersionSkill(null);
        }}
      >
        <Table
          rowKey="id"
          dataSource={versionRows}
          pagination={false}
          size="small"
          columns={[
            { title: '版本', dataIndex: 'version', width: 100 },
            { title: '技能名称', dataIndex: 'name', ellipsis: true },
            { title: '业务域', dataIndex: 'business_domain', width: 140, ellipsis: true },
            { title: '调用次数', dataIndex: 'call_count', width: 100 },
            { title: '好评率', dataIndex: 'positive_rate', width: 100, render: (value: number) => percent(value) },
            { title: '差评率', dataIndex: 'negative_rate', width: 100, render: (value: number) => percent(value) },
            { title: '更新时间', dataIndex: 'updated_at', width: 150, render: (value: string) => value.slice(0, 10) },
            {
              title: '操作',
              width: 80,
              fixed: 'right',
              render: (_, row) => (
                <Dropdown
                  trigger={['click']}
                  menu={{
                    items: [
                      { key: 'detail', icon: <EyeOutlined />, label: '查看详情' },
                      {
                        key: 'rollback',
                        icon: <RollbackOutlined />,
                        label: row.version === versionSkill?.version ? '当前版本' : '回滚到此版本',
                        disabled: row.version === versionSkill?.version,
                      },
                    ],
                    onClick: ({ key }) => {
                      if (key === 'detail') void showVersionDetail(row);
                      if (key === 'rollback') rollbackVersion(row);
                    },
                  }}
                >
                  <Button type="text" icon={<MoreOutlined />} aria-label="版本操作" />
                </Dropdown>
              ),
            },
          ]}
        />
      </Modal>
      <Modal
        open={Boolean(detailVersion)}
        title={detailVersion ? `版本详情：${detailVersion.name} / ${detailVersion.version}` : '版本详情'}
        width={920}
        footer={null}
        onCancel={() => setDetailVersion(null)}
      >
        {detailVersion && (
          <div className="version-detail">
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="技能 ID">{detailVersion.skill_id}</Descriptions.Item>
              <Descriptions.Item label="版本">{detailVersion.version}</Descriptions.Item>
              <Descriptions.Item label="业务域">{detailVersion.business_domain || '-'}</Descriptions.Item>
              <Descriptions.Item label="状态">{statusText(detailVersion.status)}</Descriptions.Item>
              <Descriptions.Item label="调用次数">{detailVersion.call_count}</Descriptions.Item>
              <Descriptions.Item label="好评率">{percent(detailVersion.positive_rate)}</Descriptions.Item>
              <Descriptions.Item label="差评率">{percent(detailVersion.negative_rate)}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{detailVersion.updated_at.slice(0, 10)}</Descriptions.Item>
            </Descriptions>
            <pre className="version-detail-source">{skillSourceText(detailVersion)}</pre>
          </div>
        )}
      </Modal>
    </>
  );
}

function RankingCard({
  title,
  rows,
  value,
  version,
  scope,
  onScopeChange,
  onMore,
}: {
  title: string;
  rows: RankedSkill[];
  value: (row: RankedSkill) => string;
  version?: (row: RankedSkill) => string;
  scope?: RankingScope;
  onScopeChange?: (scope: RankingScope) => void;
  onMore: () => void;
}) {
  return (
    <Card
      title={title}
      extra={
        <div className="skill-ranking-extra">
          {scope && onScopeChange && (
            <Segmented
              size="small"
              value={scope}
              options={[
                { label: '当前', value: 'current' },
                { label: '总榜', value: 'total' },
              ]}
              onChange={(value) => onScopeChange(value as RankingScope)}
            />
          )}
          <Button type="link" size="small" onClick={onMore}>
            查看更多
          </Button>
        </div>
      }
      className="skill-ranking-card"
    >
      {rows.length === 0 ? (
        <Typography.Text type="secondary">暂无数据</Typography.Text>
      ) : (
        rows.map((row) => (
          <div className="skill-ranking-item" key={`${title}_${row.skill_id}`}>
            <span className="skill-ranking-index">{row.rank}</span>
            <span className="skill-ranking-main">
              <span className="skill-ranking-name" title={row.name}>{row.name}</span>
            </span>
            {version && <span className="skill-ranking-version">{version(row)}</span>}
            <strong>{value(row)}</strong>
          </div>
        ))
      )}
    </Card>
  );
}

function rankByMetric(
  rows: SkillRead[],
  field: NumericSkillMetric,
  tieBreaker?: NumericSkillMetric,
  callTieBreaker: NumericSkillMetric = 'total_call_count',
): RankedSkill[] {
  return [...rows]
    .sort((a, b) => {
      const primary = (b[field] || 0) - (a[field] || 0);
      if (primary !== 0) return primary;
      if (tieBreaker) {
        const secondary = (b[tieBreaker] || 0) - (a[tieBreaker] || 0);
        if (secondary !== 0) return secondary;
      }
      return (b[callTieBreaker] || 0) - (a[callTieBreaker] || 0);
    })
    .map((row, index) => ({ ...row, rank: index + 1 }));
}

function percent(value: number | undefined): string {
  return `${Math.round((value || 0) * 100)}%`;
}

function rankingTitle(mode: RankingMode, scope: RankingScope): string {
  if (mode === 'calls') return '完整排行：全历史调用';
  if (mode === 'positive') return scope === 'current' ? '完整排行：当前版本好评率' : '完整排行：历史总榜好评率';
  return scope === 'current' ? '完整排行：当前版本差评率' : '完整排行：历史总榜差评率';
}

function rankingRowsFor(
  rows: {
    calls: RankedSkill[];
    positiveCurrent: RankedSkill[];
    positiveTotal: RankedSkill[];
    negativeCurrent: RankedSkill[];
    negativeTotal: RankedSkill[];
  },
  mode: RankingMode,
  scope: RankingScope,
): RankedSkill[] {
  if (mode === 'calls') return rows.calls;
  if (mode === 'positive') return scope === 'current' ? rows.positiveCurrent : rows.positiveTotal;
  return scope === 'current' ? rows.negativeCurrent : rows.negativeTotal;
}

function rankingVersionText(row: SkillRead, scope: RankingScope): string {
  return scope === 'current' ? `v${row.version}` : '全版本';
}

function rankingMetricTitle(mode: RankingMode, scope: RankingScope): string {
  if (mode === 'calls') return '全历史调用';
  if (mode === 'positive') return scope === 'current' ? '当前好评率' : '总好评率';
  return scope === 'current' ? '当前差评率' : '总差评率';
}

function rankingMetricValue(row: SkillRead, mode: RankingMode, scope: RankingScope): string {
  if (mode === 'calls') return `${row.total_call_count || 0} 次`;
  if (mode === 'positive') return percent(scope === 'current' ? row.positive_rate : row.total_positive_rate);
  return percent(scope === 'current' ? row.negative_rate : row.total_negative_rate);
}

function rankingCalls(row: SkillRead, scope: RankingScope): number {
  return scope === 'current' ? row.call_count || 0 : row.total_call_count || 0;
}

function rankingPositiveRate(row: SkillRead, scope: RankingScope): number {
  return scope === 'current' ? row.positive_rate || 0 : row.total_positive_rate || 0;
}

function rankingNegativeRate(row: SkillRead, scope: RankingScope): number {
  return scope === 'current' ? row.negative_rate || 0 : row.total_negative_rate || 0;
}

function rankingFeedbackText(row: SkillRead, scope: RankingScope): string {
  if (scope === 'current') {
    return `${row.positive_feedback_count || 0}/${row.negative_feedback_count || 0}`;
  }
  return `${row.total_positive_feedback_count || 0}/${row.total_negative_feedback_count || 0}`;
}

function statusText(status: string): string {
  return STATUS_LABELS[status as SkillRead['status']]?.text || status;
}

function skillSourceText(row: SkillVersionRead): string {
  const skill = row.content;
  return [
    `# ${skill.name}`,
    `- skill_id: ${skill.skill_id}`,
    `- version: ${skill.version}`,
    `- business_domain: ${skill.business_domain || '-'}`,
    `- description: ${skill.description || '-'}`,
    `- trigger_intents: ${formatList(skill.trigger_intents)}`,
    `- user_utterance_examples: ${formatList(skill.user_utterance_examples)}`,
    `- goal: ${formatList(skill.goal)}`,
    `- required_info: ${formatList(skill.required_info)}`,
    `- response_rules: ${formatList(skill.response_rules)}`,
    '',
    '## 详细步骤',
    ...skill.steps.flatMap((step, index) => [
      '',
      `### Step ${index + 1}: ${String(step.name || step.step_id || '-')}`,
      `- step_id: ${String(step.step_id || '-')}`,
      `- instruction: ${String(step.instruction || '-')}`,
      `- expected_user_info: ${formatList(step.expected_user_info)}`,
      `- allowed_actions: ${formatList(step.allowed_actions)}`,
    ]),
  ].join('\n');
}

function formatList(value: unknown): string {
  if (!Array.isArray(value) || value.length === 0) return '-';
  return value.map(String).join(', ');
}
