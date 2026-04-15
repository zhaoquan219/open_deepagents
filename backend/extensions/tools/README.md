# Tool Extensions

Tools in this directory are loaded by the backend through:

```dotenv
DEEPAGENTS_TOOL_SPECS=extensions.tools:TOOLS
```

Add project tools as Python modules, decorate each callable with
`langchain_core.tools.tool`, and export the aggregated list from
`extensions.tools:TOOLS`.

## Example

```python
from langchain_core.tools import tool


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order by ID."""

    return f"order={order_id}"


TOOLS = [lookup_order]
```

Then add the module's tools to `backend/extensions/tools/__init__.py`:

```python
from extensions.tools.order_tools import TOOLS as ORDER_TOOLS

TOOLS = [*ORDER_TOOLS]
```

## Built-In Tools

DeepAgents also provides built-in tools. The backend can allowlist or blocklist
them without changing code:

```dotenv
DEEPAGENTS_BUILTIN_TOOLS=write_todos,ls,read_file,glob,grep,task
DEEPAGENTS_DISABLED_BUILTIN_TOOLS=execute,write_file,edit_file
```

Known built-in names are `write_todos`, `ls`, `read_file`, `write_file`,
`edit_file`, `glob`, `grep`, `execute`, and `task`.

Custom tools exported from `TOOLS` are not removed by the built-in filter.
