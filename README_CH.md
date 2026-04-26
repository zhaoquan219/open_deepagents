# open_deepagents

`open_deepagents` 是一个可直接运行、面向真实用户的开源智能体应用框架。它包含
FastAPI 后端、Vue 聊天界面、持久化会话、文件上传和生成文件下载、流式运行事件、
模型选择、Mermaid 渲染、sandbox 控制，以及一个可递归扩展的
`backend/agents/` agent 包。

English documentation: [README.md](README.md)

![DeepAgents 工作台截图](docs/images/workspace-zh.png)

## 功能一览

- 管理员登录，可配置多个用户。
- 会话历史和消息持久化。
- 文件上传，附件信息会进入 agent run。
- SSE 流式回复。
- 运行时间线展示 run、tool、skill、sandbox、subagent 状态。
- Markdown 和 Mermaid 渲染。
- 浏览器支持图片剪贴板时，可复制 Mermaid PNG 图片。
- `models.json` 模型目录，支持 OpenAI-compatible provider。
- `backend/agents/` 递归 agent 包，可配置提示词、工具、中间件、钩子、技能、
  记忆和 subagent。
- 支持 `state`、`filesystem`、`local_shell`、`custom` sandbox backend。

## 架构一眼看懂

```text
frontend/                 Vue 3 UI：登录、会话、聊天、上传、运行时间线
backend/                  FastAPI API：认证、持久化、上传、运行编排
backend/agents/           后端加载的递归 agent 包
backend/models.json       前端模型选择器读取的模型目录
docs/                     用户指南和截图
packages/contracts/       共享事件契约样例
tests/                    仓库级后端集成测试
verification/             脚手架和契约审计工具
```

一次运行的大致流程：

1. 前端登录并保存 API token。
2. 用户选择或创建会话。
3. 用户上传文件并发送消息。
4. 后端保存用户消息并启动 DeepAgents run。
5. `backend/agents:AGENT` 解析出提示词、工具、中间件、钩子、技能、记忆、
   permissions 和 subagents。
6. 后端把历史消息、附件、hooks 和运行配置组装为 LLM 输入。
7. 运行事件转换为前端 SSE 事件并推送到浏览器。
8. 前端更新聊天记录和运行时间线。
9. `state` backend 生成的文件会导出为上传记录，并挂到最终助手回复上。

## 快速开始

### 1. 配置后端

```bash
cp backend/.env.example backend/.env
cp backend/models.example.json backend/models.json
```

编辑 `backend/models.json`，通过 `${OPENAI_API_KEY}` 这类环境变量占位配置真实
凭据。应用配置读取 `backend/.env`。

常用 `.env` 配置：

| 配置 | 用途 |
| --- | --- |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | 默认登录账号。 |
| `ADMIN_USERS` | 多用户 JSON 映射。 |
| `UPLOAD_STORAGE_DIR` | 上传文件和生成文件存储目录。 |
| `DEEPAGENTS_MAIN_AGENT` | 主 agent import spec，默认 `agents:AGENT`。 |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | 模型目录路径，默认 `./models.json`。 |
| `DEEPAGENTS_DEFAULT_TIMEZONE` | 后端持久化时间和认证/会话事件使用的时区。 |
| `DEEPAGENTS_STREAM_IDLE_TIMEOUT` | runtime/model/tool 长时间无事件时自动失败的秒数，防止界面永久卡住；设为 `0` 可关闭。 |
| `DEEPAGENTS_SANDBOX_KIND` | `state`、`filesystem`、`local_shell` 或 `custom`。 |
| `DEEPAGENTS_SANDBOX_ROOT_DIR` | filesystem/local-shell 文件工具的根目录。 |
| `DEEPAGENTS_SANDBOX_BACKEND_SPEC` | 自定义 backend factory 的 import spec。 |

### 2. 启动后端

```bash
cd backend
uv sync --group dev
uv run python -m app.db.manage init
uv run uvicorn app.main:app --reload
```

默认地址：

- `http://127.0.0.1:8000/api`
- `http://127.0.0.1:8000/health`

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认地址是 `http://127.0.0.1:5173`。

## 模型目录

`backend/models.json` 控制前端输入框里的模型选择器。

示例：

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
          "model": "gpt-5.4",
          "temperature": null,
          "extra_body": {}
        }
      }
    }
  }
}
```

`provider.options` 和模型字段会传给 `ChatOpenAI`。每个模型条目的参数保持扁平。

## Agent 包

默认主 agent 是 `backend/agents:AGENT`。

```python
AGENT = {
    "id": "main",
    "system_prompt": ROOT / "prompts" / "system.md",
    "workspace": "/workspace/main",
    "tools": TOOLS,
    "builtin_tools": ("write_todos", "ls", "read_file", "glob", "grep", "task"),
    "disabled_builtin_tools": ("execute", "write_file", "edit_file"),
    "middleware": MIDDLEWARE,
    "hooks": {"run_input": RUN_INPUT_HOOKS, "upload": UPLOAD_HOOKS},
    "skills": SKILLS,
    "memory": MEMORY,
    "subagents": SUBAGENTS,
}
```

这是推荐的最小配置。`model`、`workspace`、`permissions`、内联 `subagents`
这些字段，都建议按需再加，不要默认全堆进去。

Subagent 支持同样的字段，也可以配置自己的 `model`、`workspace`、工具、内置工具
过滤、permissions、技能、记忆和嵌套 subagents。
`workspace` 只是 UI、日志和 agent 编写时的描述性元数据，不是文件系统或工具访问
边界；真正的访问控制来自 sandbox backend 选择和 `permissions`。

常改文件：

- 主提示词：[backend/agents/prompts/system.md](backend/agents/prompts/system.md)
- 工具：[backend/agents/tools](backend/agents/tools)
- 中间件：[backend/agents/middleware](backend/agents/middleware)
- 钩子：[backend/agents/hooks](backend/agents/hooks)
- 技能：[backend/agents/skills](backend/agents/skills)
- 记忆：[backend/agents/memory](backend/agents/memory)
- Subagents：[backend/agents/subagents](backend/agents/subagents)

Agent 包示例见 [backend/agents/README.md](backend/agents/README.md)。

## 工具可见性和权限

内置工具和文件权限是两层控制：

- `builtin_tools` / `disabled_builtin_tools` 决定模型能看到哪些 DeepAgents 内置工具名。
- `permissions` 决定可见文件工具被调用后，实际能操作哪些路径。
- 同一个内置工具如果同时出现在两个列表里，`disabled_builtin_tools` 优先。
- 如果当前没有启用 subagents，后端会自动隐藏内置 `task` 工具。

默认脚手架现在直接用显式 `skills` 和 `subagents` 列表。

`operations=["read"]` 覆盖 `ls`、`read_file`、`glob`、`grep`。
`operations=["write"]` 覆盖 `write_file`、`edit_file`。

示例：

```python
"builtin_tools": ("ls", "read_file", "glob", "grep", "task"),
"disabled_builtin_tools": ("execute", "write_file", "edit_file"),
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main"]},
],
```

## 上传与生成文件

- 上传文件存储在 `UPLOAD_STORAGE_DIR`。
- 上传元数据会传给 run-input hook。
- `state` sandbox 会把上传文件复制为虚拟 `/uploads/...` 文件。
- Agent 在 `state` backend 里新增或修改的文件，会在 run 完成后导出。
- 导出的文件会作为助手回复附件展示和下载。

## Prompt Injection

提示词注入功能还在，没有删。实现现在放在
`backend/app/core/session_scope.py` 的 `PromptInjectionService` 里。

后端代码或 hooks 里这样用：

```python
app.state.prompt_injections.inject_prompt(
    session_id=session_id,
    content="回答前先检查上传文件。",
    visibility="hidden",
    position="before_user",
    source="backend.rule",
)
```

说明：

- `hidden` 注入会影响 runtime 输入，但不会出现在默认 transcript 里
- `visible` 注入会持久化为 injection 记录，但公开 transcript 仍然保持干净
- 传 `run_id=...` 时，只对那个 run 生效
- 公共 message/run API 故意不接受隐藏注入元数据

## Runtime 状态

每个 Web 会话都会拥有后端内部维护的稳定 LangGraph thread id。应用数据库作为
产品账本，负责 sessions、messages、uploads 和可回放的 UI event views。

## Sandbox

| 类型 | 适合场景 | 说明 |
| --- | --- | --- |
| `state` | 默认安全虚拟文件状态。 | 不暴露主机 shell，完成后导出生成文件。 |
| `filesystem` | 文件工具需要访问受控目录。 | 使用以 `DEEPAGENTS_SANDBOX_ROOT_DIR` 为根的虚拟路径。 |
| `local_shell` | 可信本地命令执行。 | 会执行主机命令，需要严格限制工具和用户。 |
| `custom` | 自定义 backend。 | 设置 `DEEPAGENTS_SANDBOX_BACKEND_SPEC`。 |

给非可信用户开放 filesystem 或 shell 前，请先阅读 [docs/sandbox.md](docs/sandbox.md)。

## 前端行为

- 用户停在底部时，会话会跟随最新输出。
- 用户向上滚动后，流式输出不会再强制拉到底部。
- 用户发送新消息时，会话会跳到底部。
- Runtime timeline 会批量处理并限制条目数量，长时间工具调用也保持响应。
- Mermaid 代码块会渲染成图。浏览器支持图片剪贴板时，可复制 PNG 图片。

## 验证

后端：

```bash
cd backend
uv run ruff check .
uv run pytest
uv run mypy app/core/config.py app/core/runtime_catalog.py app/services/runs.py deepagents_integration
```

前端：

```bash
cd frontend
npm run check
```

仓库级集成测试：

```bash
cd backend
uv run pytest ../tests/backend/test_deepagents_integration.py
```
