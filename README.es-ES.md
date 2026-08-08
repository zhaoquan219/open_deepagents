

# open_deepagents

`open_deepagents` es un scaffold de FastAPI + Vue para construir aplicaciones de DeepAgents con un espacio de trabajo de chat en el navegador, sesiones autenticadas, eventos de tiempo de ejecución en streaming, un selector de modelos, renderizado de Mermaid y un paquete `backend/agents/` recursivo y personalizable.

Documentación en chino: [README_CH.md](README_CH.md)

![Captura de pantalla del espacio de trabajo web de DeepAgents](docs/images/workspace-en.png)

## Qué Obtendrás

- Inicio de sesión de administrador con configuración opcional de múltiples usuarios.
- Sesiones por usuario respaldadas por un registro SQL `users` / `sessions` / `uploads` / `events`.
- Un único endpoint de ejecución con fetch-stream que inicia una ejecución de DeepAgents y transmite eventos de la UI.
- Línea de tiempo de tiempo de ejecución para actividad de estado, herramientas, subagentes, sandbox y mensajes del asistente.
- Renderizado de Markdown y Mermaid en los mensajes del asistente.
- Configuración del catálogo de modelos para proveedores compatibles con OpenAI.
- Soporte para paquetes de agentes recursivos que incluyen prompts, herramientas, middleware, habilidades, memoria, permisos nativos del sistema de archivos y subagentes.
- Conexión nativa de tiempo de ejecución de DeepAgents/LangGraph para `thread_id`, checkpointer, store, cache y selección de backend.

El backend nativo actual mantiene la superficie de ejecución intencionalmente pequeña:
sesiones, eventos ordenados, cargas propiedad de la sesión y un único endpoint de ejecución con fetch-stream.
Los metadatos de carga se pasan a través del contexto de tiempo de ejecución; si un proyecto desea convertir esas rutas en un mensaje adicional del modelo, lo hace explícitamente en el middleware del agente.

## Estructura del Proyecto

```text
frontend/                 Vue 3 UI: login, sessions, chat, stream timeline
backend/                  FastAPI API: auth, sessions, event ledger, DeepAgents
backend/app/runtime/      Shared DeepAgents sandbox, permission, and SSE helpers
backend/agents/           Recursive agent package loaded by the backend
backend/models.json       Model catalog shown in the UI model selector
docs/                     User guides and screenshots
packages/contracts/       Shared UI SSE contract fixtures
tests/                    Repository-level integration tests
verification/             Scaffold and contract audit helpers
```

Flujo de ejecución:

1. El frontend inicia sesión y almacena un token bearer.
2. El usuario selecciona o crea una sesión.
3. El frontend envía un prompt a `POST /api/sessions/{session_id}/runs/stream`.
4. El backend persiste los eventos `run.started` y `user.message`.
5. `` `backend/agents:AGENT` `` se resuelve en entradas de tiempo de ejecución de DeepAgents.
6. DeepAgents transmite eventos de tiempo de ejecución a través de `graph.astream_events(...)`.
7. El backend almacena filas de eventos normalizados y emite paquetes SSE.
8. El frontend actualiza la transcripción a partir de eventos de mensaje y la línea de tiempo a partir de eventos de estado/herramientas/tiempo de ejecución.

## Inicio Rápido

### 1. Configurar el backend

```bash
cp backend/.env.example backend/.env
cp backend/models.example.json backend/models.json
```

Edita `backend/models.json` y proporciona las credenciales mediante marcadores de posición de entorno como `${OPENAI_API_KEY}`.

Configuraciones importantes de `.env`:

| Configuración | Propósito |
| --- | --- |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Credenciales de inicio de sesión por defecto. |
| `ADMIN_USERS` | Mapa JSON opcional o pares `username=password` separados por comas. |
| `ADMIN_TOKEN_SECRET` | Secreto para firmar JWT. Usa un valor largo y aleatorio fuera del desarrollo local. |
| `DATABASE_URL` | URL de SQLAlchemy para SQLite, PostgreSQL o MySQL para el registro del producto. |
| `DEEPAGENTS_MAIN_AGENT` | Especificación de importación del agente principal. Por defecto: `agents:AGENT`. |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | Ruta del catálogo de modelos. Por defecto: `./models.json`. |
| `DEEPAGENTS_SANDBOX_PROFILE` | Preset de sandbox de alto nivel: `safe`, `files`, `shell` o `custom`. |
| `DEEPAGENTS_SANDBOX_ROOT_DIR` | Directorio raíz del espacio de trabajo para los perfiles de sandbox `files` y `shell`. |
| `DEEPAGENTS_UPLOAD_ROOT_DIR` | Raíz de almacenamiento montada en modo de solo lectura en `/uploads`. |
| `DEEPAGENTS_CHECKPOINT_BACKEND` | `sqlite` por defecto;  soporta `memory`, `sqlite` y `postgresql`. |
| `BACKEND_LOG_LEVEL` | `info` por defecto; `debug` registra resúmenes concisos de eventos de tiempo de ejecución. |

Configuración recomendada:

- Desarrollo local: copia `.env.example`, cambia la contraseña y el secreto del token de administrador, y luego completa `models.json`.
- Mantén `DEEPAGENTS_SANDBOX_PROFILE=safe` a menos que necesites escrituras en el sistema de archivos o un shell local de confianza.
- Despliegue en producción: establece `ENVIRONMENT=production`, secretos de autenticación fuertes y `DEEPAGENTS_CHECKPOINT_BACKEND=sqlite` o `postgresql`.

El estado del producto y el estado de tiempo de ejecución están separados deliberadamente. `DATABASE_URL` almacena las tablas del producto (`users`, `sessions`, `runs`, `uploads`, `events`); el estado de checkpoint de sqlite por defecto es `./data/checkpoints.db`, mientras que el estado de checkpoint de postgresql utiliza `DEEPAGENTS_CHECKPOINT_DATABASE_URL`.

Ejemplos del registro del producto:

```dotenv
DATABASE_URL=sqlite+pysqlite:///./data/backend.db
DATABASE_URL=postgresql+psycopg://app:change-me@127.0.0.1:5432/open_deepagents
DATABASE_URL=mysql+pymysql://app:change-me@127.0.0.1:3306/open_deepagents?charset=utf8mb4
```

El backend crea la base de datos si es necesario, y luego inicializa las tablas del scaffold. `DATABASE_URL` almacena usuarios, sesiones, cargas, ejecuciones y eventos; la persistencia de checkpoint/store de LangGraph se configura por separado a través de los ajustes de checkpoint anteriores.

### 2. Iniciar el backend

```bash
cd backend
uv sync --group dev
uv run python -m app
```

`` `python -m app` `` es el punto de entrada recomendado. Establece la política del bucle de eventos de asyncio antes de que uvicorn enlace su socket, lo cual es necesario para el backend de checkpoint/store de Postgres en Windows (el modo asíncrono de psycopg no puede ejecutarse en el `ProactorEventLoop` por defecto). Respeta `BACKEND_HOST`, `BACKEND_PORT` y `BACKEND_RELOAD` (la recarga está activada por defecto). El comando `` `uv run uvicorn app.main:app --reload` `` funciona para checkpoints de SQLite/en memoria, pero en Windows con checkpoints de Postgres se colgará en cada solicitud, por lo que se prefiere `python -m app`.

La aplicación inicializa el esquema al inicio. Las API por defecto son:

- `http://127.0.0.1:8000/api`
- `http://127.0.0.1:8000/health`

### 3. Iniciar el frontend

```bash
cd frontend
npm install
npm run dev
```

El servidor de desarrollo del frontend usa por defecto `http://127.0.0.1:5173`.

## Contrato de API

| Endpoint | Propósito |
| --- | --- |
| `POST /api/auth/login` | Iniciar sesión y recibir un token bearer. |
| `GET /api/auth/me` | Devolver el usuario actual. |
| `GET /api/models` | Devolver metadatos seguros del selector de modelos. |
| `GET /api/sessions` | Listar las sesiones del usuario actual. |
| `POST /api/sessions` | Crear una sesión. |
| `PATCH /api/sessions/{session_id}` | Actualizar título o metadatos. |
| `DELETE /api/sessions/{session_id}` | Archivar una sesión propia, ocultar el historial normal y revocar las cargas. |
| `GET /api/sessions/{session_id}/events?after_seq=N` | Cargar historial ordenado persistente. |
| `POST /api/sessions/{session_id}/uploads` | Almacenar una fila de carga propia de la sesión y devolver `/uploads/{session_id}/{filename}`. |
| `POST /api/sessions/{session_id}/runs/stream` | Iniciar y transmitir una ejecución. |

El cliente detiene una ejecución a través de `POST /api/runs/{run_id}/cancel`, y luego cierra el stream de fetch activo.

## Catálogo de Modelos

`backend/models.json` controla el selector de modelos que se muestra en la UI.

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

Las opciones de proveedor `options` y los campos de modelo se pasan a `ChatOpenAI`. Mantén los secretos en variables de entorno; los metadatos públicos de los modelos omiten los valores secretos.

## Paquete de Agentes

El agente principal por defecto es `backend/agents:AGENT`.

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

Usa `permissions[].operations` nativo para controlar el acceso al sistema de archivos. La visibilidad de las herramientas integradas ya no se configura a través del mapeo del agente; las herramientas de archivo se permiten o deniegan según la operación y la ruta virtual.
Los `tools`, `middleware`, `skills`, `memory` y `subagents` locales al paquete se resuelven en relación con el paquete de agente actual. Los selectores pueden ser listas explícitas o `"*"` para descubrir todas las exportaciones de componentes en esa carpeta de paquete.

Consulta [backend/agents/README.md](backend/agents/README.md) para ejemplos de paquetes.

## Backends de Sandbox

| Tipo | Mejor para | Notas |
| --- | --- | --- |
| `safe` | Estado de archivo virtual por defecto más `/skills` y `/uploads` de solo lectura. | Sin shell del host. |
| `files` | Herramientas de archivo sobre un directorio de datos controlado más montajes de solo lectura. | Usa solo cuando se necesiten escrituras en archivos. |
| `shell` | Ejecución de comandos locales de confianza. | Ejecuta comandos en el host. |
| `custom` | Trae tu propio backend. | Sobrescritura avanzada. |

Lee [docs/sandbox.md](docs/sandbox.md) antes de habilitar el acceso al sistema de archivos o al shell para usuarios no confiables.

## Verificación

Backend:

```bash
cd backend
uv run ruff check .
uv run mypy app
uv run pytest
```

Frontend:

```bash
cd frontend
npm run check
```

Verificaciones a nivel de repositorio:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest tests tests/backend
python verification/scaffold_audit.py
```
