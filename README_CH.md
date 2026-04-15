# open_deepagents

`open_deepagents` 是一个用于构建 DeepAgents Web 工作台的全栈脚手架。

它定位为可扩展的工程基础，而不是开箱即用的成品平台。仓库已经具备本地端到端运行能力，覆盖管理员鉴权、会话管理、附件上传、运行流式输出，以及 DeepAgents 运行时集成，同时保留了较清晰的扩展边界。

English version: [README.md](README.md)

## 架构概览

仓库目前分成两条主线：

- `backend/`：基于 FastAPI 的后端服务，负责鉴权、持久化、上传、运行编排和 DeepAgents 运行时集成。
- `frontend/`：基于 Vue 3 + Vite 的控制台，负责登录、会话切换、聊天界面、附件上传、流式事件展示和运行时间线。

当前请求链路如下：

1. 管理员从前端登录。
2. 前端保存 Bearer Token，并调用后端 API。
3. 用户创建或选择一个会话。
4. 用户上传附件并提交 prompt。
5. 后端创建 run，写入用户消息，并启动 DeepAgents 运行时。
6. 运行时事件被桥接为前端可消费的 SSE 信封格式。
7. 前端根据流式事件更新聊天区和运行时间线。

更完整的目录边界说明见 [docs/repository-architecture.md](docs/repository-architecture.md)。

## 仓库结构

```text
.
├── backend/
│   ├── app/                          FastAPI 应用代码
│   ├── deepagents_integration/       DeepAgents 运行时桥接层
│   ├── extensions/                   工具 / 中间件 / 运行时钩子 / 技能 / 沙箱模板
│   ├── prompts/                      项目内维护的运行时提示词
│   └── tests/                        后端测试
├── frontend/
│   └── src/
│       ├── components/               Vue UI 组件
│       ├── lib/copy.js               前端用户可见文案集中配置
│       ├── store/                    客户端状态容器
│       └── api/                      后端 HTTP/SSE 客户端
├── packages/
│   ├── contracts/                    共享契约
│   └── extension-manifest.template.json
├── docs/                             架构与维护文档
├── tests/                            仓库级测试
└── verification/                     审计与契约校验
```

## 功能概览

### 后端

- FastAPI 应用工厂与 `/health` 健康检查
- 单管理员 Bearer Token 鉴权
- 会话、消息、上传的 CRUD
- DeepAgents run 创建、运行时事件桥接与 SSE 流输出
- 本地文件上传存储
- 可配置的工具、中间件、运行时钩子、技能、记忆和沙箱后端
- 通过环境变量控制 DeepAgents 内置工具 allowlist / blocklist
- 以 SQLite 为默认本地方案，并兼容 MySQL 部署模型

### 前端

- 管理员登录页
- 会话列表与切换
- 支持附件上传的聊天工作区
- Markdown 与 Mermaid 渲染
- 基于 SSE 的运行状态流
- 运行步骤、工具、技能、沙箱事件时间线
- `frontend/src/lib/copy.js` 集中管理常用 UI 文案，便于轻量定制

### 工程支持

- 共享 SSE 契约定义
- 仓库结构审计检查
- 后端、前端与契约相关测试
- 扩展模板和架构说明文档

## 环境要求

- Python `3.11` 或 `3.12`
- [uv](https://docs.astral.sh/uv/) 用于后端依赖管理
- Node.js `18+`
- npm
- 一个可用的模型来源：
  - 普通模型字符串，例如 `openai:gpt-5.4`
  - 或 OpenAI-compatible 自定义端点

## 快速开始

### 1. 配置后端环境变量

复制模板：

```bash
cp backend/.env.example backend/.env
```

关键说明：

- 后端只读取 `backend/.env`。
- 如果没有设置 `DATABASE_URL`，后端会回退到 `sqlite+pysqlite:///./data/backend.db`。
- 运行时系统提示词位于 [backend/prompts/deepagents-system-prompt.md](backend/prompts/deepagents-system-prompt.md)，不放在 `.env` 中。
- 如果同时设置了 `CUSTOM_API_KEY`、`CUSTOM_API_URL` 和 `CUSTOM_API_MODEL`，后端会改用该 OpenAI-compatible 端点，且 `CUSTOM_API_MODEL` 优先于 `DEEPAGENTS_MODEL`。

### 2. 安装后端依赖

```bash
cd backend
uv sync --group dev
```

### 3. 安装前端依赖

```bash
cd frontend
npm install
```

### 4. 启动后端

```bash
cd backend
uv run uvicorn app.main:app --reload
```

默认地址：

- API：`http://127.0.0.1:8000/api`
- 健康检查：`http://127.0.0.1:8000/health`
- OpenAPI：`http://127.0.0.1:8000/docs`

### 5. 启动前端

```bash
cd frontend
npm run dev
```

默认情况下前端通过 `/api` 访问后端；如有需要，可通过 `VITE_API_BASE_URL` 覆盖。

### 6. 登录

默认凭据来自 `backend/.env`：

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

## 配置说明

### 后端核心配置

`backend/.env` 中常用变量如下：

| 变量 | 说明 |
| --- | --- |
| `APP_NAME` | FastAPI 应用名 |
| `API_PREFIX` | API 前缀，默认 `/api` |
| `DATABASE_URL` | 完整数据库连接串 |
| `ADMIN_EMAIL` | 管理员邮箱，可选 |
| `ADMIN_USERNAME` | 管理员用户名 |
| `ADMIN_PASSWORD` | 管理员密码 |
| `ADMIN_TOKEN_SECRET` | JWT 签名密钥 |
| `ADMIN_TOKEN_EXPIRE_MINUTES` | Token 过期时间（分钟） |
| `CORS_ALLOWED_ORIGINS` | 允许的前端来源，逗号分隔 |
| `UPLOAD_STORAGE_DIR` | 上传目录 |
| `MAX_UPLOAD_SIZE_BYTES` | 单文件上传大小限制 |
| `DEEPAGENTS_MODEL` | 默认模型配置 |
| `DEEPAGENTS_AGENT_NAME` | 运行时 Agent 名称 |
| `DEEPAGENTS_DEBUG` | 是否开启 DeepAgents 调试 |
| `DEEPAGENTS_TOOL_SPECS` | 工具扩展入口 |
| `DEEPAGENTS_MIDDLEWARE_SPECS` | 中间件扩展入口 |
| `DEEPAGENTS_RUN_INPUT_HOOK_SPECS` | 可选的运行输入钩子入口 |
| `DEEPAGENTS_UPLOAD_HOOK_SPECS` | 可选的上传后处理钩子入口 |
| `DEEPAGENTS_BUILTIN_TOOLS` | 可选的 DeepAgents 内置工具 allowlist |
| `DEEPAGENTS_DISABLED_BUILTIN_TOOLS` | 可选的 DeepAgents 内置工具 blocklist |
| `DEEPAGENTS_SKILLS` | 技能目录 |
| `DEEPAGENTS_MEMORY` | 额外记忆 / 指导文件 |
| `DEEPAGENTS_SANDBOX_*` | 沙箱后端配置 |

### OpenAI-compatible 自定义模型配置

如果以下三个必填变量同时存在，后端会构造 `ChatOpenAI` 客户端，而不是直接把 `DEEPAGENTS_MODEL` 透传给运行时：

- `CUSTOM_API_KEY`
- `CUSTOM_API_URL`
- `CUSTOM_API_MODEL`

可选变量：

| 变量 | 说明 |
| --- | --- |
| `CUSTOM_API_TEMPERATURE` | 自定义模型温度。未设置或为空时，后端不会向 `ChatOpenAI` 传递 `temperature` 字段。 |
| `CUSTOM_API_DEFAULT_HEADERS` | 自定义端点请求头。推荐使用 JSON object 字符串格式。 |

推荐的 header 格式：

```dotenv
CUSTOM_API_DEFAULT_HEADERS={"HTTP-Referer":"https://app.example.com","X-Title":"open_deepagents"}
```

为兼容性考虑，也支持旧式的逗号分隔 `KEY=VALUE` 格式：

```dotenv
CUSTOM_API_DEFAULT_HEADERS=HTTP-Referer=https://app.example.com,X-Title=open_deepagents
```

`CUSTOM_API_URL` 在创建客户端前会被标准化为基础 API 地址：

- 如果以 `/chat/completions` 结尾，会去掉该后缀
- 否则如果尚未以 `/v1` 结尾，会自动补上 `/v1`

### 默认扩展配置

`backend/.env.example` 默认启用了以下示例扩展：

- `DEEPAGENTS_TOOL_SPECS=extensions/tools/__init__.py:TOOLS`
- `DEEPAGENTS_MIDDLEWARE_SPECS=extensions/middleware/__init__.py:MIDDLEWARE`
- `DEEPAGENTS_SKILLS=extensions/skills`
- `DEEPAGENTS_SANDBOX_KIND=state`
- `DEEPAGENTS_SANDBOX_ROOT_DIR=./data/sandbox`

后端会把 `DEEPAGENTS_SKILLS=extensions/skills` 规范化为 DeepAgents 可见的
`/extensions/skills/`，并把这个 source 路由到磁盘上的项目目录，因此即使主运行时
后端是 `state`，或者沙箱根目录位于 `backend/data/sandbox` 下，技能仍然可以被发现。

运行时钩子默认不强制启用。推荐从这个配置开始：

```dotenv
DEEPAGENTS_RUN_INPUT_HOOK_SPECS=extensions/runtime_hooks/__init__.py:RUN_INPUT_HOOKS
DEEPAGENTS_UPLOAD_HOOK_SPECS=extensions/runtime_hooks/__init__.py:UPLOAD_HOOKS
```

然后编辑 [backend/extensions/runtime_hooks](backend/extensions/runtime_hooks) 下的模板。
Run input hook 会拿到当前消息和已解析的附件元数据，可以替换传给 DeepAgents 的
content；upload hook 会在文件落盘后运行，可以把自定义元数据写入 `UploadRecord.extra`。

DeepAgents 内置工具也可以直接通过环境变量控制，不需要改源码：

```dotenv
# 只让这些内置工具对模型可见；自定义工具仍会透传。
DEEPAGENTS_BUILTIN_TOOLS=ls,read_file,grep,task

# 或者只隐藏少量内置工具。
DEEPAGENTS_DISABLED_BUILTIN_TOOLS=execute,write_file,edit_file
```

当前已知内置工具名包括 `write_todos`、`ls`、`read_file`、`write_file`、
`edit_file`、`glob`、`grep`、`execute` 和 `task`。

### 默认沙箱权限

默认沙箱权限不是只写在文档里，而是由代码强制注入：

- 操作权限：只读
- 允许读取的路径：
  - `backend/data`
  - `backend/extensions/skills`

这些权限路径会被标准化为 DeepAgents 可接受的绝对路径字符串，其中也包含对 Windows 盘符路径的斜杠规范化处理。

### 沙箱类型与路径模型

沙箱后端决定 DeepAgents 的文件类工具和 shell 类工具在哪里运行：

| 类型 | 适合场景 | 边界 |
| --- | --- | --- |
| `state` | 希望使用内存态 DeepAgents 工作区，不开放宿主机 shell。 | 不暴露真实项目目录，只通过路由后的技能/上传元数据提供访问线索。 |
| `filesystem` | 希望文件工具访问某个真实目录树。 | `DEEPAGENTS_SANDBOX_ROOT_DIR` 是文件系统根目录。 |
| `local_shell` | 明确需要 `execute` 在宿主机运行命令。 | 命令会在宿主机执行，只建议可信部署使用。 |
| `custom` | 需要自己实现 DeepAgents backend。 | 通过 `DEEPAGENTS_SANDBOX_BACKEND_SPEC=module_or_path:factory` 接入。 |

上传元数据里的路径字段：

- `storage_key`：数据库中记录的、相对于 `UPLOAD_STORAGE_DIR` 的路径。
- `upload_path`：宿主机绝对路径，适合后端有读权限时使用。
- `sandbox_path`：当上传文件位于 sandbox root 内时，给 DeepAgents 工具使用的路径。

如果 `DEEPAGENTS_SANDBOX_VIRTUAL_MODE=true`，sandbox 路径会带前导斜杠，例如
`/uploads/<session>/<file>`。如果关闭 virtual mode，则更接近普通文件系统路径。

## 后端 API 概览

### 管理员鉴权

- `POST /api/admin/login`
- `GET /api/admin/me`

### 会话

- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/{session_id}`
- `PATCH /api/sessions/{session_id}`
- `DELETE /api/sessions/{session_id}`

### 消息

- `GET /api/sessions/{session_id}/messages`
- `POST /api/sessions/{session_id}/messages`
- `GET /api/messages/{message_id}`
- `PATCH /api/messages/{message_id}`
- `DELETE /api/messages/{message_id}`

### 上传

- `GET /api/sessions/{session_id}/uploads`
- `POST /api/sessions/{session_id}/uploads`
- `POST /api/uploads`
- `GET /api/uploads/{upload_id}`
- `GET /api/uploads/{upload_id}/content`

### 运行

- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/stream`

## 扩展点

后端通过配置加载扩展，而不是把扩展硬编码进应用。

### 工具与中间件

- 统一工具入口：[backend/extensions/tools/__init__.py](backend/extensions/tools/__init__.py)
- 工具模板：[backend/extensions/tools/echo_tool.py](backend/extensions/tools/echo_tool.py)
- 统一中间件入口：[backend/extensions/middleware/__init__.py](backend/extensions/middleware/__init__.py)
- 中间件模板：[backend/extensions/middleware/audit_middleware.py](backend/extensions/middleware/audit_middleware.py)

入口格式：

```text
path/to/file.py:OBJECT_NAME
```

推荐模式：

- 在 `backend/extensions/tools/` 下新增工具模块
- 在 `backend/extensions/tools/__init__.py` 中统一导出 `TOOLS`
- 在 `backend/extensions/middleware/` 下新增中间件模块
- 在 `backend/extensions/middleware/__init__.py` 中统一导出 `MIDDLEWARE`

### 运行时钩子

- 模板目录：[backend/extensions/runtime_hooks](backend/extensions/runtime_hooks)
- 统一入口：[backend/extensions/runtime_hooks/__init__.py](backend/extensions/runtime_hooks/__init__.py)
- 示例实现：[backend/extensions/runtime_hooks/attachment_hooks.py](backend/extensions/runtime_hooks/attachment_hooks.py)

配置方式：

```dotenv
DEEPAGENTS_RUN_INPUT_HOOK_SPECS=extensions/runtime_hooks/__init__.py:RUN_INPUT_HOOKS
DEEPAGENTS_UPLOAD_HOOK_SPECS=extensions/runtime_hooks/__init__.py:UPLOAD_HOOKS
```

`RUN_INPUT_HOOKS` 函数会收到 `session_id`、`run_id`、`role`、`content`、
`attachments`、`is_current_run`。返回字符串或 `{"content": "..."}` 即可替换
消息内容；返回 `None` 表示不修改。

`UPLOAD_HOOKS` 函数会在文件保存后收到上传元数据和原始 payload。返回 dict 会合并到
上传记录的 `extra` 字段；返回 `None` 表示不修改。

### 技能

- 技能模板目录：[backend/extensions/skills](backend/extensions/skills)
- 通过 `DEEPAGENTS_SKILLS` 配置
- 每个技能都需要自己的子目录，并在其中放置 `SKILL.md`

结构示例：

```text
backend/extensions/skills/
  web-research/
    SKILL.md
    helper.py
```

### 记忆

- 通过 `DEEPAGENTS_MEMORY` 配置

### 沙箱

支持的沙箱类型：

- `state`
- `filesystem`
- `local_shell`
- `custom`

更多路径示例、上传文件可见性和安全说明见
[backend/extensions/sandboxes/README.md](backend/extensions/sandboxes/README.md)。

### 前端文案定制

常用用户可见文案集中在 [frontend/src/lib/copy.js](frontend/src/lib/copy.js)。
简单产品文案调整优先改这个文件；主要组件和 store 会从这里读取文案，避免到模板里逐个搜索。

## 开发与验证

后端检查通常在 `backend/` 目录执行。常用命令：

```bash
uv run pytest
uv run mypy app
uv run ruff check .
```
