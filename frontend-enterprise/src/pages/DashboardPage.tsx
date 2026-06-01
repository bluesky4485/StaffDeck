import { ApiOutlined, MessageOutlined, ProfileOutlined, ToolOutlined } from '@ant-design/icons';
import { Card, Col, Row, Statistic, Typography, message } from 'antd';
import { useEffect, useState } from 'react';
import { api, TENANT_ID } from '../api/client';
import type { ModelConfigRead, SkillRead, ToolRead } from '../types';

export default function DashboardPage() {
  const [skills, setSkills] = useState<SkillRead[]>([]);
  const [models, setModels] = useState<ModelConfigRead[]>([]);
  const [tools, setTools] = useState<ToolRead[]>([]);

  useEffect(() => {
    Promise.all([
      api.get<SkillRead[]>(`/api/enterprise/skills?tenant_id=${TENANT_ID}`),
      api.get<ModelConfigRead[]>(`/api/enterprise/model-configs?tenant_id=${TENANT_ID}`),
      api.get<ToolRead[]>(`/api/enterprise/tools?tenant_id=${TENANT_ID}`),
    ])
      .then(([skillRows, modelRows, toolRows]) => {
        setSkills(skillRows);
        setModels(modelRows);
        setTools(toolRows);
      })
      .catch((error) => message.error(error.message));
  }, []);

  const defaultModel = models.find((item) => item.is_default);
  const totalCalls = skills.reduce((sum, item) => sum + (item.total_call_count || 0), 0);
  const positiveFeedback = skills.reduce((sum, item) => sum + (item.total_positive_feedback_count || 0), 0);
  const negativeFeedback = skills.reduce((sum, item) => sum + (item.total_negative_feedback_count || 0), 0);
  const positiveRate = totalCalls ? positiveFeedback / totalCalls : 0;
  const negativeRate = totalCalls ? negativeFeedback / totalCalls : 0;
  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>Dashboard</Typography.Title>
      </div>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8} xl={6}>
          <Card>
            <Statistic title="已发布技能" value={skills.filter((item) => item.status === 'published').length} prefix={<ProfileOutlined />} />
          </Card>
        </Col>
        <Col xs={24} md={8} xl={6}>
          <Card>
            <Statistic title="草稿技能" value={skills.filter((item) => item.status === 'draft').length} prefix={<ProfileOutlined />} />
          </Card>
        </Col>
        <Col xs={24} md={8} xl={6}>
          <Card>
            <Statistic title="已启用工具" value={tools.filter((item) => item.enabled).length} prefix={<ToolOutlined />} />
          </Card>
        </Col>
        <Col xs={24} md={8} xl={6}>
          <Card>
            <Statistic title="模型配置" value={models.length} prefix={<MessageOutlined />} />
          </Card>
        </Col>
        <Col xs={24} md={8} xl={6}>
          <Card>
            <Statistic title="总调用次数" value={totalCalls} prefix={<ApiOutlined />} />
          </Card>
        </Col>
        <Col xs={24} md={8} xl={6}>
          <Card>
            <Statistic title="总好评率" value={Math.round(positiveRate * 100)} suffix="%" />
          </Card>
        </Col>
        <Col xs={24} md={8} xl={6}>
          <Card>
            <Statistic title="总差评率" value={Math.round(negativeRate * 100)} suffix="%" />
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card title="默认模型">
            <Typography.Text>
              {defaultModel ? `${defaultModel.name} / ${defaultModel.model}` : '未配置'}
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card title="技能总数">
            <Statistic value={skills.length} prefix={<ApiOutlined />} />
          </Card>
        </Col>
      </Row>
    </>
  );
}
