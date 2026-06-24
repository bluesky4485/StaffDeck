import { IdcardOutlined } from '@ant-design/icons';
import { Form, Input, Modal, Select, Switch, Typography, message } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { api, TENANT_ID } from '../api/client';
import type { EnterpriseAuthUser } from '../auth';
import { employeeDisplayName, employeeProfile } from '../employee';
import type { AgentProfileRead } from '../types';
import EmployeeAvatar from './EmployeeAvatar';

type EmployeeProfileFormValues = {
  name: string;
  roleName: string;
  onboardedAt: string;
  description: string;
  personaPrompt: string;
  systemPromptSummary: string;
  workStyles: string[];
  expertiseTags: string[];
  workModes: string[];
  status: 'active' | 'archived';
  publishedToGallery: boolean;
};

const STYLE_OPTIONS = ['目标明确', '证据优先', '动作可追溯', '事实先行', '流程推进', '风险克制', '及时追问'];
const EXPERTISE_OPTIONS = ['业务问答', 'SOP 执行', '工具调用', '用户接待', '商品比价', '售后处理', '资料维护'];
const WORK_MODE_OPTIONS = ['识别意图', '补齐信息', '调用 SOP', '查询资料', '执行并复盘', '确认后执行', '必要时转人工'];

export default function EmployeeProfileEditor({
  agent,
  open,
  onClose,
  onSaved,
  currentUser,
}: {
  agent?: AgentProfileRead | null;
  open: boolean;
  onClose: () => void;
  onSaved?: (agent: AgentProfileRead) => void;
  currentUser?: EnterpriseAuthUser;
}) {
  const [form] = Form.useForm<EmployeeProfileFormValues>();
  const [saving, setSaving] = useState(false);
  const profile = useMemo(() => employeeProfile(agent), [agent]);

  useEffect(() => {
    if (!open || !agent) return;
    form.setFieldsValue({
      name: employeeDisplayName(agent),
      roleName: profile.roleName === '待补充岗位' ? '' : profile.roleName,
      onboardedAt: profile.onboardedAt === '-' ? new Date().toISOString().slice(0, 10) : profile.onboardedAt,
      description: agent.description || '',
      personaPrompt: agent.persona_prompt || '',
      systemPromptSummary: typeof agent.metadata?.system_prompt_summary === 'string' ? agent.metadata.system_prompt_summary : '',
      workStyles: profile.workStyles,
      expertiseTags: profile.expertiseTags,
      workModes: profile.workModes,
      status: agent.status === 'archived' ? 'archived' : 'active',
      publishedToGallery: agent.metadata?.published_to_gallery === true,
    });
  }, [agent, form, open, profile]);

  async function save() {
    if (!agent) return;
    try {
      const values = await form.validateFields();
      setSaving(true);
      const wasPublished = agent.metadata?.published_to_gallery === true;
      const metadata: Record<string, unknown> = {
        ...(agent.metadata || {}),
        blank_onboarding: false,
        role_name: values.roleName.trim() || '待补充岗位',
        onboarded_at: values.onboardedAt || new Date().toISOString().slice(0, 10),
        system_prompt_summary: values.systemPromptSummary.trim(),
        work_styles: compactTags(values.workStyles),
        expertise_tags: compactTags(values.expertiseTags),
        work_modes: compactTags(values.workModes),
        published_to_gallery: values.publishedToGallery,
      };
      if (values.publishedToGallery && !wasPublished) {
        metadata.gallery_published_at = new Date().toISOString();
        metadata.gallery_published_by = currentUser?.username;
      }
      if (!values.publishedToGallery) {
        delete metadata.gallery_published_at;
        delete metadata.gallery_published_by;
      }

      const saved = await api.put<AgentProfileRead>(`/api/enterprise/agents/${agent.id}`, {
        tenant_id: TENANT_ID,
        name: values.name.trim(),
        description: values.description.trim(),
        persona_prompt: values.personaPrompt.trim(),
        status: values.status,
        metadata,
      });
      message.success('员工信息已更新');
      onSaved?.(saved);
      onClose();
      window.dispatchEvent(new Event('ultrarag-enterprise-agent-scope-refresh'));
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return;
      message.error(error instanceof Error ? error.message : '保存员工信息失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      className="employee-profile-modal"
      title={agent ? `编辑员工信息：${employeeDisplayName(agent)}` : '编辑员工信息'}
      open={open}
      onCancel={onClose}
      onOk={() => void save()}
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
      width={860}
      destroyOnClose
    >
      <div className="employee-profile-editor">
        <div className="employee-profile-preview">
          <EmployeeAvatar agent={agent} size={92} />
          <div>
            <Typography.Text type="secondary">员工入职资料</Typography.Text>
            <Typography.Title level={4}>{agent ? employeeDisplayName(agent) : '数字员工'}</Typography.Title>
            <Typography.Paragraph type="secondary">
              这里会同步影响员工信息页、员工名册和任务派发台里的员工展示。
            </Typography.Paragraph>
          </div>
          <span className="employee-profile-preview-icon"><IdcardOutlined /></span>
        </div>

        <Form form={form} layout="vertical" className="employee-profile-form">
          <div className="employee-profile-form-grid">
            <Form.Item name="name" label="员工姓名" rules={[{ required: true, message: '请输入员工姓名' }]}>
              <Input placeholder="例如：默认员工" />
            </Form.Item>
            <Form.Item name="roleName" label="岗位">
              <Input placeholder="例如：在线客服员工" />
            </Form.Item>
            <Form.Item name="onboardedAt" label="入职时间">
              <Input type="date" />
            </Form.Item>
            <Form.Item name="status" label="工作状态">
              <Select
                options={[
                  { value: 'active', label: '在线' },
                  { value: 'archived', label: '下线' },
                ]}
              />
            </Form.Item>
          </div>

          <Form.Item name="description" label="岗位人设摘要">
            <Input.TextArea rows={3} placeholder="概括这个员工的岗位边界、服务风格和执行重点" />
          </Form.Item>
          <Form.Item name="systemPromptSummary" label="看板摘要">
            <Input.TextArea rows={2} placeholder="用于员工信息页顶部展示的 system prompt 摘要" />
          </Form.Item>
          <Form.Item name="personaPrompt" label="岗位执行约束">
            <Input.TextArea rows={4} placeholder="员工在对话中的角色、人设、回复风格和执行边界" />
          </Form.Item>

          <div className="employee-profile-form-grid is-tags">
            <Form.Item name="expertiseTags" label="掌握方向">
              <Select mode="tags" tokenSeparators={[',', '，']} options={EXPERTISE_OPTIONS.map((item) => ({ value: item }))} />
            </Form.Item>
            <Form.Item name="workStyles" label="工作风格">
              <Select mode="tags" tokenSeparators={[',', '，']} options={STYLE_OPTIONS.map((item) => ({ value: item }))} />
            </Form.Item>
            <Form.Item name="workModes" label="工作模式">
              <Select mode="tags" tokenSeparators={[',', '，']} options={WORK_MODE_OPTIONS.map((item) => ({ value: item }))} />
            </Form.Item>
          </div>

          <div className="employee-profile-publish">
            <div>
              <Typography.Text strong>发布到员工广场</Typography.Text>
              <Typography.Paragraph type="secondary">
                开启后，其他员工账号可以在任务派发台和员工广场中选择这个员工。
              </Typography.Paragraph>
            </div>
            <Form.Item name="publishedToGallery" valuePropName="checked" noStyle>
              <Switch checkedChildren="开放" unCheckedChildren="关闭" />
            </Form.Item>
          </div>
        </Form>
      </div>
    </Modal>
  );
}

function compactTags(values: string[] | undefined): string[] {
  return Array.from(new Set((values || []).map((item) => item.trim()).filter(Boolean))).slice(0, 12);
}
