import {
  DeleteOutlined,
  EditOutlined,
  GlobalOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RightOutlined,
} from '@ant-design/icons';
import { Button, Empty, Input, Modal, Select, Typography, message } from 'antd';
import type { MouseEvent } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, clearAuthSession, getAuthSession, isAuthError } from '../api/client';
import EmployeeAvatarMark from '../components/EmployeeAvatarMark';
import { employeeDisplayName, employeeProfile } from '../employee';
import { ThemeToggleButton } from '../theme';
import type { AgentProfileRead, ChatSession } from '../types';

function SessionChatIcon() {
  return (
    <svg className="session-chat-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 4.2c-4.7 0-8.1 3.05-8.1 7.25 0 2.32 1.02 4.32 2.75 5.65l-.55 2.65 3.05-1.45c.9.26 1.9.4 2.95.4 4.7 0 8.1-3.05 8.1-7.25S16.7 4.2 12 4.2Z" />
      <path d="M8.7 11.45h.04M12 11.45h.04M15.3 11.45h.04" />
    </svg>
  );
}

export default function SessionListPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [sessionAgentFilter, setSessionAgentFilter] = useState('all');
  const [renameSession, setRenameSession] = useState<ChatSession | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const navigate = useNavigate();
  const [auth] = useState(() => getAuthSession());
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => (
    window.localStorage.getItem('skill_agent_sidebar_collapsed') === 'true'
  ));
  const tenantId = auth?.user.tenant_id || 'tenant_demo';

  const load = () =>
    Promise.all([
      api.get<ChatSession[]>(`/api/chat/sessions?tenant_id=${tenantId}`),
      api.get<AgentProfileRead[]>(`/api/enterprise/agents?tenant_id=${tenantId}`),
    ])
      .then(([sessionRows, agentRows]) => {
        setSessions(sessionRows);
        setAgents(agentRows);
      })
      .catch((error) => {
        if (isAuthError(error)) {
          clearAuthSession();
          navigate('/login', { replace: true });
          return;
        }
        message.error(error.message);
      });

  const sessionFilterOptions = useMemo(() => {
    const counts = new Map<string, number>();
    sessions.forEach((session) => {
      if (!session.agent_id) return;
      counts.set(session.agent_id, (counts.get(session.agent_id) || 0) + 1);
    });
    const rows = Array.from(counts.keys())
      .map((agentId) => agents.find((agent) => agent.id === agentId))
      .filter((agent): agent is AgentProfileRead => Boolean(agent))
      .sort((a, b) => employeeDisplayName(a).localeCompare(employeeDisplayName(b), 'zh-Hans-CN'));
    return [
      { value: 'all', label: `全部员工 · ${sessions.length}` },
      ...rows.map((agent) => ({
        value: agent.id,
        label: `${employeeDisplayName(agent)} · ${counts.get(agent.id) || 0}`,
      })),
    ];
  }, [agents, sessions]);

  const visibleSessions = useMemo(() => (
    sessionAgentFilter === 'all'
      ? sessions
      : sessions.filter((session) => session.agent_id === sessionAgentFilter)
  ), [sessionAgentFilter, sessions]);

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (sessionAgentFilter === 'all') return;
    if (!sessionFilterOptions.some((item) => item.value === sessionAgentFilter)) {
      setSessionAgentFilter('all');
    }
  }, [sessionAgentFilter, sessionFilterOptions]);

  function toggleSidebar() {
    setSidebarCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem('skill_agent_sidebar_collapsed', String(next));
      return next;
    });
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
        await api.delete(`/api/chat/sessions/${target.id}?tenant_id=${tenantId}`);
        setSessions((items) => items.filter((item) => item.id !== target.id));
        message.success('已删除');
      },
    });
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
        {!sidebarCollapsed && (
          <button type="button" className="sidebar-gallery-entry" onClick={() => navigate('/employees')}>
            <span className="sidebar-gallery-entry-icon"><GlobalOutlined /></span>
            <span className="sidebar-gallery-entry-copy">
              <strong>员工广场</strong>
              <span>选择接单员工</span>
            </span>
            <RightOutlined />
          </button>
        )}
        <div className="session-list-scroll">
          {!sidebarCollapsed && (
            <div className="session-filter-bar">
              <span className="session-filter-label">员工会话</span>
              <Select
                size="small"
                className="session-filter-select"
                value={sessionAgentFilter}
                options={sessionFilterOptions}
                onChange={setSessionAgentFilter}
              />
            </div>
          )}
          <div className="session-section-label">任务记录</div>
          {visibleSessions.length === 0 ? (
            <div className="session-list-empty">
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前员工暂无任务记录" />
            </div>
          ) : (
            visibleSessions.map((session) => {
              const sessionTitle = session.title || session.id;
              const sessionSummary = session.summary || session.last_agent_question || '新任务';
              const sessionAgent = session.agent_id ? agents.find((agent) => agent.id === session.agent_id) || null : null;
              const sessionProfile = sessionAgent ? employeeProfile(sessionAgent) : null;
              const sessionAgentFallback = sessionAgent ? employeeDisplayName(sessionAgent).slice(0, 1) : '员';
              return (
                <div
                  key={session.id}
                  role="button"
                  tabIndex={0}
                  className="session-card"
                  onClick={() => navigate(`/${session.id}`)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      navigate(`/${session.id}`);
                    }
                  }}
                >
                  <div className="session-card-content">
                    <span className="session-title-icon session-title-avatar">
                      {sessionProfile ? (
                        <EmployeeAvatarMark
                          profile={sessionProfile}
                          fallback={sessionAgentFallback || '员'}
                          className="session-agent-avatar"
                        />
                      ) : (
                        <SessionChatIcon />
                      )}
                    </span>
                    <div className="session-meta">
                      <div className="session-title" title={sessionTitle}>
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
            })
          )}
        </div>
      </aside>
      <main className="chat-main">
        <div className="chat-header">
          <div>
            <Typography.Text strong>任务派发台</Typography.Text>
            <div className="header-subtitle">选择历史任务或派发新任务</div>
          </div>
          <div className="chat-header-actions">
            <ThemeToggleButton />
          </div>
        </div>
        <div className="chat-messages">
          <div className="chat-empty-state">
            <span className="chat-empty-mark"><GlobalOutlined /></span>
            <Typography.Title level={3}>从员工广场派发任务</Typography.Title>
            <Typography.Paragraph>
              进入员工广场选择接单员工，即可创建任务会话。
            </Typography.Paragraph>
          </div>
        </div>
      </main>
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
