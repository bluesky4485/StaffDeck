const resolveApiBase = () => {
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL;
  }

  return '';
};

export const API_BASE = resolveApiBase();

export const TENANT_ID = import.meta.env.VITE_TENANT_ID || 'tenant_demo';
export const USER_ID = import.meta.env.VITE_USER_ID || 'user_demo';
export const SHOW_DEBUG = import.meta.env.VITE_SHOW_DEBUG === 'true';
const AUTH_STORAGE_KEY = 'ultrarag_auth';
const LEGACY_CHAT_AUTH_STORAGE_KEY = 'skill_agent_auth';
const LEGACY_ENTERPRISE_AUTH_STORAGE_KEY = 'ultrarag_enterprise_auth';

export type AuthUser = {
  id: string;
  tenant_id: string;
  username: string;
  display_name?: string;
};

export type AuthSession = {
  token: string;
  user: AuthUser;
};

export type ChatStreamEvent = {
  event: 'status' | 'stream_delta' | 'stream_end' | 'complete' | 'token' | 'done' | 'error' | string;
  data: Record<string, unknown>;
};

export class ApiError extends Error {
  status: number;
  body: string;

  constructor(status: number, body: string, statusText: string) {
    super(readErrorMessage(body) || statusText || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text, response.statusText);
  }
  return response.json() as Promise<T>;
}

async function keepalivePost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    keepalive: true,
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text, response.statusText);
  }
  const text = await response.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  postKeepalive: <T>(path: string, body?: unknown) => keepalivePost<T>(path, body),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export async function uploadChatAttachments<T>(
  tenantId: string,
  files: File[],
  signal?: AbortSignal,
): Promise<T> {
  const form = new FormData();
  files.forEach((file) => form.append('files', file));
  const response = await fetch(`${API_BASE}/api/chat/attachments?tenant_id=${encodeURIComponent(tenantId)}`, {
    method: 'POST',
    headers: { ...authHeader() },
    body: form,
    signal,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text, response.statusText);
  }
  return response.json() as Promise<T>;
}

export function getAuthSession(): AuthSession | null {
  const current = readStoredSession(AUTH_STORAGE_KEY);
  if (current) return current;

  const legacyEnterprise = readStoredSession(LEGACY_ENTERPRISE_AUTH_STORAGE_KEY);
  const legacyChat = readStoredSession(LEGACY_CHAT_AUTH_STORAGE_KEY);
  const migrated = legacyEnterprise || legacyChat;
  if (migrated) {
    setAuthSession(migrated);
    return migrated;
  }
  return null;
}

export function setAuthSession(session: AuthSession): void {
  window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
  window.localStorage.removeItem(LEGACY_CHAT_AUTH_STORAGE_KEY);
  window.localStorage.removeItem(LEGACY_ENTERPRISE_AUTH_STORAGE_KEY);
}

export function clearAuthSession(): void {
  window.localStorage.removeItem(AUTH_STORAGE_KEY);
  window.localStorage.removeItem(LEGACY_CHAT_AUTH_STORAGE_KEY);
  window.localStorage.removeItem(LEGACY_ENTERPRISE_AUTH_STORAGE_KEY);
}

function readStoredSession(key: string): AuthSession | null {
  const raw = window.localStorage.getItem(key);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as AuthSession;
    if (!parsed.token || !parsed.user?.id) return null;
    return parsed;
  } catch {
    return null;
  }
}

function authHeader(): Record<string, string> {
  const session = getAuthSession();
  return session?.token ? { Authorization: `Bearer ${session.token}` } : {};
}

export async function streamChatTurn(
  body: Record<string, unknown>,
  onEvent: (item: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader() },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text, response.statusText);
  }
  if (!response.body) {
    throw new Error('当前浏览器不支持流式响应');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() || '';
    blocks.forEach((block) => {
      const parsed = parseSseBlock(block);
      if (parsed) onEvent(parsed);
    });
  }

  buffer += decoder.decode();
  const parsed = parseSseBlock(buffer);
  if (parsed) onEvent(parsed);
}

function parseSseBlock(block: string): ChatStreamEvent | null {
  const lines = block.split('\n').map((line) => line.trimEnd());
  const eventLine = lines.find((line) => line.startsWith('event:'));
  const dataLines = lines.filter((line) => line.startsWith('data:'));
  if (!eventLine || dataLines.length === 0) return null;
  const event = eventLine.replace(/^event:\s*/, '');
  const rawData = dataLines.map((line) => line.replace(/^data:\s*/, '')).join('\n');
  try {
    const data = JSON.parse(rawData) as Record<string, unknown>;
    return { event, data };
  } catch {
    return { event, data: { raw: rawData } };
  }
}

function readErrorMessage(body: string): string {
  if (!body) return '';
  try {
    const parsed = JSON.parse(body) as { detail?: unknown; message?: unknown };
    const detail = parsed.detail ?? parsed.message;
    if (typeof detail === 'string') return detail;
    if (detail !== undefined) return JSON.stringify(detail);
  } catch {
    // Response is not JSON; use the raw body below.
  }
  return body;
}
