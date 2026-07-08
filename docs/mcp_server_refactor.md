# MCP 工具集重构（Server → 自动发现 tools）

> 分支：`fix_mcp_connect`　｜　目标：把 MCP 配置从「HTTP 化的重配置」重构成标准 MCP Client 的「配 Server → tools/list 自动发现工具」模型。

## 1. 背景与问题

重构前，MCP 工具与 HTTP 工具共用同一张 `Tool` 表，配置界面把 HTTP 概念强行套在 MCP 上：

- MCP 类型下仍要填 `Method`（GET/POST）、`URL`，但对 MCP 客户端根本不生效（被改名成「Method 标记 / MCP URL 标记」的死字段）。
- `mcp_config` 是一个裸 JSON textarea，用户要手写 `{"transport": "stdio", "command": ..., "tool": ...}`。
- `Input Schema` / `Output Schema` 要人工填写，而标准 MCP 应通过 `tools/list` 自动发现。
- 底层 `mcp_client.py` 只实现了 `tools/call`，没有 `tools/list`；SSE transport 未实现。

标准 MCP Client（如 Cursor）的心智：**只配 Server 连接方式（stdio / streamable_http / sse）+ URL/命令 + Headers**，连上后自动 `tools/list` 发现工具，元信息由 Server 提供。

## 2. 设计决策（已与需求方确认）

| 决策点 | 选择 |
|---|---|
| 数据模型 | **新增 `MCPServer` 表**，`Tool` 通过 `mcp_server_id` 关联到 Server |
| 支持的 transport | **stdio + streamable_http + sse 全支持**（补齐 SSE + tools/list 发现） |
| 发现后工具存储 | **落成 Tool 行**（`tool_type=mcp`，input/output schema 自动填），与现有 skills/权限/分桶/测试页兼容 |
| 同步时机 | **手动点击「发现 / 同步」**，可选择性导入子集，可预览 |
| 管理入口 | 仍在工具页；MCP Server 呈现为「工具集」——同表内**可展开的父/子行** |
| 子工具粒度 | 子工具可单独启用 / 配 allowed_skills / 分桶（沿用现有 Tool 规则） |

## 3. 架构：一个 MCP Server = 一个「工具集」

```
MCPServer（连接配置：transport / url / headers / command / args / env / cwd）
   └── tools/list 发现
         ├── Tool 行（tool_type=mcp, mcp_server_id=<server>, config_json={"tool": "<leaf>"}）
         ├── Tool 行 ...
         └── ...
```

- **连接配置只存在 Server 上**；MCP Tool 行的 `config_json` 只放 `{"tool": <leaf name>}`。
- MCP Tool 行的 `method`/`url` 是占位值（`method=POST`，`url=mcp://<server>/<leaf>`），因为 `Tool.method`/`Tool.url` 是非空列；执行时不使用它们。
- 工具命名：`<server_name>.<leaf_tool_name>`（如 `modelscope_time.get_current_time`）。

## 4. 后端改动

### 4.1 `app/tools/mcp_client.py`（核心重写）

- 抽出 `_MCPSession` 会话基类：一次连接内复用 `initialize` → `tools/call` / `tools/list`。
- 三个 transport 会话实现：`_StdioSession`、`_HttpSession`（streamable_http）、`_SseSession`。
- 新增 **`list_mcp_tools(config, timeout)`**：通过 `tools/list` 发现工具，返回标准化 `[{name, description, input_schema, output_schema}]`。
- `execute_mcp_tool(config, arguments, timeout, tool_name=None)`：新增 `tool_name` 参数，支持外部显式指定 leaf 工具名。
- **SSE transport 实现**（MCP 2024-11-05 HTTP+SSE）：
  - GET server url 建立 SSE 流 → 读首个 `event: endpoint` 拿消息端点。
  - 后续 JSON-RPC 请求 POST 到消息端点，响应从 SSE 流按 `id` 匹配返回。
  - `_iter_sse_events` 逐事件解析 `event:` / `data:`。
- `normalize_transport`：`streamable_http` 归一化为内部 `http`；兼容历史 `server`/`command`/`url` 推断。

### 4.2 `app/tools/mcp_builtin.py`

- 新增 `builtin_mcp_tool_definitions(config)`：为内置 `builtin.demo` server 提供 `tools/list` 的工具定义（echo / sum / product_lookup）。

### 4.3 `app/db/models.py`

- 新增 **`MCPServer`** 表：
  - `id / tenant_id / name / display_name / description / bucket`
  - `transport`（stdio / streamable_http / sse / builtin）
  - `url / headers_json`（http/sse 用）
  - `command / args_json / env_json / cwd`（stdio 用）
  - `discovered_tools_json`（最近一次发现的原始工具定义，预览/审计）
  - `last_synced_at / enabled / created_at / updated_at`
  - 唯一约束：`(tenant_id, name)`
- `Tool` 新增 `mcp_server_id: Optional[str]`（index）。

### 4.4 `app/db/database.py`

- sqlite 迁移：`tools` 表补 `mcp_server_id VARCHAR` 列（`mcp_servers` 新表由 `create_all` 自动创建）。

### 4.5 `app/tools/tool_schema.py`

- `ToolRead` 增加 `mcp_server_id`。
- 新增：`MCPServerConnection`、`MCPServerCreateRequest`、`MCPServerUpdateRequest`、`MCPServerRead`、`MCPDiscoveredTool`、`MCPDiscoverRequest`、`MCPDiscoverResponse`、`MCPSyncRequest`、`MCPSyncResponse`。

### 4.6 `app/api/tools.py`（新增 `mcp_router`）

前缀 `/api/enterprise/mcp-servers`：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `""` | 列出 MCP servers（含 tool_count） |
| POST | `""` | 创建 MCP server |
| GET | `/{server_id}` | 查询单个 |
| PUT | `/{server_id}` | 更新连接配置 |
| DELETE | `/{server_id}?remove_tools=true` | 删除 server（可级联删子工具） |
| POST | `/discover` | 未保存时用连接配置直接探测 `tools/list` |
| POST | `/{server_id}/discover` | 已保存 server：拉取 tools/list，并标注哪些已导入 |
| POST | `/{server_id}/sync` | 把发现的工具落成 Tool 行（新建/更新 schema），可选子集 |

- `_connection_to_client_config` / `_server_client_config`：结构化连接配置 ↔ mcp_client 扁平 config。
- `mcp_router` 已在 `app/main.py` 注册。

### 4.7 `app/tools/tool_executor.py`

- `_execute_mcp_tool` → `_resolve_mcp_config(tool)`：
  - 新模型：`tool.mcp_server_id` 关联 `MCPServer`，连接配置从 server 读取，`config_json` 只提供 leaf tool 名。
  - 旧模型：`config_json` 自带完整连接配置（兼容历史数据）。

### 4.8 `app/db/seed.py`

- MCP demo 数据改为 server → tools 结构：
  - `MCP_BUILTIN_DEMO_SERVER`（transport=builtin）
  - `MCP_STDIO_DEMO_SERVER`（transport=stdio，指向 `mock_servers/mcp_stdio_server.py`）
  - `MCP_SERVER_TOOLS`：每个 server 预落地的子工具。
- 新增 `_seed_mcp_servers()`；`DEMO_TOOLS` 移除原两个内联 MCP 工具。

### 4.9 测试

- 新增 `backend/tests/test_mcp_servers_api.py`：discover（builtin/stdio）、sync 导入+执行、幂等更新、已保存 server 标注 imported、删除级联。

## 5. 前端改动（frontend-enterprise）

### 5.1 `src/types/index.ts`

- `ToolRead` 增加 `mcp_server_id`。
- 新增 `MCPTransport`、`MCPServerConnection`、`MCPServerRead`、`MCPDiscoveredTool`、`MCPDiscoverResponse`、`MCPSyncResponse`。

### 5.2 `src/App.tsx`

- 新增路由：`/enterprise/tools/mcp/new`、`/enterprise/tools/mcp/:serverId/edit`。

### 5.3 `src/pages/ToolsPage.tsx`

- **列表**：同时拉 tools + mcp-servers，合成树。MCP Server 是可展开父行（显示 transport / 端点 / 工具数），展开是子工具；HTTP 工具是扁平行。
- **新增下拉**：加「添加 MCP 服务器（工具集）」。
- **MCP Server 编辑页** `McpServerEditorPage`：
  - 结构化连接表单：transport 下拉，按类型显示 URL+Headers 或 命令+args+env+cwd。
  - 「发现工具」按钮 → `discover` → 列出工具 + 勾选。
  - 「导入 / 同步」按钮 → `sync` → 落成 Tool 行。
- `ToolFormFields`：移除 MCP 分支的死字段（Method 标记 / MCP URL 标记 / 手写 mcp_config / 手填 schema）；MCP 子工具在编辑页为只读提示（连接与元信息由 Server 管理）。

## 6. 真机验证结果

用真实 ModelScope SSE MCP server 验证（`https://mcp.api-inference.modelscope.net/167a0ab52ced4b/sse`，Header `Authorization: Bearer <token>`）：

- ✅ SSE `tools/list` 发现 → `get_current_time` / `convert_time` + input schema
- ✅ SSE `tools/call` 调用（含 header 鉴权）→ 返回真实结果
- ✅ 完整产品链路：create server → discover → sync 落成 Tool → `ToolExecutor.execute` 执行成功
- ✅ 对话链路：`_list_enabled_tools` → `_step_agent_tools`（enabled + allowed_skills 作用域）→ StepAgent `available_tools` 载荷，MCP 工具与 HTTP 工具一视同仁地暴露给 LLM
- ✅ 后端 227 测试全过、ruff clean、前端 `tsc -b` + `vite build` 通过

## 7. 注意事项 / 后续

- **超时**：远程 SSE/HTTP MCP 建议 `TOOL_TIMEOUT_SECONDS` 调到 15~30（默认 8 偏紧）。
- **对话可见性**：新导入 MCP 工具默认 `allowed_skills=[]`（对所有技能可见）；若限定到技能需配 `allowed_skills`。
- **SSE 集成测试**：SSE 依赖外网真实 server，未纳入 CI；可加「设置 token 环境变量才跑」的可选集成测试。
- 涉及文件清单：
  - 后端：`app/api/tools.py`、`app/db/database.py`、`app/db/models.py`、`app/db/seed.py`、`app/main.py`、`app/tools/mcp_builtin.py`、`app/tools/mcp_client.py`、`app/tools/tool_executor.py`、`app/tools/tool_schema.py`、`tests/test_mcp_servers_api.py`(新增)
  - 前端：`src/App.tsx`、`src/pages/ToolsPage.tsx`、`src/types/index.ts`
