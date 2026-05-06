# open_deepagents

`open_deepagents` 是一个 FastAPI + Vue 的 DeepAgents 应用脚手架，提供浏览器
聊天工作区、登录、会话历史、流式运行事件、模型选择、Mermaid 渲染，以及可定制
的递归 `backend/agents/` agent 包。

English documentation: [README.md](README.md)

![DeepAgents web workspace screenshot](docs/images/workspace-zh.png)

## 当前包含

- 管理员登录，可配置多个用户。
- SQL `users` / `sessions` / `events` 产品账本，按用户隔离会话。
- 单一 fetch-stream 运行接口：启动 DeepAgents run 并直接返回 SSE。
- Runtime timeline：展示 status、tool、subagent、sandbox、assistant message 等事件。
- Markdown 与 Mermaid 渲染。
- OpenAI-compatible provider/model catalog。
- 递归 agent 包：prompts、tools、middleware、skills、memory、permissions、
  built-in tool visibility、subagents。
- 原生 DeepAgents/LangGraph runtime wiring：`thread_id`、checkpointer、store、
  cache、backend。

当前原生后端有意保持运行面很小：sessions、有序 events、以及一个 fetch-stream
run 接口。upload/download、生成文件导出、拆分式 run manager、prompt-injection
service 都不属于这个脚手架。前端仍能展示历史消息里的 attachment 元数据，但当前
上传控件只是本地 pending attachment 占位。

## 工作流

1. 前端登录并保存 bearer token。
2. 用户选择或创建 session。
3. 前端请求 `POST /api/sessions/{session_id}/runs/stream`。
4. 后端写入 `run.started` 与 `user.message` 事件。
5. `backend/agents:AGENT` 被解析为 DeepAgents runtime 输入。
6. DeepAgents 通过 `graph.astream_events(...)` 流式返回事件。
7. 后端写入 SQL event ledger，并发送 UI SSE envelope。
8. 前端用 message event 更新 transcript，用 status/tool/runtime event 更新 timeline。

## 快速开始

```bash
cp backend/.env.example backend/.env
cp backend/models.example.json backend/models.json
```

编辑 `backend/models.json`，用 `${OPENAI_API_KEY}` 这类环境变量占位配置密钥。

启动后端：

```bash
cd backend
uv sync --group dev
uv run uvicorn app.main:app --reload
```

应用启动时会自动初始化 schema。默认地址：

- `http://127.0.0.1:8000/api`
- `http://127.0.0.1:8000/health`

启动前端：

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
| `DATABASE_URL` | 产品账本 SQLAlchemy URL，支持 SQLite/MySQL。 |
| `DEEPAGENTS_MAIN_AGENT` | 主 agent import spec，默认 `agents:AGENT`。 |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | 模型配置文件，默认 `./models.json`。 |
| `DEEPAGENTS_RUNTIME_DRIVER` | 更直观的 runtime 模式。本地用 `memory`，需要持久化时用内置数据库版 `durable`。 |
| `DEEPAGENTS_RUNTIME_FACTORY` | 可选高级用法：指向你们自己的 durable runtime bundle 工厂。 |
| `DEEPAGENTS_RUNTIME_PERSISTENCE` | 兼容保留的底层 runtime 模式。 |
| `DEEPAGENTS_RUNTIME_SPEC` | 兼容保留的底层 runtime 工厂入口。 |
| `DEEPAGENTS_SANDBOX_PROFILE` | 更直观的沙箱预设：`safe`、`files`、`shell`、`custom`。 |
| `DEEPAGENTS_SANDBOX_KIND` | `state`、`filesystem`、`local_shell` 或 `custom`。 |
| `DEEPAGENTS_SANDBOX_MAX_OUTPUT_BYTES` | `local_shell` 最大保留输出字节数。 |
| `DEEPAGENTS_SANDBOX_INHERIT_ENV` | `local_shell` 是否继承宿主机环境变量。 |
| `DEEPAGENTS_SANDBOX_ENV` | 传给 `local_shell` 的额外环境变量 JSON 对象。 |
| `DEEPAGENTS_BACKEND_SPEC` | 自定义 DeepAgents backend import spec。 |

推荐配置方式：

- 本地开发：设置 `DEEPAGENTS_RUNTIME_DRIVER=memory` 和 `DEEPAGENTS_SANDBOX_PROFILE=safe`。
- 标准持久化部署：设置 `ENVIRONMENT=production` 和 `DEEPAGENTS_RUNTIME_DRIVER=durable`。
- 高级覆盖：只有在你们想替换掉内置数据库版 runtime 时，才设置 `DEEPAGENTS_RUNTIME_FACTORY`。
- 旧的 `DEEPAGENTS_RUNTIME_PERSISTENCE`、`DEEPAGENTS_RUNTIME_SPEC` 以及低层 `*_SPEC` 仍保留为高级兼容入口。

内置 `durable` 会把 checkpoint/store 状态直接存进 `DATABASE_URL` 指向的数据库。
如果你们需要别的后端，再用 `DEEPAGENTS_RUNTIME_FACTORY` 覆盖。

MySQL 产品账本示例：

```dotenv
DATABASE_URL=mysql+pymysql://app:change-me@127.0.0.1:3306/open_deepagents?charset=utf8mb4
```

后端启动时会按需自动创建数据库，再初始化 `users`、`sessions`、`events`
三张表。这里的 MySQL 只负责产品账本；LangGraph 运行时持久化则由上面的 runtime
配置单独控制。

## API Contract

| Endpoint | 作用 |
| --- | --- |
| `POST /api/auth/login` | 登录并返回 bearer token。 |
| `GET /api/auth/me` | 返回当前用户。 |
| `GET /api/models` | 返回安全的模型选择元数据。 |
| `GET /api/sessions` | 列出当前用户的 sessions。 |
| `POST /api/sessions` | 创建 session。 |
| `PATCH /api/sessions/{session_id}` | 更新 title 或 metadata。 |
| `DELETE /api/sessions/{session_id}` | 删除自己的 session 与事件。 |
| `GET /api/sessions/{session_id}/events?after_seq=N` | 读取持久化有序历史。 |
| `POST /api/sessions/{session_id}/runs/stream` | 启动并流式返回一个 run。 |

没有拆分式 `/api/runs` 接口；停止运行通过前端 abort fetch stream 完成。

## Agent Package

默认主 agent 是 `backend/agents:AGENT`。

```python
AGENT = {
    "id": "main",
    "system_prompt": ROOT / "prompts" / "system.md",
    "tools": TOOLS,
    "builtin_tools": ("write_todos", "ls", "read_file", "glob", "grep", "task"),
    "disabled_builtin_tools": ("execute", "write_file", "edit_file"),
    "middleware": MIDDLEWARE,
    "skills": SKILLS,
    "memory": MEMORY,
    "subagents": SUBAGENTS,
}
```

开放文件工具时请配置 `permissions`。`workspace` 只是元数据，不是安全边界。
现在 `skills`、`memory`、`subagents` 都会相对于当前 agent package 解析，
所以像 `SKILLS = ["skill-creator"]` 这样的写法可以直接工作。

详见 [backend/agents/README.md](backend/agents/README.md)。

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
