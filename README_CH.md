# open_deepagents

`open_deepagents` 是一个 FastAPI + Vue 的 DeepAgents 应用脚手架，提供浏览器
聊天工作区、登录、会话历史、流式运行事件、模型选择、Mermaid 渲染，以及可定制
的递归 `backend/agents/` agent 包。

English documentation: [README.md](README.md)

![DeepAgents web workspace screenshot](docs/images/workspace-zh.png)

## 当前包含

- 管理员登录，可配置多个用户。
- SQL `users` / `sessions` / `uploads` / `events` 产品账本，按用户隔离会话。
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

## 工作流

架构上，`backend/app/runtime/` 是仍在使用的运行时辅助层，负责 DeepAgents
sandbox、permissions、内置工具过滤与 SSE 归一化；路由层不要重复实现这些逻辑。

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

后端启动时会按需自动创建数据库，再初始化 `users`、`sessions`、`runs`、`events`
四张表。`DATABASE_URL` 只负责产品账本；LangGraph checkpoint/store 持久化由
checkpoint 配置单独控制。

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
