# open_deepagents

`open_deepagents` 是一个用于实验和搭建 DeepAgents Web 工作台的全栈脚手架。

它当前包含两条真实主线：

- `backend/`：基于 FastAPI 的后端服务，负责鉴权、会话、消息、上传、运行流和 DeepAgents 集成
- `frontend/`：基于 Vue 3 + Vite 的操作台前端，负责会话管理、聊天界面、流式事件展示和运行时间线

这个仓库已经能完成本地端到端运行，但定位仍然是“可继续扩展的基础工程”，而不是开箱即用的成品平台。

## 项目包含什么

### 后端能力

- FastAPI 应用工厂与 `/health` 健康检查
- 单管理员 Bearer Token 鉴权
- 会话、消息、上传的 CRUD
- DeepAgents 运行创建、事件桥接与 SSE 流输出
- 本地文件上传存储
- 可配置的工具、中间件、技能、记忆、沙箱后端
- SQLite 本地默认配置，以及 MySQL 部署兼容

### 前端能力

- 管理员登录页
- 会话列表与切换
- 聊天工作区与附件上传
- Markdown 与 Mermaid 渲染
- 基于 SSE 的运行状态流
- 运行步骤、工具、技能、沙箱事件时间线

### 工程能力

- SSE 契约文件
- 仓库结构审计脚本
- 后端、前端、契约相关测试
- 扩展模板与架构说明文档

## 仓库结构

```text
.
├── backend/
│   ├── app/                          FastAPI 主应用
│   ├── deepagents_integration/       DeepAgents 运行时桥接层
│   ├── extensions/                   工具 / 中间件 / 技能 / 沙箱扩展模板
│   ├── prompts/                      项目内管理的系统提示词
│   └── tests/                        后端测试
├── frontend/
│   └── src/                          前端源码
├── packages/
│   ├── contracts/                    共享契约
│   └── extension-manifest.template.json
├── docs/                             架构与清理说明
├── tests/                            仓库级测试
└── verification/                     审计与契约校验
```

## 整体架构

当前请求链路如下：

1. 管理员在前端登录
2. 前端保存 Bearer Token，并调用后端 API
3. 用户创建或选择会话
4. 用户上传附件并提交 prompt
5. 后端创建 run，写入用户消息，并启动 DeepAgents 运行
6. 运行时事件被桥接为前端可消费的 SSE 信封格式
7. 前端消费流式事件，更新消息区和运行时间线

建议结合 [docs/repository-architecture.md](/Users/zhaoquan/AI_Coding/open_deepagents/docs/repository-architecture.md) 一起阅读，会更容易理解目录边界。

## 环境要求

- Python `3.11` 或 `3.12`
- [uv](https://docs.astral.sh/uv/) 用于后端依赖管理
- Node.js `18+`
- npm
- 一个可用的模型来源：
  - 常规模型字符串，例如 `openai:gpt-5.4`
  - 或 OpenAI-compatible 自定义端点

## 快速开始

### 1. 配置后端环境变量

复制模板：

```bash
cp backend/.env.example backend/.env
```

关键说明：

- 后端只读取 `backend/.env`
- 如果没有配置 `DATABASE_URL`，后端会回退为 `sqlite+pysqlite:///./data/backend.db`
- 系统提示词不在 `.env` 中，而是放在 [backend/prompts/deepagents-system-prompt.md](/Users/zhaoquan/AI_Coding/open_deepagents/backend/prompts/deepagents-system-prompt.md)
- 如果 `CUSTOM_API_KEY`、`CUSTOM_API_URL`、`CUSTOM_API_MODEL` 三项同时配置，后端会优先走自定义兼容端点，此时 `CUSTOM_API_MODEL` 优先于 `DEEPAGENTS_MODEL`

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

默认情况下，前端按 `/api` 访问后端。若需要覆盖，可配置 `VITE_API_BASE_URL`。

### 6. 登录

默认凭据来自 `backend/.env`：

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

## 环境变量说明

### 后端环境文件：`backend/.env`

常用配置如下：

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
| `UPLOAD_STORAGE_DIR` | 上传文件目录 |
| `MAX_UPLOAD_SIZE_BYTES` | 单文件上传大小限制 |
| `DEEPAGENTS_MODEL` | 默认模型配置 |
| `DEEPAGENTS_AGENT_NAME` | Agent 名称 |
| `DEEPAGENTS_DEBUG` | 是否开启调试 |
| `DEEPAGENTS_TOOL_SPECS` | 工具扩展入口 |
| `DEEPAGENTS_MIDDLEWARE_SPECS` | 中间件扩展入口 |
| `DEEPAGENTS_SKILLS` | 技能目录 |
| `DEEPAGENTS_MEMORY` | 额外记忆/指导文件 |
| `DEEPAGENTS_SANDBOX_*` | 沙箱相关配置 |
| `CUSTOM_API_*` | OpenAI-compatible 自定义端点配置 |

### 当前默认扩展配置

`backend/.env.example` 默认已经显式启用：

- `DEEPAGENTS_TOOL_SPECS=extensions/tools/echo_tool.py:TOOLS`
- `DEEPAGENTS_MIDDLEWARE_SPECS=extensions/middleware/audit_middleware.py:MIDDLEWARE`
- `DEEPAGENTS_SKILLS=extensions/skills`
- `DEEPAGENTS_SANDBOX_KIND=state`
- `DEEPAGENTS_SANDBOX_ROOT_DIR=./data/sandbox`

### 默认沙箱权限

默认沙箱权限是代码里强制注入的，而不是仅靠模板说明：

- 只允许 `read`
- 只允许读取：
  - [backend/data](/Users/zhaoquan/AI_Coding/open_deepagents/backend/data)
  - [backend/extensions/skills](/Users/zhaoquan/AI_Coding/open_deepagents/backend/extensions/skills)

也就是说，默认 baseline 是一个偏保守的只读沙箱。

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

后端运行时支持通过配置加载扩展，而不是硬编码。

### 工具与中间件

- 工具模板：[backend/extensions/tools/echo_tool.py](/Users/zhaoquan/AI_Coding/open_deepagents/backend/extensions/tools/echo_tool.py)
- 中间件模板：[backend/extensions/middleware/audit_middleware.py](/Users/zhaoquan/AI_Coding/open_deepagents/backend/extensions/middleware/audit_middleware.py)

路径格式为：

```text
path/to/file.py:OBJECT_NAME
```

### 技能

- 技能目录模板：[backend/extensions/skills](/Users/zhaoquan/AI_Coding/open_deepagents/backend/extensions/skills)
- 通过 `DEEPAGENTS_SKILLS` 配置

### 记忆

- 通过 `DEEPAGENTS_MEMORY` 配置额外记忆或指导文件路径

### 沙箱

支持的沙箱类型包括：

- `state`
- `filesystem`
- `local_shell`
- `custom`

如果使用自定义沙箱后端，可通过 `DEEPAGENTS_SANDBOX_BACKEND_SPEC` 指向工厂函数或对象。

### 扩展清单模板

[packages/extension-manifest.template.json](/Users/zhaoquan/AI_Coding/open_deepagents/packages/extension-manifest.template.json) 展示了一种统一描述工具、中间件、技能和沙箱扩展的写法。

## 契约与流式事件

当前有两个关键契约面：

- [packages/contracts/deepagents-sse-event-v1.json](/Users/zhaoquan/AI_Coding/open_deepagents/packages/contracts/deepagents-sse-event-v1.json)
  仓库级 SSE 契约
- 前端运行时会把事件标准化为：
  - `status`
  - `message.delta`
  - `message.final`
  - `step`
  - `tool`
  - `skill`
  - `sandbox`
  - `error`

如果你修改了流格式，最好同时更新：

- 契约文件
- 标准化逻辑
- 测试

## 常用开发命令

### 根目录

```bash
python -m unittest discover -s tests
python verification/scaffold_audit.py
```

### 后端

```bash
cd backend
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check .
uv run mypy .
```

### 前端

```bash
cd frontend
npm run dev
npm run test
npm run typecheck
npm run check
```

## 测试与验证

当前覆盖包括：

- 后端鉴权、会话、上传、运行生命周期测试
- DeepAgents 配置、扩展加载、SSE 桥接测试
- 契约校验测试
- 仓库结构审计测试

常用验证命令：

```bash
cd backend && uv run pytest
cd backend && uv run ruff check .
cd backend && uv run mypy .
cd frontend && npm run check
python -m unittest discover -s tests
python verification/scaffold_audit.py
```

## 当前限制

- 当前鉴权模型仍然比较简单，只支持单管理员
- 上传文件默认保存在本地文件系统
- 还没有数据库迁移体系
- 前端是操作台壳层，不是完整多角色产品
- 生产部署、反向代理、对象存储等仍需要你按场景补齐

## 建议阅读顺序

如果你第一次进入这个仓库，建议按这个顺序看：

1. 本 README
2. [docs/repository-architecture.md](/Users/zhaoquan/AI_Coding/open_deepagents/docs/repository-architecture.md)
3. [backend/app/main.py](/Users/zhaoquan/AI_Coding/open_deepagents/backend/app/main.py)
4. [frontend/src/App.vue](/Users/zhaoquan/AI_Coding/open_deepagents/frontend/src/App.vue)
5. [backend/app/services/runs.py](/Users/zhaoquan/AI_Coding/open_deepagents/backend/app/services/runs.py)
6. [frontend/src/api/client.js](/Users/zhaoquan/AI_Coding/open_deepagents/frontend/src/api/client.js)

这样能最快建立对真实运行路径的理解。
