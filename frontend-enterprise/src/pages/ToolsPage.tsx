import { DeleteOutlined, ExperimentOutlined, SaveOutlined, ToolOutlined } from '@ant-design/icons';
import { Button, Card, Form, Input, Modal, Select, Space, Switch, Table, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useState } from 'react';
import { api, TENANT_ID } from '../api/client';
import type { ToolRead } from '../types';

export default function ToolsPage() {
  const [rows, setRows] = useState<ToolRead[]>([]);
  const [selected, setSelected] = useState<ToolRead | null>(null);
  const [testToolId, setTestToolId] = useState<string | undefined>();
  const [form] = Form.useForm();
  const [testJson, setTestJson] = useState('{}');
  const [testResult, setTestResult] = useState('');

  const load = () =>
    api
      .get<ToolRead[]>(`/api/enterprise/tools?tenant_id=${TENANT_ID}`)
      .then(setRows)
      .catch((error) => message.error(error.message));

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!rows.length) {
      setTestToolId(undefined);
      return;
    }
    if (!testToolId || !rows.some((row) => row.id === testToolId)) {
      const [first] = rows;
      setTestToolId(first.id);
      setTestJson(JSON.stringify(exampleFromSchema(first.input_schema), null, 2));
    }
  }, [rows, testToolId]);

  function edit(row: ToolRead) {
    setSelected(row);
    setTestToolId(row.id);
    form.setFieldsValue({
      ...row,
      headers: JSON.stringify(row.headers, null, 2),
      auth: JSON.stringify(row.auth, null, 2),
      input_schema: JSON.stringify(row.input_schema, null, 2),
      output_schema: JSON.stringify(row.output_schema, null, 2),
      allowed_skills: row.allowed_skills.join(','),
    });
    setTestJson(JSON.stringify(exampleFromSchema(row.input_schema), null, 2));
    setTestResult('');
  }

  function clearEditor() {
    setSelected(null);
    form.resetFields();
  }

  async function save() {
    const values = await form.validateFields();
    const payload = {
      tenant_id: TENANT_ID,
      name: values.name,
      display_name: values.display_name,
      description: values.description,
      method: values.method,
      url: values.url,
      headers: parseJson(values.headers, {}),
      auth: parseJson(values.auth, {}),
      input_schema: parseJson(values.input_schema, {}),
      output_schema: parseJson(values.output_schema, {}),
      allowed_skills: String(values.allowed_skills || '').split(',').map((item) => item.trim()).filter(Boolean),
      enabled: values.enabled,
    };
    if (selected) {
      await api.put(`/api/enterprise/tools/${selected.id}`, payload);
    } else {
      await api.post('/api/enterprise/tools', payload);
    }
    message.success('已保存');
    clearEditor();
    load();
  }

  async function remove(row: ToolRead) {
    Modal.confirm({
      title: '删除工具？',
      content: `确认删除「${row.display_name || row.name}」？删除后，引用该工具的技能将无法继续调用它。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        await api.delete(`/api/enterprise/tools/${row.id}?tenant_id=${TENANT_ID}`);
        if (selected?.id === row.id) clearEditor();
        if (testToolId === row.id) {
          setTestToolId(undefined);
          setTestJson('{}');
          setTestResult('');
        }
        message.success('已删除');
        load();
      },
    });
  }

  function selectTestTool(toolId: string) {
    const row = rows.find((item) => item.id === toolId);
    setTestToolId(toolId);
    setTestJson(JSON.stringify(exampleFromSchema(row?.input_schema || {}), null, 2));
    setTestResult('');
  }

  async function test(row?: ToolRead) {
    const target = row || rows.find((item) => item.id === testToolId) || selected;
    if (!target) {
      message.warning('请先选择工具');
      return;
    }
    if (row) {
      setTestToolId(row.id);
      setTestJson(JSON.stringify(exampleFromSchema(row.input_schema), null, 2));
    }
    const argumentsJson = row ? exampleFromSchema(row.input_schema) : parseJson(testJson, {});
    const result = await api.post(`/api/enterprise/tools/${target.id}/test`, {
      tenant_id: TENANT_ID,
      arguments: argumentsJson,
    });
    setTestResult(JSON.stringify(result, null, 2));
  }

  const columns: ColumnsType<ToolRead> = [
    { title: '工具名称', dataIndex: 'name', width: 170, ellipsis: true },
    { title: '展示名称', dataIndex: 'display_name', width: 160, ellipsis: true },
    { title: 'Method', dataIndex: 'method', width: 96 },
    { title: 'URL', dataIndex: 'url', width: 280, ellipsis: true },
    { title: '启用', dataIndex: 'enabled', width: 80, render: (value) => (value ? '是' : '否') },
    {
      title: '操作',
      width: 244,
      render: (_, row) => (
        <span className="table-actions">
          <Button size="small" onClick={() => edit(row)}>编辑</Button>
          <Button size="small" icon={<ExperimentOutlined />} onClick={() => test(row)}>测试</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => void remove(row)}>删除</Button>
        </span>
      ),
    },
  ];

  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>工具配置</Typography.Title>
      </div>
      <div className="grid-2">
        <Card className="data-card" title="工具列表">
          <Table
            rowKey="id"
            columns={columns}
            dataSource={rows}
            pagination={{ pageSize: 8 }}
            scroll={{ x: 946 }}
            size="middle"
          />
        </Card>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Card
            className="editor-card"
            title={selected ? '编辑工具' : '新建工具'}
            extra={(
              <Space className="card-header-actions">
                <Button onClick={clearEditor}>清空</Button>
                <Button type="primary" icon={<SaveOutlined />} onClick={save}>保存</Button>
              </Space>
            )}
          >
            <Form form={form} layout="vertical" initialValues={{ method: 'POST', enabled: true, headers: '{}', auth: '{}', input_schema: '{}', output_schema: '{}' }}>
              <Form.Item name="name" label="工具名称" rules={[{ required: true }]}><Input prefix={<ToolOutlined />} /></Form.Item>
              <Form.Item name="display_name" label="展示名称"><Input /></Form.Item>
              <Form.Item name="description" label="描述"><Input.TextArea rows={2} /></Form.Item>
              <Form.Item name="method" label="HTTP Method"><Select options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((value) => ({ value, label: value }))} /></Form.Item>
              <Form.Item name="url" label="URL" rules={[{ required: true }]}><Input /></Form.Item>
              <Form.Item name="headers" label="Headers JSON"><Input.TextArea rows={4} /></Form.Item>
              <Form.Item name="auth" label="Auth JSON"><Input.TextArea rows={3} /></Form.Item>
              <Form.Item name="input_schema" label="Input Schema"><Input.TextArea rows={5} /></Form.Item>
              <Form.Item name="output_schema" label="Output Schema"><Input.TextArea rows={5} /></Form.Item>
              <Form.Item name="allowed_skills" label="Allowed Skills"><Input placeholder="after_sales_refund,after_sales_exchange" /></Form.Item>
              <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
            </Form>
          </Card>
          <Card
            className="editor-card"
            title="工具测试"
            extra={(
              <Space className="card-header-actions">
                <Select
                  value={testToolId}
                  placeholder="选择工具"
                  style={{ width: 220 }}
                  options={rows.map((row) => ({
                    value: row.id,
                    label: row.display_name ? `${row.display_name} / ${row.name}` : row.name,
                  }))}
                  onChange={selectTestTool}
                />
                <Button icon={<ExperimentOutlined />} onClick={() => test()}>调用</Button>
              </Space>
            )}
          >
            <Input.TextArea rows={4} value={testJson} onChange={(event) => setTestJson(event.target.value)} />
            <Input.TextArea rows={8} value={testResult} readOnly style={{ marginTop: 12 }} />
          </Card>
        </Space>
      </div>
    </>
  );
}

function parseJson<T>(value: string, fallback: T): T {
  if (!value) return fallback;
  return JSON.parse(value) as T;
}

function exampleFromSchema(schema: Record<string, unknown>): Record<string, unknown> {
  const properties = schema.properties && typeof schema.properties === 'object'
    ? schema.properties as Record<string, Record<string, unknown>>
    : {};
  return Object.fromEntries(
    Object.entries(properties).map(([key, value]) => [key, exampleValue(key, value)]),
  );
}

function exampleValue(key: string, schema: Record<string, unknown>): unknown {
  if (schema.default !== undefined) return schema.default;
  if (schema.example !== undefined) return schema.example;
  if (Array.isArray(schema.enum) && schema.enum.length > 0) return schema.enum[0];
  if (schema.type === 'integer') return 1;
  if (schema.type === 'number') return 1;
  if (schema.type === 'boolean') return true;
  if (schema.type === 'array') return [];
  if (schema.type === 'object') return {};
  return `sample_${key}`;
}
