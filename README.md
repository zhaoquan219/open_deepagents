# open_deepagents

`open_deepagents` 是一个 FastAPI + Vue 的 DeepAgents 应用脚手架，提供浏览器
聊天工作区、登录、会话历史、流式运行事件、模型选择、Mermaid 渲染，以及可定制
的递归 `backend/agents/` agent 包。

**简体中文** | [English](README_EN.md)

![DeepAgents web workspace screenshot](docs/images/workspace-zh.png)

## 当前包含

- 管理员登录，可配置多个用户。
- SQL `users` / `sessions` / `runs` / `uploads` / `events` 产品账本，按用户隔离会话。
- 单一 fetch-stream 运行接口：启动 DeepAgents run 并直接返回 SSE。
- Runtime timeline：展示 status、tool、subagent、sandbox、assistant message 等事件。
- Markdown 与 Mermaid 渲染。
- OpenAI-compatible provider/model catalog。
- 递归 agent 包：prompts、tools、middleware、skills、memory、原生文件权限、
  subagents。
- 原生 DeepAgents/LangGraph runtime wiring：`thread_id`、checkpointer、store、
  cache、backend。

当前原生后端有意保持运行面很小：sessions、有序 events、session 归属的上传文件、
以及一个 fetch-stream run 接口。上传文件信息会进入 runtime context；如果项目希望
把这些路径转成额外 model message，请在 agent middleware 中显式实现。

## 项目结构

```text
frontend/                 Vue 3 前端：登录、会话、聊天与流式事件时间线
backend/                  FastAPI API：认证、会话、事件账本与 DeepAgents
backend/app/runtime/      共享的 DeepAgents 沙箱、权限与 SSE 辅助模块
backend/agents/           后端加载的递归 agent 包
backend/models.json       前端模型选择器使用的模型目录
docs/                     使用指南与截图
packages/contracts/       前后端共享的 SSE 契约样例
tests/                    仓库级集成测试
verification/             脚手架与契约检查工具
```

运行流程：

1. 前端登录并保存 bearer token。
2. 用户选择或创建 session。
3. 前端请求 `POST /api/sessions/{session_id}/runs/stream`。
4. 后端写入 `run.started` 与 `user.message` 事件。
5. `backend/agents:AGENT` 被解析为 DeepAgents runtime 输入。
6. DeepAgents 通过 `graph.astream_events(...)` 流式返回事件。
7. 后端写入 SQL event ledger，并发送 UI SSE envelope。
8. 前端用 message event 更新 transcript，用 status/tool/runtime event 更新 timeline。

## 快速开始

### 1. 配置后端

```bash
cp backend/.env.example backend/.env
cp backend/models.example.json backend/models.json
```

编辑 `backend/models.json`，用 `${OPENAI_API_KEY}` 这类环境变量占位配置密钥。

### 2. 启动后端

```bash
cd backend
uv sync --group dev
uv run python -m app
```

推荐用 `python -m app` 启动：它会在 uvicorn 绑定端口之前设置 asyncio 事件循环策略，
这对 Windows 上使用 PostgreSQL 检查点/存储后端是必需的（psycopg 的异步模式无法运行在
Windows 默认的 `ProactorEventLoop` 上）。该入口支持 `BACKEND_HOST`、`BACKEND_PORT`、
`BACKEND_RELOAD`（默认开启热重载）。直接使用 `uv run uvicorn app.main:app --reload`
对 SQLite/内存检查点仍然可用，但在 Windows 上配合 PostgreSQL 检查点时会导致每个请求都卡住，
因此请改用 `python -m app`。

应用启动时会自动初始化 schema。默认地址：

- `http://127.0.0.1:8000/api`
- `http://127.0.0.1:8000/health`

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认地址：`http://127.0.0.1:5173`。

## 重要配置

| 配置 | 作用 |
| --- | --- |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | 默认登录用户。 |
| `ADMIN_USERS` | 额外用户，支持 JSON map 或 `username=password` 逗号列表。 |
| `ADMIN_TOKEN_SECRET` | JWT 签名密钥，生产环境请使用长随机值。 |
| `DATABASE_URL` | 产品账本 SQLAlchemy URL，支持 SQLite/PostgreSQL/MySQL。 |
| `DEEPAGENTS_MAIN_AGENT` | 主 agent import spec，默认 `agents:AGENT`。 |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | 模型配置文件，默认 `./models.json`。 |
| `DEEPAGENTS_SANDBOX_PROFILE` | 更直观的沙箱预设：`safe`、`files`、`shell`、`custom`。 |
| `DEEPAGENTS_SANDBOX_ROOT_DIR` | `files` 和 `shell` 沙箱使用的工作区根目录。 |
| `DEEPAGENTS_UPLOAD_ROOT_DIR` | 上传文件保存根目录，并只读挂载到 `/uploads`。 |
| `DEEPAGENTS_CHECKPOINT_BACKEND` | 默认 `sqlite`；支持 `memory`、`sqlite`、`postgresql`。 |
| `BACKEND_LOG_LEVEL` | 默认 `info`；`debug` 只打印简洁的 runtime 事件摘要。 |

推荐配置方式：

- 本地开发：复制 `.env.example`，修改管理员密码和 token secret，然后补好 `models.json`。
- 除非需要文件写入或可信本地 shell，否则保持 `DEEPAGENTS_SANDBOX_PROFILE=safe`。
- 生产部署：设置 `ENVIRONMENT=production`、强认证密钥，并使用
  `DEEPAGENTS_CHECKPOINT_BACKEND=sqlite` 或 `postgresql`。

产品状态和 LangGraph runtime 状态是分开的。`DATABASE_URL` 只保存产品表；
sqlite checkpoint 默认写入 `./data/checkpoints.db`；postgresql checkpoint 使用
`DEEPAGENTS_CHECKPOINT_DATABASE_URL`。

产品账本示例：

```dotenv
DATABASE_URL=sqlite+pysqlite:///./data/backend.db
DATABASE_URL=postgresql+psycopg://app:change-me@127.0.0.1:5432/open_deepagents
DATABASE_URL=mysql+pymysql://app:change-me@127.0.0.1:3306/open_deepagents?charset=utf8mb4
```

后端启动时会按需自动创建数据库，再初始化 `users`、`sessions`、`uploads`、
`runs`、`events` 等脚手架数据表。`DATABASE_URL` 只负责产品账本；LangGraph
checkpoint/store 持久化由 checkpoint 配置单独控制。

## API Contract

| Endpoint | 作用 |
| --- | --- |
| `POST /api/auth/login` | 登录并返回 bearer token。 |
| `GET /api/auth/me` | 返回当前用户。 |
| `GET /api/models` | 返回安全的模型选择元数据。 |
| `GET /api/sessions` | 列出当前用户的 sessions。 |
| `POST /api/sessions` | 创建 session。 |
| `PATCH /api/sessions/{session_id}` | 更新 title 或 metadata。 |
| `DELETE /api/sessions/{session_id}` | 归档自己的 session、从普通历史中隐藏，并撤销其上传文件。 |
| `GET /api/sessions/{session_id}/events?after_seq=N` | 读取持久化有序历史。 |
| `POST /api/sessions/{session_id}/uploads` | 保存 session 归属的上传记录，并返回 `/uploads/{session_id}/{filename}`。 |
| `POST /api/sessions/{session_id}/runs/stream` | 启动并流式返回一个 run。 |

客户端通过 `POST /api/runs/{run_id}/cancel` 停止运行，然后关闭当前 fetch stream。

## 模型目录

`backend/models.json` 控制界面模型选择器中显示的模型。

```json
{
  "model": "openai/gpt-5-4",
  "provider": {
    "openai": {
      "name": "OpenAI",
      "options": {
        "api_key": "${OPENAI_API_KEY}",
        "base_url": "https://api.openai.com/v1"
      },
      "models": {
        "gpt-5-4": {
          "name": "GPT-5.4",
          "model": "gpt-5.4"
        }
      }
    }
  }
}
```

Provider 的 `options` 与模型字段会传给 `ChatOpenAI`。密钥应保存在环境变量中；
公开的模型元数据不会包含密钥值。

## Agent Package

默认主 agent 是 `backend/agents:AGENT`。

```python
AGENT = {
    "id": "main",
    "system_prompt": ROOT / "prompts" / "system.md",
    "tools": TOOLS,
    "middleware": MIDDLEWARE,
    "skills": SKILLS,
    "memory": MEMORY,
    "permissions": [
        {
            "operations": ("read",),
            "paths": ["/workspace/main", "/skills", "/uploads"],
        },
        {"operations": ("write",), "paths": ["/workspace/main/output"]},
    ],
    "subagents": SUBAGENTS,
}
```

用原生 `permissions[].operations` 管理沙箱文件读写权限；内置工具可见性不再通过
agent 配置控制，文件工具能否访问某个路径由 `operations` 和虚拟路径共同决定。
现在 `tools`、`middleware`、`skills`、`memory`、`subagents` 都会相对于当前
agent package 解析，并支持用 `"*"` 发现该目录下的全部组件。

详见 [backend/agents/README.md](backend/agents/README.md)。

## 沙箱后端

| 类型 | 适用场景 | 说明 |
| --- | --- | --- |
| `safe` | 默认虚拟文件状态，以及只读的 `/skills` 和 `/uploads`。 | 不提供宿主机 shell。 |
| `files` | 在受控数据目录上使用文件工具，并提供只读挂载。 | 仅在确实需要写文件时使用。 |
| `shell` | 可信的本地命令执行。 | 命令直接运行在宿主机上。 |
| `custom` | 接入自定义 backend。 | 高级覆盖选项。 |

为不可信用户启用文件系统或 shell 访问前，请先阅读
[docs/sandbox.md](docs/sandbox.md)。

## 验证

后端：

```bash
cd backend
uv run ruff check .
uv run mypy app
uv run pytest
```

前端：

```bash
cd frontend
npm run check
```

仓库级检查：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest tests tests/backend
python verification/scaffold_audit.py
```
